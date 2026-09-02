# -*- coding: utf-8 -*-
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "sync_production_fixtures.py"
spec = importlib.util.spec_from_file_location("sync_production_fixtures", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def fixture(rows):
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


SOURCE_ROWS = [
    {"bom_level": 0, "parent_uid": "", "child_uid": "ROOT", "part_id": "ROOT-1", "quantity": "1"},
    {"bom_level": 1, "parent_uid": "ROOT", "child_uid": "CHILD", "part_id": "PART-1", "quantity": "2"},
]


class FixtureServer(BaseHTTPRequestHandler):
    fixtures = {"example.json": fixture(SOURCE_ROWS)}

    def log_message(self, *_args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/tc/v1/fixtures":
            body = json.dumps({"status": 200, "data": {"fixtures": [
                {"name": name} for name in sorted(self.fixtures)
            ]}}, ensure_ascii=False).encode("utf-8")
        elif path.startswith("/tc/v1/fixtures/"):
            name = path.rsplit("/", 1)[-1]
            body = self.fixtures.get(name)
            if body is None:
                self.send_error(404)
                return
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SyncProductionFixturesTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="mocktc-sync-"))
        self.target = self.tempdir / "fixtures"
        self.target.mkdir()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureServer)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.source = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        shutil.rmtree(self.tempdir)

    def run_cli(self, *args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return sync.main(["--source-url", self.source, "--target-dir", str(self.target), *args])

    def test_check_is_read_only_and_reports_difference(self):
        old = fixture([dict(SOURCE_ROWS[0], part_id="LOCAL")])
        target = self.target / "example.json"
        target.write_bytes(old)
        before = target.read_bytes()
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse((self.target / ".history").exists())
        self.assertFalse((self.target / ".sync-history").exists())

    def test_apply_requires_fixture_and_expected_hash(self):
        target = self.target / "example.json"
        old = fixture([dict(SOURCE_ROWS[0], part_id="LOCAL")])
        target.write_bytes(old)
        self.assertEqual(self.run_cli("--apply"), 2)
        self.assertEqual(self.run_cli("--apply", "--fixture", "example.json"), 2)
        self.assertEqual(target.read_bytes(), old)

    def test_apply_backs_up_replaces_and_writes_audit_manifest(self):
        target = self.target / "example.json"
        old = fixture([dict(SOURCE_ROWS[0], part_id="LOCAL")])
        target.write_bytes(old)
        old_sha = hashlib.sha256(old).hexdigest()
        self.assertEqual(self.run_cli("--apply", "--fixture", "example.json",
                                      "--expect-target-sha", "example.json=" + old_sha), 0)
        self.assertEqual(target.read_bytes(), FixtureServer.fixtures["example.json"])
        backups = list((self.target / ".history").glob("example.json.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), old)
        manifests = list((self.target / ".sync-history").glob("production-fixture-sync-*.json"))
        self.assertEqual(len(manifests), 1)
        payload = json.loads(manifests[0].read_text("utf-8"))
        self.assertEqual(payload["fixtures"][0]["previous_sha256"], old_sha)
        self.assertEqual(payload["fixtures"][0]["target_sha256"],
                         hashlib.sha256(FixtureServer.fixtures["example.json"]).hexdigest())

    def test_apply_refuses_stale_hash_without_writing(self):
        target = self.target / "example.json"
        old = fixture([dict(SOURCE_ROWS[0], part_id="LOCAL")])
        target.write_bytes(old)
        self.assertEqual(self.run_cli("--apply", "--fixture", "example.json",
                                      "--expect-target-sha", "example.json=" + "0" * 64), 2)
        self.assertEqual(target.read_bytes(), old)
        self.assertFalse((self.target / ".history").exists())

    def test_apply_can_create_only_with_explicit_absent_expectation(self):
        self.assertEqual(self.run_cli("--apply", "--fixture", "example.json",
                                      "--expect-target-sha", "example.json=absent"), 0)
        self.assertEqual((self.target / "example.json").read_bytes(),
                         FixtureServer.fixtures["example.json"])

    def test_apply_refuses_multiple_fixture_replacements(self):
        self.assertEqual(self.run_cli("--apply", "--fixture", "example.json",
                                      "--fixture", "another.json",
                                      "--expect-target-sha", "example.json=absent",
                                      "--expect-target-sha", "another.json=absent"), 2)
        self.assertFalse((self.target / "example.json").exists())


if __name__ == "__main__":
    unittest.main()
