#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mock Teamcenter (mocktc) - lightweight Siemens Teamcenter mock for interface testing.

A minimal Flask service that exposes Teamcenter-like RESTful BOM interfaces so
external systems (e.g. ECC) can develop and verify their integration without a
real Teamcenter installation. Every API call is recorded into an interface log
that is visible through the built-in web UI.

Runs on Python 3.6+ / Flask 2.0+.
"""

import json
import hmac
import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime

import fcntl

from flask import Flask, Response, g, jsonify, render_template, request


SERVICE_NAME = "Mock Teamcenter"
SERVICE_VERSION = "1.2.0"
API_PREFIX = "/tc/v1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("MOCKTC_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.environ.get("MOCKTC_DB_PATH", os.path.join(DATA_DIR, "mocktc.db"))
FIXTURE_DIR = os.environ.get("MOCKTC_FIXTURE_DIR", os.path.join(BASE_DIR, "fixtures"))
FIXTURE_BOM_FILENAME = "20260808-bom1-2.json"
FIXTURE_ITEM_UID = "item-litho-001"
MAX_LOG_BODY = 5000  # characters stored per request/response body


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False  # Flask 2.2+ / 3.x
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

    @app.context_processor
    def inject_service_context():
        return {"version": SERVICE_VERSION, "service": SERVICE_NAME}

    os.makedirs(DATA_DIR, exist_ok=True)

    # ------------------------------------------------------------------ db
    def get_db():
        if "db" not in g:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            g.db = conn
        return g.db

    @app.teardown_appcontext
    def close_db(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def init_db():
        conn = sqlite3.connect(DB_PATH)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                uid        TEXT PRIMARY KEY,
                item_id    TEXT NOT NULL UNIQUE,
                item_name  TEXT NOT NULL,
                item_type  TEXT NOT NULL DEFAULT 'Part',
                project    TEXT NOT NULL DEFAULT '',
                owner      TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'Released',
                created    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS item_revisions (
                uid         TEXT PRIMARY KEY,
                item_uid    TEXT NOT NULL REFERENCES items(uid),
                revision_id TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'Released',
                sequence    INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS bom_headers (
                uid         TEXT PRIMARY KEY,
                item_uid    TEXT NOT NULL REFERENCES items(uid),
                revision_uid TEXT,
                name        TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS bom_lines (
                uid                TEXT PRIMARY KEY,
                bom_uid            TEXT NOT NULL REFERENCES bom_headers(uid),
                parent_bomline_uid TEXT,
                sequence           INTEGER NOT NULL DEFAULT 0,
                position           TEXT NOT NULL DEFAULT '',
                quantity           REAL NOT NULL DEFAULT 1,
                unit               TEXT NOT NULL DEFAULT 'EA',
                child_item_uid     TEXT NOT NULL REFERENCES items(uid),
                child_revision_uid TEXT,
                notes              TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS api_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT NOT NULL,
                method        TEXT NOT NULL,
                path          TEXT NOT NULL,
                query         TEXT NOT NULL DEFAULT '',
                request_body  TEXT NOT NULL DEFAULT '',
                status        INTEGER NOT NULL DEFAULT 0,
                duration_ms   REAL NOT NULL DEFAULT 0,
                client_ip     TEXT NOT NULL DEFAULT '',
                user_agent    TEXT NOT NULL DEFAULT '',
                response_body TEXT NOT NULL DEFAULT '',
                is_api        INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_logs_ts ON api_logs(ts DESC);
            """
        )
        conn.commit()
        conn.close()

    def now_iso():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def backup_database():
        history_dir = os.path.join(DATA_DIR, ".history")
        os.makedirs(history_dir, mode=0o700, exist_ok=True)
        name = "mocktc.db.%s.%s.bak" % (
            datetime.now().strftime("%Y%m%dT%H%M%S%f"), uuid.uuid4().hex[:8]
        )
        target_path = os.path.join(history_dir, name)
        source = sqlite3.connect(DB_PATH)
        target = sqlite3.connect(target_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.chmod(target_path, 0o600)
        return name

    # ------------------------------------------------------------- seeding
    def seed_if_empty():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            count = conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]
        except sqlite3.OperationalError:
            count = 1
        if count > 0:
            conn.close()
            return
        cur = conn.cursor()
        created = now_iso()
        items = {
            "item-p1000": ("P-1000", "变速箱总成", "Assembly", "XM-MOCK", "张工", "Released"),
            "item-p2000": ("P-2000", "差速器总成", "Assembly", "XM-MOCK", "李工", "Released"),
            "item-p3000": ("P-3000", "离合器总成", "Assembly", "XM-MOCK", "王工", "Released"),
            "item-sa1001": ("SA-1001", "箱体总成", "Assembly", "XM-MOCK", "张工", "Released"),
            "item-sa1002": ("SA-1002", "端盖组件", "Assembly", "XM-MOCK", "张工", "Released"),
            "item-sa2001": ("SA-2001", "齿轮轴总成", "Assembly", "XM-MOCK", "李工", "Released"),
            "item-sa2002": ("SA-2002", "输出轴组件", "Assembly", "XM-MOCK", "李工", "Released"),
            "item-sa3001": ("SA-3001", "压盘组件", "Assembly", "XM-MOCK", "王工", "Released"),
            "item-m1101": ("M-1101", "箱体毛坯", "Part", "XM-MOCK", "张工", "Released"),
            "item-m1102": ("M-1102", "箱体端盖", "Part", "XM-MOCK", "张工", "Released"),
            "item-m1103": ("M-1103", "油封 50x70x8", "Part", "XM-MOCK", "张工", "Released"),
            "item-m1104": ("M-1104", "螺栓 M8x25", "Part", "XM-MOCK", "张工", "Released"),
            "item-m1105": ("M-1105", "定位销 6x20", "Part", "XM-MOCK", "张工", "Released"),
            "item-m2101": ("M-2101", "输入轴", "Part", "XM-MOCK", "李工", "Released"),
            "item-m2102": ("M-2102", "输出齿轮", "Part", "XM-MOCK", "李工", "Released"),
            "item-m2103": ("M-2103", "轴承 6205", "Part", "XM-MOCK", "李工", "Released"),
            "item-m2104": ("M-2104", "花键套", "Part", "XM-MOCK", "李工", "Released"),
            "item-m2105": ("M-2105", "卡簧 25", "Part", "XM-MOCK", "李工", "Released"),
            "item-m3101": ("M-3101", "压盘", "Part", "XM-MOCK", "王工", "Released"),
            "item-m3102": ("M-3102", "摩擦片", "Part", "XM-MOCK", "王工", "Released"),
            "item-m3103": ("M-3103", "分离轴承", "Part", "XM-MOCK", "王工", "Released"),
        }
        for uid, (item_id, name, typ, project, owner, status) in items.items():
            cur.execute(
                "INSERT OR IGNORE INTO items (uid, item_id, item_name, item_type, project, owner, status, created) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (uid, item_id, name, typ, project, owner, status, created),
            )
        # one revision per item
        for uid in items:
            rev_uid = uid.replace("item-", "rev-") + "-a"
            cur.execute(
                "INSERT OR IGNORE INTO item_revisions "
                "(uid, item_uid, revision_id, description, status, sequence) VALUES (?,?,?,?,?,?)",
                (rev_uid, uid, "A", "", "Released", 1),
            )
        # BOM headers for the three products
        headers = [
            ("bom-p1000", "item-p1000", "rev-p1000-a", "P-1000 变速箱总成 BOM"),
            ("bom-p2000", "item-p2000", "rev-p2000-a", "P-2000 差速器总成 BOM"),
            ("bom-p3000", "item-p3000", "rev-p3000-a", "P-3000 离合器总成 BOM"),
            ("bom-sa1001", "item-sa1001", "rev-sa1001-a", "SA-1001 箱体总成 BOM"),
            ("bom-sa1002", "item-sa1002", "rev-sa1002-a", "SA-1002 端盖组件 BOM"),
            ("bom-sa2001", "item-sa2001", "rev-sa2001-a", "SA-2001 齿轮轴总成 BOM"),
            ("bom-sa2002", "item-sa2002", "rev-sa2002-a", "SA-2002 输出轴组件 BOM"),
            ("bom-sa3001", "item-sa3001", "rev-sa3001-a", "SA-3001 压盘组件 BOM"),
        ]
        for uid, item_uid, rev_uid, name in headers:
            cur.execute(
                "INSERT OR IGNORE INTO bom_headers (uid, item_uid, revision_uid, name, description) "
                "VALUES (?,?,?,?,?)",
                (uid, item_uid, rev_uid, name, ""),
            )
        # bom lines: (uid, bom_uid, parent, seq, position, qty, unit, child_item_uid, notes)
        lines = [
            ("bl-p1000-1", "bom-p1000", None, 10, "0010", 1.0, "EA", "item-sa1001", "箱体总成"),
            ("bl-p1000-2", "bom-p1000", None, 20, "0020", 1.0, "EA", "item-sa2001", "齿轮轴总成"),
            ("bl-p1000-3", "bom-p1000", None, 30, "0030", 2.0, "EA", "item-m1103", "油封"),
            ("bl-p1000-4", "bom-p1000", None, 40, "0040", 12.0, "EA", "item-m1104", "螺栓"),
            ("bl-sa1001-1", "bom-sa1001", None, 10, "0010", 1.0, "EA", "item-m1101", "箱体毛坯"),
            ("bl-sa1001-2", "bom-sa1001", None, 20, "0020", 1.0, "EA", "item-sa1002", "端盖组件"),
            ("bl-sa1001-3", "bom-sa1001", None, 30, "0030", 4.0, "EA", "item-m1105", "定位销"),
            ("bl-sa1002-1", "bom-sa1002", None, 10, "0010", 1.0, "EA", "item-m1102", "端盖"),
            ("bl-sa1002-2", "bom-sa1002", None, 20, "0020", 1.0, "EA", "item-m1103", "油封"),
            ("bl-sa2001-1", "bom-sa2001", None, 10, "0010", 1.0, "EA", "item-m2101", "输入轴"),
            ("bl-sa2001-2", "bom-sa2001", None, 20, "0020", 1.0, "EA", "item-m2102", "输出齿轮"),
            ("bl-sa2001-3", "bom-sa2001", None, 30, "0030", 2.0, "EA", "item-m2103", "轴承"),
            ("bl-sa2001-4", "bom-sa2001", None, 40, "0040", 1.0, "EA", "item-m2104", "花键套"),
            ("bl-sa2001-5", "bom-sa2001", None, 50, "0050", 1.0, "EA", "item-m2105", "卡簧"),
            ("bl-sa2002-1", "bom-sa2002", None, 10, "0010", 1.0, "EA", "item-m2102", "输出齿轮"),
            ("bl-sa2002-2", "bom-sa2002", None, 20, "0020", 1.0, "EA", "item-m2103", "轴承"),
            ("bl-p2000-1", "bom-p2000", None, 10, "0010", 1.0, "EA", "item-sa2002", "输出轴组件"),
            ("bl-p2000-2", "bom-p2000", None, 20, "0020", 2.0, "EA", "item-sa1002", "端盖组件"),
            ("bl-p2000-3", "bom-p2000", None, 30, "0030", 4.0, "EA", "item-m2103", "轴承"),
            ("bl-p3000-1", "bom-p3000", None, 10, "0010", 1.0, "EA", "item-sa3001", "压盘组件"),
            ("bl-p3000-2", "bom-p3000", None, 20, "0020", 2.0, "EA", "item-m3102", "摩擦片"),
            ("bl-p3000-3", "bom-p3000", None, 30, "0030", 1.0, "EA", "item-m3103", "分离轴承"),
            ("bl-sa3001-1", "bom-sa3001", None, 10, "0010", 1.0, "EA", "item-m3101", "压盘"),
            ("bl-sa3001-2", "bom-sa3001", None, 20, "0020", 1.0, "EA", "item-m3102", "摩擦片"),
        ]
        for uid, bom_uid, parent, seq, pos, qty, unit, child, notes in lines:
            cur.execute(
                "INSERT OR IGNORE INTO bom_lines "
                "(uid, bom_uid, parent_bomline_uid, sequence, position, quantity, unit, child_item_uid, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (uid, bom_uid, parent, seq, pos, qty, unit, child, notes),
            )
        conn.commit()
        conn.close()

    init_db()
    seed_if_empty()

    # ---------------------------------------------------- external fixtures
    # BOM JSON 数据集：每次请求从磁盘重新读取，保证多 worker 场景下修改后立即
    # 可见。写操作使用文件锁、历史快照和同目录原子替换，避免并发覆盖或半文件。
    FIXTURE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    def fixture_name_ok(name):
        return (
            isinstance(name, str)
            and bool(name)
            and ".." not in name
            and "/" not in name
            and "\\" not in name
            and "\x00" not in name
            and bool(FIXTURE_NAME_RE.match(name))
        )

    def load_fixtures():
        loaded = {}
        if not os.path.isdir(FIXTURE_DIR):
            return loaded
        for fname in sorted(os.listdir(FIXTURE_DIR)):
            if not fname.endswith(".json") or not fixture_name_ok(fname):
                continue
            fpath = os.path.join(FIXTURE_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, "rb") as fh:
                    raw = fh.read()
                rows = json.loads(raw)
                if not isinstance(rows, list):
                    app.logger.warning(
                        "fixture %s: top-level is not a JSON array, skipped", fname
                    )
                    continue
                fields = []
                child_to_row = {}
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    for key in row:
                        if key not in fields:
                            fields.append(key)
                    cid = row.get("child_uid")
                    if cid is not None and str(cid) not in child_to_row:
                        child_to_row[str(cid)] = row
                root = rows[0] if rows else None
                for row in rows:
                    if isinstance(row, dict) and row.get("bom_level") == 0:
                        root = row
                        break
                root = root if isinstance(root, dict) else {}
                loaded[fname] = {
                    "meta": {
                        "name": fname,
                        "size": len(raw),
                        "rows": len(rows),
                        "fields": fields,
                        "item_id": str(root.get("part_id") or ""),
                        "part_name": str(root.get("part_name") or ""),
                        "revision": str(root.get("revision_id") or ""),
                    },
                    "raw": raw,
                    "rows": rows,
                    "child_to_row": child_to_row,
                }
            except Exception as exc:
                app.logger.warning("fixture %s: failed to load: %s", fname, exc)
        return loaded

    @contextmanager
    def fixture_write_lock():
        os.makedirs(FIXTURE_DIR, exist_ok=True)
        lock_path = os.path.join(FIXTURE_DIR, ".mocktc-fixtures.lock")
        with open(lock_path, "a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def validate_fixture_rows(rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("BOM 数据至少需要一行根节点")
        child_uids = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError("第 %d 行必须为对象" % (index + 1))
            child_uid = str(row.get("child_uid") or "").strip()
            part_id = str(row.get("part_id") or "").strip()
            if not child_uid or not part_id:
                raise ValueError("第 %d 行 child_uid 和 part_id 不能为空" % (index + 1))
            try:
                level = int(row.get("bom_level", 0))
            except (TypeError, ValueError):
                raise ValueError("第 %d 行 bom_level 必须为非负整数" % (index + 1))
            if level < 0:
                raise ValueError("第 %d 行 bom_level 必须为非负整数" % (index + 1))
            row["bom_level"] = level
            child_uids.append(child_uid)
        if len(child_uids) != len(set(child_uids)):
            raise ValueError("child_uid 必须唯一")
        known = set(child_uids)
        roots = 0
        for index, row in enumerate(rows):
            parent_uid = str(row.get("parent_uid") or "").strip()
            child_uid = str(row.get("child_uid") or "").strip()
            if not parent_uid:
                roots += 1
                if int(row.get("bom_level") or 0) != 0:
                    raise ValueError("第 %d 行无父节点时 bom_level 必须为 0" % (index + 1))
            elif parent_uid == child_uid or parent_uid not in known:
                raise ValueError("第 %d 行 parent_uid 不存在或指向自身" % (index + 1))
        if roots != 1:
            raise ValueError("BOM 必须且只能有一个根节点")

    def persist_fixture(name, rows):
        validate_fixture_rows(rows)
        path = os.path.join(FIXTURE_DIR, name)
        history_dir = os.path.join(FIXTURE_DIR, ".history")
        os.makedirs(history_dir, mode=0o700, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        backup_name = "%s.%s.%s.bak" % (name, stamp, uuid.uuid4().hex[:8])
        backup_path = os.path.join(history_dir, backup_name)
        if os.path.exists(path):
            shutil.copy2(path, backup_path)
            os.chmod(backup_path, 0o600)
        temp_path = "%s.tmp.%s" % (path, uuid.uuid4().hex)
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(rows, handle, ensure_ascii=False, indent=1)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o644)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        return backup_name if os.path.exists(backup_path) else ""

    fixtures = load_fixtures()

    # 兼容旧逻辑：LITHO-001（FIXTURE_BOM_FILENAME）的 BOM 接口按原始字节返回。
    fixture_bom = None
    fixture_raw = None
    fixture_root = {}
    legacy_fixture = fixtures.get(FIXTURE_BOM_FILENAME)
    if legacy_fixture is not None and legacy_fixture["meta"]["item_id"]:
        fixture_raw = legacy_fixture["raw"]
        fixture_bom = legacy_fixture["rows"]
        fixture_root = {
            "item_id": legacy_fixture["meta"]["item_id"],
            "item_name": legacy_fixture["meta"]["part_name"] or "BOM Fixture",
            "revision": legacy_fixture["meta"]["revision"] or "A",
        }

    def register_fixture_item():
        if not fixture_root:
            return
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        created = now_iso()
        conn.execute(
            "INSERT OR IGNORE INTO items "
            "(uid, item_id, item_name, item_type, project, owner, status, created) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                FIXTURE_ITEM_UID,
                fixture_root["item_id"],
                fixture_root["item_name"],
                "Assembly",
                "XM-MOCK",
                "",
                "Released",
                created,
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO item_revisions "
            "(uid, item_uid, revision_id, description, status, sequence) VALUES (?,?,?,?,?,1)",
            (
                "rev-" + FIXTURE_ITEM_UID + "-" + fixture_root["revision"],
                FIXTURE_ITEM_UID,
                fixture_root["revision"],
                "",
                "Released",
            ),
        )
        conn.commit()
        conn.close()

    register_fixture_item()

    def fixture_bom_response():
        current = load_fixtures().get(FIXTURE_BOM_FILENAME)
        if current is not None:
            return Response(current["raw"], mimetype="application/json")
        return api_err(500, "BOM fixture not loaded")

    # ------------------------------------------------------------ helpers
    def item_row(uid):
        return get_db().execute("SELECT * FROM items WHERE uid=?", (uid,)).fetchone()

    def revision_rows(item_uid):
        return get_db().execute(
            "SELECT * FROM item_revisions WHERE item_uid=? ORDER BY sequence", (item_uid,)
        ).fetchall()

    def header_for_item(item_uid):
        return get_db().execute(
            "SELECT * FROM bom_headers WHERE item_uid=?", (item_uid,)
        ).fetchone()

    def lines_for(bom_uid, parent_uid):
        return get_db().execute(
            "SELECT * FROM bom_lines WHERE bom_uid=? AND parent_bomline_uid IS ? ORDER BY sequence",
            (bom_uid, parent_uid),
        ).fetchall()

    def serialize_item(row):
        row = dict(row)
        rev = get_db().execute(
            "SELECT revision_id FROM item_revisions WHERE item_uid=? ORDER BY sequence DESC LIMIT 1",
            (row["uid"],),
        ).fetchone()
        row["revision"] = rev["revision_id"] if rev else ""
        return row

    def serialize_bom_line(row, depth, visited):
        row = dict(row)
        child = item_row(row["child_item_uid"])
        if child is None:
            return None
        out = {
            "uid": row["uid"],
            "position": row["position"],
            "sequence": row["sequence"],
            "quantity": row["quantity"],
            "unit": row["unit"],
            "notes": row["notes"],
            "child_item": serialize_item(child),
            "children": [],
        }
        if depth != 0:
            out["children"] = build_tree(child["uid"], depth - 1, visited)
        return out

    def build_tree(item_uid, depth, visited):
        if item_uid in visited:
            return []
        header = header_for_item(item_uid)
        if header is None:
            return []
        next_visited = set(visited)
        next_visited.add(item_uid)
        children = []
        for line in lines_for(header["uid"], None):
            node = serialize_bom_line(line, depth, next_visited)
            if node:
                children.append(node)
        return children

    def get_fixture_or_error(name):
        """安全查找 fixture；非法名称 400，合法但不存在 404。"""
        if not fixture_name_ok(name):
            return None, api_err(400, "Invalid fixture name: " + str(name))
        fixture = load_fixtures().get(name)
        if fixture is None:
            return None, api_err(404, "Fixture not found: " + str(name))
        return fixture, None

    def enrich_fixture_row(row, fixture):
        """为 fixture 行补充父级物料信息（parent_id / parent_name）。"""
        out = dict(row)
        parent = fixture["child_to_row"].get(str(row.get("parent_uid") or ""))
        out["parent_id"] = str(parent.get("part_id") or "") if parent else ""
        out["parent_name"] = str(parent.get("part_name") or "") if parent else ""
        return out

    def api_ok(data):
        return jsonify({"status": 200, "message": "OK", "data": data})

    def api_err(code, message, extra=None):
        payload = {"status": code, "message": message, "data": None}
        if extra:
            payload["data"] = extra
        return jsonify(payload), code

    def parse_depth():
        raw = request.args.get("depth", "0")
        try:
            depth = int(raw)
        except (TypeError, ValueError):
            raise ValueError("depth 参数必须为整数")
        return depth

    # --------------------------------------------------------- auth (opt)
    token = os.environ.get("MOCKTC_API_TOKEN", "").strip()
    admin_token = os.environ.get("MOCKTC_ADMIN_TOKEN", "").strip()

    def supplied_admin_token():
        supplied = request.headers.get("X-MockTC-Admin-Token", "").strip()
        if not supplied:
            authorization = request.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                supplied = authorization[7:].strip()
        return supplied

    def require_admin():
        if not admin_token:
            return api_err(503, "MockTC 管理写入功能尚未配置管理员令牌")
        supplied = supplied_admin_token()
        if not supplied or not hmac.compare_digest(supplied, admin_token):
            return api_err(403, "管理员令牌无效")
        return None

    @app.before_request
    def check_token():
        if not token or not request.path.startswith(API_PREFIX):
            return None
        supplied = request.headers.get("Authorization", "")
        if supplied.startswith("Bearer "):
            supplied = supplied[7:]
        elif supplied.startswith("Basic "):
            supplied = ""
        if not supplied:
            supplied = request.args.get("token", "")
        admin_supplied = supplied_admin_token()
        if supplied != token and not (
            admin_token and admin_supplied and hmac.compare_digest(admin_supplied, admin_token)
        ):
            return api_err(401, "Unauthorized: missing or invalid API token")
        return None

    # ------------------------------------------------------------- logging
    @app.before_request
    def start_logging():
        g.request_started = time.time()
        g.request_body = ""
        if request.path.startswith(API_PREFIX) and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                g.request_body = request.get_data(as_text=True)[:MAX_LOG_BODY]
            except Exception:
                g.request_body = ""

    @app.after_request
    def write_api_log(response):
        if not request.path.startswith(API_PREFIX):
            return response
        duration = (time.time() - g.get("request_started", time.time())) * 1000.0
        body = ""
        try:
            body = response.get_data(as_text=True)[:MAX_LOG_BODY]
        except Exception:
            body = ""
        try:
            db = get_db()
            db.execute(
                "INSERT INTO api_logs "
                "(ts, method, path, query, request_body, status, duration_ms, client_ip, user_agent, response_body, is_api) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                (
                    now_iso(),
                    request.method,
                    request.path,
                    request.query_string.decode("utf-8", "replace")[:2000],
                    g.get("request_body", ""),
                    response.status_code,
                    round(duration, 2),
                    request.remote_addr or "",
                    request.headers.get("User-Agent", "")[:500],
                    body,
                ),
            )
            db.commit()
        except Exception:
            app.logger.exception("failed to write api log")
        return response

    # -------------------------------------------------------------- health
    @app.route(API_PREFIX + "/health", methods=["GET"])
    def health():
        current_fixtures = load_fixtures()
        return api_ok(
            {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "api_base": API_PREFIX,
                "time": now_iso(),
                "status": "up",
                "fixtures": {
                    "total": len(current_fixtures),
                    "names": [f["meta"]["name"] for f in current_fixtures.values()],
                },
            }
        )

    # ---------------------------------------------------------------- items
    @app.route(API_PREFIX + "/items", methods=["GET"])
    def list_items():
        where = []
        params = []
        if request.args.get("item_id"):
            where.append("item_id LIKE ?")
            params.append("%" + request.args["item_id"] + "%")
        if request.args.get("q"):
            where.append("(item_id LIKE ? OR item_name LIKE ?)")
            params.extend(["%" + request.args["q"] + "%"] * 2)
        if request.args.get("item_type"):
            where.append("item_type = ?")
            params.append(request.args["item_type"])
        if request.args.get("project"):
            where.append("project = ?")
            params.append(request.args["project"])
        if request.args.get("status"):
            where.append("status = ?")
            params.append(request.args["status"])
        try:
            limit = max(1, min(int(request.args.get("limit", 50)), 500))
            offset = max(0, int(request.args.get("offset", 0)))
        except (TypeError, ValueError):
            return api_err(400, "limit/offset 必须为整数")
        sql_where = ("WHERE " + " AND ".join(where)) if where else ""
        db = get_db()
        total = db.execute(
            "SELECT COUNT(*) AS c FROM items " + sql_where, params
        ).fetchone()["c"]
        rows = db.execute(
            "SELECT * FROM items " + sql_where + " ORDER BY item_id LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return api_ok(
            {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [serialize_item(r) for r in rows],
            }
        )

    @app.route(API_PREFIX + "/items", methods=["POST"])
    def create_item():
        denied = require_admin()
        if denied is not None:
            return denied
        payload = request.get_json(silent=True) or {}
        item_id = str(payload.get("item_id") or "").strip()
        item_name = str(payload.get("item_name") or "").strip()
        if not item_id or not item_name:
            return api_err(400, "item_id 和 item_name 为必填项")
        db = get_db()
        if db.execute("SELECT 1 FROM items WHERE item_id=?", (item_id,)).fetchone():
            return api_err(409, "item_id 已存在: " + item_id)
        uid = "item-" + uuid.uuid4().hex[:10]
        backup_database()
        db.execute(
            "INSERT INTO items (uid, item_id, item_name, item_type, project, owner, status, created) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                uid,
                item_id,
                item_name,
                str(payload.get("item_type") or "Part"),
                str(payload.get("project") or "XM-MOCK"),
                str(payload.get("owner") or ""),
                str(payload.get("status") or "Released"),
                now_iso(),
            ),
        )
        rev_uid = "rev-" + uid + "-" + str(payload.get("revision_id") or "A")
        db.execute(
            "INSERT INTO item_revisions (uid, item_uid, revision_id, description, status, sequence) "
            "VALUES (?,?,?,?,?,1)",
            (rev_uid, uid, str(payload.get("revision_id") or "A"), "", "Released"),
        )
        db.commit()
        row = item_row(uid)
        return jsonify(
            {
                "status": 201,
                "message": "Item created",
                "data": serialize_item(row),
            }
        ), 201

    @app.route(API_PREFIX + "/items/<uid>", methods=["GET"])
    def get_item(uid):
        row = item_row(uid)
        if row is None:
            return api_err(404, "Item not found: " + uid)
        return api_ok(serialize_item(row))

    @app.route(API_PREFIX + "/items/<uid>", methods=["PATCH"])
    def update_item(uid):
        denied = require_admin()
        if denied is not None:
            return denied
        row = item_row(uid)
        if row is None:
            return api_err(404, "Item not found: " + uid)
        payload = request.get_json(silent=True) or {}
        allowed = ("item_id", "item_name", "item_type", "project", "owner", "status")
        updates = {key: str(payload[key]).strip() for key in allowed if key in payload}
        if not updates:
            return api_err(400, "没有可更新的物料字段")
        if "item_id" in updates and not updates["item_id"]:
            return api_err(400, "item_id 不能为空")
        if "item_name" in updates and not updates["item_name"]:
            return api_err(400, "item_name 不能为空")
        try:
            backup_database()
            get_db().execute(
                "UPDATE items SET %s WHERE uid=?" % ",".join(key + "=?" for key in updates),
                list(updates.values()) + [uid],
            )
            get_db().commit()
        except sqlite3.IntegrityError:
            get_db().rollback()
            return api_err(409, "item_id 已存在")
        return api_ok(serialize_item(item_row(uid)))

    @app.route(API_PREFIX + "/items/<uid>/revisions", methods=["GET"])
    def list_revisions(uid):
        row = item_row(uid)
        if row is None:
            return api_err(404, "Item not found: " + uid)
        revs = [dict(r) for r in revision_rows(uid)]
        return api_ok({"item": serialize_item(row), "revisions": revs})

    @app.route(API_PREFIX + "/items/<uid>/revisions/<rev_uid>", methods=["GET"])
    def get_revision(uid, rev_uid):
        row = item_row(uid)
        if row is None:
            return api_err(404, "Item not found: " + uid)
        rev = get_db().execute(
            "SELECT * FROM item_revisions WHERE uid=? AND item_uid=?", (rev_uid, uid)
        ).fetchone()
        if rev is None:
            return api_err(404, "Revision not found: " + rev_uid)
        return api_ok({"item": serialize_item(row), "revision": dict(rev)})

    # ------------------------------------------------------------------- bom
    @app.route(API_PREFIX + "/items/<uid>/bom", methods=["GET"])
    def get_bom(uid):
        row = item_row(uid)
        if row is None:
            return api_err(404, "Item not found: " + uid)
        if uid == FIXTURE_ITEM_UID:
            return fixture_bom_response()
        try:
            depth = parse_depth()
        except ValueError as exc:
            return api_err(400, str(exc))
        header = header_for_item(uid)
        if header is None:
            return api_ok(
                {
                    "item": serialize_item(row),
                    "bom_header": None,
                    "bom_lines": [],
                    "note": "该物料没有已发布的 BOM",
                }
            )
        lines = build_tree(uid, depth, set())
        return api_ok(
            {
                "item": serialize_item(row),
                "bom_header": dict(header),
                "bom_lines": lines,
            }
        )

    @app.route(API_PREFIX + "/items/<uid>/bom/expand", methods=["GET"])
    def expand_bom(uid):
        row = item_row(uid)
        if row is None:
            return api_err(404, "Item not found: " + uid)
        if uid == FIXTURE_ITEM_UID:
            return fixture_bom_response()
        header = header_for_item(uid)
        if header is None:
            return api_ok(
                {
                    "item": serialize_item(row),
                    "bom_header": None,
                    "bom_lines": [],
                    "note": "该物料没有已发布的 BOM",
                }
            )
        lines = build_tree(uid, -1, set())
        return api_ok(
            {
                "item": serialize_item(row),
                "bom_header": dict(header),
                "bom_lines": lines,
                "expanded": True,
            }
        )

    @app.route(API_PREFIX + "/structures/<item_uid>", methods=["GET"])
    def get_structure(item_uid):
        return get_bom(item_uid)

    @app.route(API_PREFIX + "/bomlines/<uid>", methods=["GET"])
    def get_bomline(uid):
        row = get_db().execute(
            "SELECT * FROM bom_lines WHERE uid=?", (uid,)
        ).fetchone()
        if row is None:
            return api_err(404, "BOM line not found: " + uid)
        line = dict(row)
        child = item_row(line["child_item_uid"])
        line["child_item"] = serialize_item(child) if child else None
        line["children"] = []
        return api_ok(line)

    @app.route(API_PREFIX + "/bomlines/<uid>", methods=["PATCH"])
    def update_bomline(uid):
        denied = require_admin()
        if denied is not None:
            return denied
        row = get_db().execute("SELECT * FROM bom_lines WHERE uid=?", (uid,)).fetchone()
        if row is None:
            return api_err(404, "BOM line not found: " + uid)
        payload = request.get_json(silent=True) or {}
        allowed = ("position", "sequence", "quantity", "unit", "child_item_uid", "notes")
        updates = {key: payload[key] for key in allowed if key in payload}
        if not updates:
            return api_err(400, "没有可更新的 BOM 字段")
        try:
            if "sequence" in updates:
                updates["sequence"] = int(updates["sequence"])
            if "quantity" in updates:
                updates["quantity"] = float(updates["quantity"])
                if updates["quantity"] <= 0:
                    raise ValueError("quantity 必须大于 0")
        except (TypeError, ValueError) as exc:
            return api_err(400, str(exc))
        if "child_item_uid" in updates and item_row(str(updates["child_item_uid"])) is None:
            return api_err(400, "child_item_uid 不存在")
        backup_database()
        get_db().execute(
            "UPDATE bom_lines SET %s WHERE uid=?" % ",".join(key + "=?" for key in updates),
            list(updates.values()) + [uid],
        )
        get_db().commit()
        return get_bomline(uid)

    @app.route(API_PREFIX + "/items/<uid>/bomlines", methods=["POST"])
    def create_bomline(uid):
        denied = require_admin()
        if denied is not None:
            return denied
        item = item_row(uid)
        if item is None:
            return api_err(404, "Item not found: " + uid)
        payload = request.get_json(silent=True) or {}
        child_uid = str(payload.get("child_item_uid") or "").strip()
        if not child_uid or item_row(child_uid) is None:
            return api_err(400, "child_item_uid 必须指向已存在物料")
        try:
            quantity = float(payload.get("quantity", 1))
            sequence = int(payload.get("sequence", 0))
            if quantity <= 0:
                raise ValueError("quantity 必须大于 0")
        except (TypeError, ValueError) as exc:
            return api_err(400, str(exc))
        db = get_db()
        backup_database()
        header = header_for_item(uid)
        if header is None:
            header_uid = "bom-" + uuid.uuid4().hex[:12]
            revision = revision_rows(uid)
            revision_uid = revision[-1]["uid"] if revision else None
            db.execute(
                "INSERT INTO bom_headers(uid,item_uid,revision_uid,name,description) VALUES(?,?,?,?,?)",
                (header_uid, uid, revision_uid, "%s %s BOM" % (item["item_id"], item["item_name"]), ""),
            )
        else:
            header_uid = header["uid"]
        line_uid = "bl-" + uuid.uuid4().hex[:12]
        db.execute(
            "INSERT INTO bom_lines(uid,bom_uid,parent_bomline_uid,sequence,position,quantity,unit,child_item_uid,notes) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (line_uid, header_uid, None, sequence, str(payload.get("position") or ""), quantity,
             str(payload.get("unit") or "EA"), child_uid, str(payload.get("notes") or "")),
        )
        db.commit()
        response = get_bomline(line_uid)
        if isinstance(response, tuple):
            response[0].status_code = 201
        else:
            response.status_code = 201
        return response

    @app.route(API_PREFIX + "/bomlines/<uid>", methods=["DELETE"])
    def delete_bomline(uid):
        denied = require_admin()
        if denied is not None:
            return denied
        row = get_db().execute("SELECT * FROM bom_lines WHERE uid=?", (uid,)).fetchone()
        if row is None:
            return api_err(404, "BOM line not found: " + uid)
        backup_database()
        get_db().execute("DELETE FROM bom_lines WHERE uid=?", (uid,))
        get_db().commit()
        return api_ok({"deleted": uid})

    @app.route(API_PREFIX + "/bomlines/<uid>/children", methods=["GET"])
    def bomline_children(uid):
        row = get_db().execute(
            "SELECT * FROM bom_lines WHERE uid=?", (uid,)
        ).fetchone()
        if row is None:
            return api_err(404, "BOM line not found: " + uid)
        try:
            depth = parse_depth()
        except ValueError as exc:
            return api_err(400, str(exc))
        children = build_tree(row["child_item_uid"], depth, set())
        return api_ok({"bom_line": dict(row), "children": children})

    # --------------------------------------------------------- fixture API
    @app.route(API_PREFIX + "/fixtures", methods=["GET"])
    def list_fixtures():
        current_fixtures = load_fixtures()
        return api_ok(
            {
                "total": len(current_fixtures),
                "fixtures": [f["meta"] for f in current_fixtures.values()],
            }
        )

    @app.route(API_PREFIX + "/fixtures/<name>", methods=["GET"])
    def get_fixture_file(name):
        fixture, err = get_fixture_or_error(name)
        if err is not None:
            return err
        if (request.args.get("raw") or "").lower() in ("1", "true", "yes", "on"):
            return Response(fixture["raw"], mimetype="application/json")
        return api_ok(
            {
                "fixture": fixture["meta"],
                "total": fixture["meta"]["rows"],
                "fields": fixture["meta"]["fields"],
                "items": fixture["rows"],
            }
        )

    @app.route(API_PREFIX + "/fixtures/<name>/query", methods=["GET"])
    def query_fixture(name):
        fixture, err = get_fixture_or_error(name)
        if err is not None:
            return err
        part_id_q = (request.args.get("part_id") or "").strip()
        part_name_q = (request.args.get("part_name") or "").strip()
        q = (request.args.get("q") or "").strip()
        revision_q = (request.args.get("revision_id") or "").strip()
        child_uid_q = (request.args.get("child_uid") or "").strip()
        parent_uid_q = (request.args.get("parent_uid") or "").strip()
        parent_id_q = (request.args.get("parent_id") or "").strip()
        exact = (request.args.get("exact") or "").lower() in ("1", "true", "yes", "on")
        bom_level_raw = (request.args.get("bom_level") or "").strip()
        try:
            limit = max(1, min(int(request.args.get("limit", 200)), 1000))
            offset = max(0, int(request.args.get("offset", 0)))
        except (TypeError, ValueError):
            return api_err(400, "limit/offset 必须为整数")
        if bom_level_raw:
            try:
                bom_level = int(bom_level_raw)
            except ValueError:
                return api_err(400, "bom_level 必须为整数")
        else:
            bom_level = None

        parent_uid_for_id = None
        if parent_id_q:
            for row in fixture["rows"]:
                if isinstance(row, dict) and str(row.get("part_id") or "").strip() == parent_id_q:
                    parent_uid_for_id = row.get("child_uid")
                    break

        def match(row):
            if not isinstance(row, dict):
                return False

            def s(key):
                return str(row.get(key) or "").strip()

            if part_id_q:
                pid = s("part_id").lower()
                needle = part_id_q.lower()
                if exact:
                    if pid != needle:
                        return False
                elif needle not in pid:
                    return False
            if part_name_q and part_name_q.lower() not in s("part_name").lower():
                return False
            if q and q.lower() not in s("part_id").lower() and q.lower() not in s("part_name").lower():
                return False
            if revision_q and s("revision_id") != revision_q:
                return False
            if bom_level is not None and row.get("bom_level") != bom_level:
                return False
            if child_uid_q and s("child_uid") != child_uid_q:
                return False
            if parent_uid_q and s("parent_uid") != parent_uid_q:
                return False
            if parent_id_q:
                if parent_uid_for_id is None or s("parent_uid") != str(parent_uid_for_id):
                    return False
            return True

        matched = [enrich_fixture_row(r, fixture) for r in fixture["rows"] if match(r)]
        return api_ok(
            {
                "fixture": fixture["meta"],
                "total": len(matched),
                "limit": limit,
                "offset": offset,
                "items": matched[offset : offset + limit],
            }
        )

    @app.route(API_PREFIX + "/fixtures/<name>/materials/<part_id>", methods=["GET"])
    def get_fixture_material(name, part_id):
        fixture, err = get_fixture_or_error(name)
        if err is not None:
            return err
        part_id = part_id.strip()
        matched = []
        for row in fixture["rows"]:
            if isinstance(row, dict) and str(row.get("part_id") or "").strip() == part_id:
                matched.append(row)
        if not matched:
            return api_err(
                404, "Material not found in fixture " + name + ": " + part_id
            )
        enriched = [enrich_fixture_row(r, fixture) for r in matched]
        return api_ok(
            {
                "fixture": fixture["meta"],
                "part_id": part_id,
                "part_name": str(matched[0].get("part_name") or ""),
                "revision_id": str(matched[0].get("revision_id") or ""),
                "total": len(enriched),
                "items": enriched,
            }
        )

    fixture_editable_fields = (
        "bom_level", "parent_uid", "part_id", "part_name", "revision_id",
        "quantity", "unit", "bom_number", "bom_alt", "item_category",
        "item_no", "scrap_rate", "plant", "usage",
    )

    @app.route(API_PREFIX + "/fixtures/<name>/rows/<child_uid>", methods=["PATCH"])
    def update_fixture_row(name, child_uid):
        denied = require_admin()
        if denied is not None:
            return denied
        payload = request.get_json(silent=True) or {}
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        updates = {key: fields[key] for key in fixture_editable_fields if key in fields}
        if not updates:
            return api_err(400, "没有可更新的 BOM 字段")
        if any(isinstance(value, (dict, list)) for value in updates.values()):
            return api_err(400, "BOM 字段只接受标量值")
        with fixture_write_lock():
            fixture, err = get_fixture_or_error(name)
            if err is not None:
                return err
            rows = [dict(row) for row in fixture["rows"]]
            target = next((row for row in rows if str(row.get("child_uid") or "") == child_uid), None)
            if target is None:
                return api_err(404, "BOM row not found: " + child_uid)
            target.update(updates)
            try:
                backup = persist_fixture(name, rows)
            except ValueError as exc:
                return api_err(400, str(exc))
        return api_ok({"fixture": name, "updated": child_uid, "row": target, "backup": backup})

    @app.route(API_PREFIX + "/fixtures/<name>/rows", methods=["POST"])
    def create_fixture_row(name):
        denied = require_admin()
        if denied is not None:
            return denied
        payload = request.get_json(silent=True) or {}
        part_id = str(payload.get("part_id") or "").strip()
        parent_uid = str(payload.get("parent_uid") or "").strip()
        if not part_id or not parent_uid:
            return api_err(400, "part_id 和 parent_uid 为必填项")
        with fixture_write_lock():
            fixture, err = get_fixture_or_error(name)
            if err is not None:
                return err
            rows = [dict(row) for row in fixture["rows"]]
            parent = next((row for row in rows if str(row.get("child_uid") or "") == parent_uid), None)
            if parent is None:
                return api_err(400, "parent_uid 不存在")
            child_uid = str(payload.get("child_uid") or "").strip() or (
                "TC-%s-%s" % (re.sub(r"[^A-Za-z0-9_-]", "-", part_id), uuid.uuid4().hex[:8])
            )
            row = {key: "" for key in fixture["meta"]["fields"]}
            row.update({
                "bom_level": int(parent.get("bom_level") or 0) + 1,
                "parent_uid": parent_uid,
                "child_uid": child_uid,
                "part_id": part_id,
                "part_name": str(payload.get("part_name") or ""),
                "revision_id": str(payload.get("revision_id") or parent.get("revision_id") or ""),
                "quantity": payload.get("quantity", "1"),
                "unit": str(payload.get("unit") or "EA"),
            })
            for key in fixture_editable_fields:
                if key in payload:
                    if key == "bom_level" and str(payload[key]).strip() == "":
                        continue
                    row[key] = payload[key]
            rows.append(row)
            try:
                backup = persist_fixture(name, rows)
            except ValueError as exc:
                return api_err(400, str(exc))
        return jsonify({"status": 201, "message": "BOM row created", "data": {
            "fixture": name, "row": row, "backup": backup,
        }}), 201

    @app.route(API_PREFIX + "/fixtures/<name>/rows/<child_uid>", methods=["DELETE"])
    def delete_fixture_row(name, child_uid):
        denied = require_admin()
        if denied is not None:
            return denied
        cascade = (request.args.get("cascade") or "").lower() in ("1", "true", "yes", "on")
        with fixture_write_lock():
            fixture, err = get_fixture_or_error(name)
            if err is not None:
                return err
            rows = [dict(row) for row in fixture["rows"]]
            target = next((row for row in rows if str(row.get("child_uid") or "") == child_uid), None)
            if target is None:
                return api_err(404, "BOM row not found: " + child_uid)
            if not str(target.get("parent_uid") or ""):
                return api_err(400, "根节点不能删除")
            descendants = set([child_uid])
            changed = True
            while changed:
                changed = False
                for row in rows:
                    uid = str(row.get("child_uid") or "")
                    if str(row.get("parent_uid") or "") in descendants and uid not in descendants:
                        descendants.add(uid)
                        changed = True
            if len(descendants) > 1 and not cascade:
                return api_err(409, "该节点包含 %d 个下级，请使用 cascade=1 明确级联删除" % (len(descendants) - 1))
            kept = [row for row in rows if str(row.get("child_uid") or "") not in descendants]
            try:
                backup = persist_fixture(name, kept)
            except ValueError as exc:
                return api_err(400, str(exc))
        return api_ok({"fixture": name, "deleted": sorted(descendants), "backup": backup})

    # -------------------------------------------------------------- UI pages
    @app.route("/")
    def index():
        db = get_db()
        current_fixtures = load_fixtures()
        counts = {
            "items": db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"],
            "bom_lines": db.execute("SELECT COUNT(*) AS c FROM bom_lines").fetchone()["c"],
            "fixture_rows": sum(f["meta"]["rows"] for f in current_fixtures.values()),
            "logs": db.execute("SELECT COUNT(*) AS c FROM api_logs").fetchone()["c"],
        }
        return render_template(
            "index.html",
            service=SERVICE_NAME,
            version=SERVICE_VERSION,
            api_prefix=API_PREFIX,
            counts=counts,
        )

    @app.route("/api")
    def api_docs():
        return render_template(
            "api.html", service=SERVICE_NAME, api_prefix=API_PREFIX
        )

    @app.route("/data")
    def data_browser():
        db = get_db()
        rows = db.execute(
            "SELECT i.*, "
            "(SELECT revision_id FROM item_revisions WHERE item_uid=i.uid ORDER BY sequence DESC LIMIT 1) AS revision, "
            "(SELECT COUNT(*) FROM bom_lines bl JOIN bom_headers bh ON bl.bom_uid=bh.uid WHERE bh.item_uid=i.uid) AS bom_count "
            "FROM items i ORDER BY i.item_id"
        ).fetchall()
        return render_template(
            "data.html", service=SERVICE_NAME, items=rows, api_prefix=API_PREFIX,
            fixtures=[f["meta"] for f in load_fixtures().values()],
            editable=bool(admin_token),
        )

    @app.route("/data/fixture/<name>")
    def data_fixture(name):
        fixture, err = get_fixture_or_error(name)
        if err is not None:
            return render_template("error.html", service=SERVICE_NAME, message="BOM 数据集不存在: " + name), 404
        q = (request.args.get("q") or "").strip().lower()
        try:
            page = max(1, int(request.args.get("page", 1)))
            per_page = max(20, min(200, int(request.args.get("per_page", 100))))
        except (TypeError, ValueError):
            page, per_page = 1, 100
        rows = [enrich_fixture_row(row, fixture) for row in fixture["rows"]]
        if q:
            rows = [row for row in rows if q in " ".join(str(value) for value in row.values()).lower()]
        total = len(rows)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        shown = rows[(page - 1) * per_page : page * per_page]
        return render_template(
            "fixture_data.html", service=SERVICE_NAME, api_prefix=API_PREFIX,
            fixture=fixture["meta"], rows=shown, total=total, q=request.args.get("q", ""),
            page=page, pages=pages, per_page=per_page, editable=bool(admin_token),
            editable_fields=fixture_editable_fields,
        )

    @app.route("/data/item/<uid>")
    def data_item(uid):
        row = item_row(uid)
        if row is None:
            return render_template("error.html", service=SERVICE_NAME, message="物料不存在: " + uid), 404
        lines = build_tree(uid, -1, set())
        return render_template(
            "data_item.html",
            service=SERVICE_NAME,
            item=serialize_item(row),
            bom_lines=lines,
            api_prefix=API_PREFIX,
            all_items=[serialize_item(r) for r in get_db().execute("SELECT * FROM items ORDER BY item_id").fetchall()],
            editable=bool(admin_token),
        )

    @app.route("/logs")
    def logs_page():
        return render_template("logs.html", service=SERVICE_NAME)

    @app.route("/logs/table")
    def logs_table():
        path = request.args.get("path", "").strip()
        status = request.args.get("status", "").strip()
        method = request.args.get("method", "").strip()
        page = max(1, request.args.get("page", 1, type=int) or 1)
        per_page = 25
        where = ["is_api = 1"]
        params = []
        if path:
            where.append("path LIKE ?")
            params.append("%" + path + "%")
        if status:
            where.append("status = ?")
            params.append(int(status))
        if method:
            where.append("method = ?")
            params.append(method.upper())
        sql_where = " AND ".join(where)
        db = get_db()
        total = db.execute(
            "SELECT COUNT(*) AS c FROM api_logs WHERE " + sql_where, params
        ).fetchone()["c"]
        pages = max(1, -(-total // per_page))
        page = min(page, pages)
        rows = db.execute(
            "SELECT * FROM api_logs WHERE "
            + sql_where
            + " ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
        return render_template(
            "logs_table.html",
            rows=rows,
            page=page,
            pages=pages,
            total=total,
            path=path,
            status=status,
            method=method,
        )

    @app.route("/logs/clear", methods=["POST"])
    def logs_clear():
        db = get_db()
        db.execute("DELETE FROM api_logs")
        db.commit()
        return api_ok({"cleared": True})

    # -------------------------------------------------------------- errors
    @app.errorhandler(404)
    def not_found(exc):
        if request.path.startswith(API_PREFIX):
            return api_err(404, "Endpoint not found: " + request.path)
        return render_template("error.html", service=SERVICE_NAME, message="页面不存在"), 404

    @app.errorhandler(405)
    def method_not_allowed(exc):
        if request.path.startswith(API_PREFIX):
            return api_err(405, "Method not allowed: " + request.method + " " + request.path)
        return Response("Method Not Allowed", status=405)

    @app.errorhandler(400)
    def bad_request(exc):
        if request.path.startswith(API_PREFIX):
            return api_err(400, "Bad request: " + str(exc.description or exc))
        return Response("Bad Request", status=400)

    @app.errorhandler(Exception)
    def unhandled(exc):
        app.logger.exception("unhandled error")
        if request.path.startswith(API_PREFIX):
            return api_err(500, "Internal server error: " + str(exc))
        return render_template("error.html", service=SERVICE_NAME, message="服务器内部错误"), 500

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("MOCKTC_PORT", "18120"))
    app.run(host="127.0.0.1", port=port, debug=False)
