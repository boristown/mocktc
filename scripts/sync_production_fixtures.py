#!/usr/bin/env python3
"""Safely compare or explicitly mirror MockTC external BOM fixtures.

The command is deliberately *not* an automatic data replication mechanism.
Without ``--apply`` it only reads the source API and target directory, writes
nothing, and prints a JSON comparison report.  An apply requires both an
explicit fixture name and the SHA-256 of the currently installed target file,
which prevents an operator from silently overwriting a newer local change.

Only external JSON fixtures are in scope.  The SQLite standard BOM is excluded
because the public Excel export is not a transactional import format.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_SOURCE_URL = "https://mocktc.bjlzc.cn"
FIXTURE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
ABSENT_SHA = "absent"
MAX_SOURCE_BYTES = 8 * 1024 * 1024


class SyncError(RuntimeError):
    """Raised for a safe, human-actionable refusal."""


def fixture_name(value: str) -> str:
    name = str(value or "").strip()
    if not FIXTURE_RE.fullmatch(name) or ".." in name:
        raise SyncError("invalid fixture name: %r" % name)
    return name


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_rows(raw: bytes, label: str) -> int:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SyncError("%s is not valid UTF-8 JSON: %s" % (label, exc))
    if not isinstance(value, list):
        raise SyncError("%s must be a JSON array" % label)
    return len(value)


def get_bytes(url: str, token_env: str = "") -> bytes:
    headers = {"Accept": "application/json"}
    if token_env:
        token = os.environ.get(token_env, "").strip()
        if token:
            headers["Authorization"] = "Bearer " + token
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(MAX_SOURCE_BYTES + 1)
    except HTTPError as exc:
        raise SyncError("source HTTP %d for %s" % (exc.code, url))
    except URLError as exc:
        raise SyncError("source unavailable for %s: %s" % (url, exc.reason))
    if len(raw) > MAX_SOURCE_BYTES:
        raise SyncError("source response exceeds %d bytes" % MAX_SOURCE_BYTES)
    return raw


def source_fixtures(base_url: str, token_env: str) -> dict[str, dict[str, Any]]:
    base = base_url.rstrip("/")
    try:
        response = json.loads(get_bytes(base + "/tc/v1/fixtures", token_env).decode("utf-8"))
        fixtures = response["data"]["fixtures"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise SyncError("source fixture manifest has unexpected shape: %s" % exc)
    if not isinstance(fixtures, list):
        raise SyncError("source fixture manifest is not a list")
    result: dict[str, dict[str, Any]] = {}
    for item in fixtures:
        if not isinstance(item, dict):
            raise SyncError("source fixture manifest contains an invalid entry")
        name = fixture_name(str(item.get("name") or ""))
        if name in result:
            raise SyncError("source fixture manifest contains duplicate %s" % name)
        raw = get_bytes(base + "/tc/v1/fixtures/" + name + "?raw=1", token_env)
        result[name] = {
            "raw": raw,
            "sha256": sha256_bytes(raw),
            "rows": json_rows(raw, "source fixture " + name),
        }
    return result


def target_entry(target_dir: Path, name: str) -> dict[str, Any]:
    path = target_dir / name
    if not path.exists():
        return {"exists": False, "sha256": ABSENT_SHA, "rows": None}
    if not path.is_file() or path.is_symlink():
        raise SyncError("target fixture is not a regular file: %s" % path)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise SyncError("target fixture exceeds %d bytes: %s" % (MAX_SOURCE_BYTES, path))
    raw = path.read_bytes()
    return {"exists": True, "sha256": sha256_bytes(raw),
            "rows": json_rows(raw, "target fixture " + name)}


def report(source: dict[str, dict[str, Any]], target_dir: Path) -> dict[str, Any]:
    target_names = set()
    if target_dir.exists():
        if not target_dir.is_dir():
            raise SyncError("target fixture path is not a directory: %s" % target_dir)
        target_names = {entry.name for entry in target_dir.iterdir()
                        if entry.is_file() and FIXTURE_RE.fullmatch(entry.name)
                        and ".." not in entry.name}
    names = sorted(set(source) | target_names)
    fixtures = []
    for name in names:
        current = target_entry(target_dir, name)
        origin = source.get(name)
        state = "missing_source" if origin is None else (
            "missing_target" if not current["exists"] else
            "equal" if current["sha256"] == origin["sha256"] else "different")
        fixtures.append({
            "name": name, "state": state,
            "source_sha256": origin["sha256"] if origin else None,
            "source_rows": origin["rows"] if origin else None,
            "target_sha256": current["sha256"], "target_rows": current["rows"],
        })
    return {"mode": "check", "source_count": len(source),
            "target_count": len(target_names), "fixtures": fixtures}


def expected_map(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        name, separator, digest = str(value).partition("=")
        name = fixture_name(name)
        digest = digest.lower().strip()
        if not separator or (digest != ABSENT_SHA and not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise SyncError("--expect-target-sha must be NAME=SHA256 or NAME=absent")
        if name in result:
            raise SyncError("duplicate expected target hash for %s" % name)
        result[name] = digest
    return result


def atomic_replace(target_dir: Path, name: str, raw: bytes, old: dict[str, Any]) -> dict[str, str]:
    target = target_dir / name
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    history_dir = target_dir / ".history"
    history_dir.mkdir(mode=0o700, exist_ok=True)
    backup_name = ""
    if old["exists"]:
        backup_name = "%s.%s.%s.bak" % (name, stamp, old["sha256"][:12])
        backup = history_dir / backup_name
        shutil.copy2(target, backup)
        with backup.open("rb") as handle:
            os.fsync(handle.fileno())
        os.chmod(backup, 0o600)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".%s.sync-" % name, dir=str(target_dir))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, target)
        directory_fd = os.open(str(target_dir), os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return {"backup": backup_name, "target_sha256": sha256_bytes(raw)}


def apply(source: dict[str, dict[str, Any]], source_url: str, target_dir: Path,
          names: list[str], expected: dict[str, str]) -> dict[str, Any]:
    if not target_dir.is_dir():
        raise SyncError("target fixture directory does not exist: %s" % target_dir)
    requested = [fixture_name(name) for name in names]
    if not requested:
        raise SyncError("--apply requires at least one --fixture")
    if len(requested) != len(set(requested)):
        raise SyncError("duplicate --fixture")
    if len(requested) != 1:
        raise SyncError("--apply accepts exactly one --fixture per execution")
    if set(requested) != set(expected):
        raise SyncError("--apply requires exactly one --expect-target-sha for every --fixture")
    missing = sorted(set(requested) - set(source))
    if missing:
        raise SyncError("requested fixture absent from source: %s" % ",".join(missing))
    lock_path = target_dir / ".mocktc-fixtures.lock"
    results = []
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            for name in requested:
                current = target_entry(target_dir, name)
                if current["sha256"] != expected[name]:
                    raise SyncError("target SHA changed for %s: expected %s, got %s" %
                                    (name, expected[name], current["sha256"]))
            for name in requested:
                current = target_entry(target_dir, name)
                outcome = atomic_replace(target_dir, name, source[name]["raw"], current)
                results.append({"name": name, "source_sha256": source[name]["sha256"],
                                "source_rows": source[name]["rows"],
                                "previous_sha256": current["sha256"], **outcome})
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    audit_dir = target_dir / ".sync-history"
    audit_dir.mkdir(mode=0o700, exist_ok=True)
    audit_name = "production-fixture-sync-%s.json" % dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    audit_path = audit_dir / audit_name
    audit = {"schema": 1, "mode": "apply", "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "source": source_url.rstrip("/"), "fixtures": results}
    temporary = audit_dir / (".%s.tmp" % audit_name)
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(audit, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, audit_path)
    return {"mode": "apply", "fixtures": results, "audit_manifest": str(audit_path)}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL,
                        help="production MockTC base URL (default: %(default)s)")
    parser.add_argument("--source-token-env", default="",
                        help="optional environment variable carrying a source API token")
    parser.add_argument("--target-dir", type=Path, required=True,
                        help="existing Kylin MockTC fixture directory")
    parser.add_argument("--apply", action="store_true", help="perform explicit fixture replacement")
    parser.add_argument("--fixture", action="append", default=[],
                        help="fixture to apply; repeatable and mandatory with --apply")
    parser.add_argument("--expect-target-sha", action="append", default=[], metavar="NAME=SHA256",
                        help="current target hash for each --fixture; use NAME=absent for a new fixture")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        source = source_fixtures(args.source_url, args.source_token_env)
        if args.apply:
            payload = apply(source, args.source_url, args.target_dir, args.fixture,
                            expected_map(args.expect_target_sha))
        else:
            if args.fixture or args.expect_target_sha:
                raise SyncError("--fixture and --expect-target-sha require --apply")
            payload = report(source, args.target_dir)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except SyncError as exc:
        print(json.dumps({"mode": "apply" if args.apply else "check", "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
