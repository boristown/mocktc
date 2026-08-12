import json
import os
import sys
import tempfile
import unittest

_TMPDIR = tempfile.mkdtemp(prefix="mocktc-fixture-api-")
os.environ["MOCKTC_DATA_DIR"] = _TMPDIR
os.environ["MOCKTC_DB_PATH"] = os.path.join(_TMPDIR, "test.db")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mocktc_app"))
import app as app_module

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "mocktc_app", "fixtures", "20260808-bom1-2.json"
)
FIXTURE_NAME = "20260808-bom1-2.json"


class MockTcFixtureApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.client = cls.app.test_client()
        with open(FIXTURE_PATH, "rb") as fh:
            cls.fixture_bytes = fh.read()
        cls.rows = json.loads(cls.fixture_bytes)

    def get_json(self, path):
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return json.loads(resp.get_data(as_text=True))

    def test_health_reports_fixtures(self):
        data = self.get_json("/tc/v1/health")
        self.assertEqual(data["data"]["status"], "up")
        self.assertGreaterEqual(data["data"]["fixtures"]["total"], 1)
        self.assertIn(FIXTURE_NAME, data["data"]["fixtures"]["names"])

    def test_list_fixtures(self):
        data = self.get_json("/tc/v1/fixtures")
        self.assertEqual(data["status"], 200)
        self.assertGreaterEqual(data["data"]["total"], 1)
        meta = data["data"]["fixtures"][0]
        self.assertEqual(meta["name"], FIXTURE_NAME)
        self.assertEqual(meta["rows"], 3316)
        self.assertEqual(meta["item_id"], "LITHO-001")
        self.assertIn("part_id", meta["fields"])
        self.assertIn("quantity", meta["fields"])

    def test_full_read_structured(self):
        data = self.get_json("/tc/v1/fixtures/" + FIXTURE_NAME)
        self.assertEqual(data["data"]["total"], 3316)
        self.assertEqual(len(data["data"]["items"]), 3316)
        self.assertEqual(data["data"]["fixture"]["name"], FIXTURE_NAME)
        self.assertEqual(data["data"]["items"][0]["part_id"], "LITHO-001")
        self.assertEqual(data["data"]["items"][0]["bom_level"], 0)

    def test_full_read_raw(self):
        resp = self.client.get("/tc/v1/fixtures/" + FIXTURE_NAME + "?raw=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "application/json")
        self.assertEqual(resp.data, self.fixture_bytes)

    def test_query_part_id_exact(self):
        data = self.get_json(
            "/tc/v1/fixtures/" + FIXTURE_NAME + "/query?part_id=S01&exact=1"
        )
        self.assertEqual(data["data"]["total"], 1)
        item = data["data"]["items"][0]
        self.assertEqual(item["part_id"], "S01")
        self.assertEqual(item["part_name"], "光源系统")
        self.assertEqual(item["parent_id"], "LITHO-001")
        self.assertEqual(item["parent_name"], "光刻机整机")

    def test_query_part_id_substring(self):
        data = self.get_json("/tc/v1/fixtures/" + FIXTURE_NAME + "/query?part_id=00")
        self.assertEqual(data["data"]["total"], 157)
        self.assertTrue(all("00" in it["part_id"] for it in data["data"]["items"]))

    def test_query_part_name_and_q(self):
        data = self.get_json(
            "/tc/v1/fixtures/" + FIXTURE_NAME + "/query?part_name=激光器"
        )
        self.assertGreaterEqual(data["data"]["total"], 1)
        self.assertTrue(
            all("激光器" in it["part_name"] for it in data["data"]["items"])
        )
        by_q = self.get_json("/tc/v1/fixtures/" + FIXTURE_NAME + "/query?q=光源")
        self.assertGreaterEqual(by_q["data"]["total"], 1)
        self.assertTrue(
            all(
                "光源" in it["part_id"] or "光源" in it["part_name"]
                for it in by_q["data"]["items"]
            )
        )

    def test_query_bom_level(self):
        data = self.get_json("/tc/v1/fixtures/" + FIXTURE_NAME + "/query?bom_level=1")
        self.assertEqual(data["data"]["total"], 15)
        self.assertTrue(all(it["bom_level"] == 1 for it in data["data"]["items"]))

    def test_query_parent_id(self):
        data = self.get_json(
            "/tc/v1/fixtures/" + FIXTURE_NAME + "/query?parent_id=LITHO-001"
        )
        self.assertEqual(data["data"]["total"], 15)
        self.assertTrue(
            all(it["parent_id"] == "LITHO-001" for it in data["data"]["items"])
        )

    def test_query_child_uid_and_parent_uid(self):
        s01 = next(r for r in self.rows if r.get("part_id") == "S01")
        data = self.get_json(
            "/tc/v1/fixtures/" + FIXTURE_NAME + "/query?child_uid=" + s01["child_uid"]
        )
        self.assertEqual(data["data"]["total"], 1)
        self.assertEqual(data["data"]["items"][0]["part_id"], "S01")
        by_parent = self.get_json(
            "/tc/v1/fixtures/" + FIXTURE_NAME + "/query?parent_uid=" + s01["parent_uid"]
        )
        self.assertEqual(by_parent["data"]["total"], 15)

    def test_query_pagination(self):
        data = self.get_json(
            "/tc/v1/fixtures/" + FIXTURE_NAME + "/query?limit=5&offset=0"
        )
        self.assertEqual(data["data"]["total"], 3316)
        self.assertEqual(data["data"]["limit"], 5)
        self.assertEqual(len(data["data"]["items"]), 5)

    def test_query_revision(self):
        data = self.get_json(
            "/tc/v1/fixtures/" + FIXTURE_NAME + "/query?revision_id=A&bom_level=0"
        )
        self.assertEqual(data["data"]["total"], 1)

    def test_query_bad_limit(self):
        resp = self.client.get("/tc/v1/fixtures/" + FIXTURE_NAME + "/query?limit=abc")
        self.assertEqual(resp.status_code, 400)
        body = json.loads(resp.get_data(as_text=True))
        self.assertEqual(body["status"], 400)

    def test_query_bad_bom_level(self):
        resp = self.client.get(
            "/tc/v1/fixtures/" + FIXTURE_NAME + "/query?bom_level=x"
        )
        self.assertEqual(resp.status_code, 400)
        body = json.loads(resp.get_data(as_text=True))
        self.assertEqual(body["status"], 400)

    def test_material_detail(self):
        data = self.get_json("/tc/v1/fixtures/" + FIXTURE_NAME + "/materials/S01")
        self.assertEqual(data["data"]["part_id"], "S01")
        self.assertEqual(data["data"]["part_name"], "光源系统")
        self.assertEqual(data["data"]["items"][0]["parent_id"], "LITHO-001")

    def test_material_detail_not_found(self):
        resp = self.client.get(
            "/tc/v1/fixtures/" + FIXTURE_NAME + "/materials/NOPE-999"
        )
        self.assertEqual(resp.status_code, 404)
        body = json.loads(resp.get_data(as_text=True))
        self.assertEqual(body["status"], 404)
        self.assertIn("NOPE-999", body["message"])

    def test_fixture_not_found(self):
        for path in [
            "/tc/v1/fixtures/nope.json",
            "/tc/v1/fixtures/nope.json/query?part_id=S01",
            "/tc/v1/fixtures/nope.json/materials/S01",
        ]:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 404, path)
            body = json.loads(resp.get_data(as_text=True))
            self.assertEqual(body["status"], 404)

    def test_path_traversal_blocked(self):
        attempts = [
            "/tc/v1/fixtures/..%2F..%2Fetc%2Fpasswd",
            "/tc/v1/fixtures/..%2F..%2Fetc%2Fpasswd?raw=1",
            "/tc/v1/fixtures/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "/tc/v1/fixtures/..%5C..%5Cetc%5Cpasswd",
            "/tc/v1/fixtures/.%2e/%2e%2e/etc/passwd",
        ]
        for path in attempts:
            resp = self.client.get(path)
            self.assertIn(resp.status_code, (400, 404), path)
            body = resp.get_data(as_text=True)
            self.assertNotIn("root:", body)
            self.assertNotIn("daemon:", body)

    def test_legacy_bom_raw_unchanged(self):
        for path in [
            "/tc/v1/items/item-litho-001/bom",
            "/tc/v1/items/item-litho-001/bom/expand",
            "/tc/v1/structures/item-litho-001",
        ]:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            self.assertEqual(resp.data, self.fixture_bytes, path)

    def test_fixture_endpoints_read_only(self):
        for path in ["/tc/v1/fixtures", "/tc/v1/fixtures/" + FIXTURE_NAME]:
            resp = self.client.post(path, data="{}", content_type="application/json")
            self.assertEqual(resp.status_code, 405, path)


DIFF_FIXTURE_NAME = "20260810-sap-alignment-diff-G100000013.json"
DIFF_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "mocktc_app", "fixtures", DIFF_FIXTURE_NAME
)

# ECC 只读基线（CS_BOM_EXPL_MAT_V2 未限定工厂完整展开 G100000013，BOM 00000011/01，
# 抬头基本数量 1000.000 EA；共 18 行组件、最大 5 层，AUSCH 为部件损耗率）。
SAP_BASELINE_COMPONENTS = [
    {"level": 1, "parent": "G100000013", "bom": "00000011", "item_no": "0010", "part_id": "G200000014", "part_name": "2025零部件4", "quantity": "1000.000", "unit": "ST", "scrap_rate": "10.00"},
    {"level": 2, "parent": "G200000014", "bom": "00000012", "item_no": "0010", "part_id": "G200000015", "part_name": "2025零部件5", "quantity": "3.000", "unit": "ST", "scrap_rate": ""},
    {"level": 3, "parent": "G200000015", "bom": "00000013", "item_no": "0010", "part_id": "G200000016", "part_name": "2025零部件6", "quantity": "4000.000", "unit": "ST", "scrap_rate": ""},
    {"level": 4, "parent": "G200000016", "bom": "00000014", "item_no": "0010", "part_id": "G200000017", "part_name": "2025零部件7", "quantity": "1000.000", "unit": "ST", "scrap_rate": ""},
    {"level": 5, "parent": "G200000017", "bom": "00000015", "item_no": "0010", "part_id": "G300000105", "part_name": "2025原材料5", "quantity": "6000.000", "unit": "EA", "scrap_rate": "100.00"},
    {"level": 5, "parent": "G200000017", "bom": "00000015", "item_no": "0020", "part_id": "G300000102", "part_name": "2025原材料2", "quantity": "1000.000", "unit": "EA", "scrap_rate": ""},
    {"level": 5, "parent": "G200000017", "bom": "00000015", "item_no": "0030", "part_id": "G300000101", "part_name": "2025原材料1", "quantity": "1000.000", "unit": "EA", "scrap_rate": ""},
    {"level": 4, "parent": "G200000016", "bom": "00000014", "item_no": "0020", "part_id": "G300000106", "part_name": "2025原材料6", "quantity": "2000.000", "unit": "EA", "scrap_rate": "5.00"},
    {"level": 1, "parent": "G100000013", "bom": "00000011", "item_no": "0030", "part_id": "G200000019", "part_name": "2025零部件9", "quantity": "1000.000", "unit": "ST", "scrap_rate": ""},
    {"level": 2, "parent": "G200000019", "bom": "00000017", "item_no": "0010", "part_id": "G300000109", "part_name": "2025原材料9", "quantity": "1000.000", "unit": "EA", "scrap_rate": ""},
    {"level": 2, "parent": "G200000019", "bom": "00000017", "item_no": "0020", "part_id": "G300000110", "part_name": "2025原材料10", "quantity": "1000.000", "unit": "EA", "scrap_rate": ""},
    {"level": 2, "parent": "G200000019", "bom": "00000017", "item_no": "0030", "part_id": "G300000111", "part_name": "2025原材料11", "quantity": "1000.000", "unit": "EA", "scrap_rate": ""},
    {"level": 1, "parent": "G100000013", "bom": "00000011", "item_no": "0040", "part_id": "G200000020", "part_name": "2025零部件10", "quantity": "2000.000", "unit": "ST", "scrap_rate": ""},
    {"level": 1, "parent": "G100000013", "bom": "00000011", "item_no": "0050", "part_id": "G300000115", "part_name": "2025原材料15", "quantity": "3000.000", "unit": "EA", "scrap_rate": ""},
    {"level": 1, "parent": "G100000013", "bom": "00000011", "item_no": "0060", "part_id": "G300000116", "part_name": "2025原材料16", "quantity": "2000.000", "unit": "EA", "scrap_rate": "20.00"},
    {"level": 1, "parent": "G100000013", "bom": "00000011", "item_no": "0070", "part_id": "G200000018", "part_name": "2025零部件8", "quantity": "8000.000", "unit": "ST", "scrap_rate": ""},
    {"level": 2, "parent": "G200000018", "bom": "00000016", "item_no": "0010", "part_id": "G300000107", "part_name": "2025原材料7", "quantity": "9000.000", "unit": "EA", "scrap_rate": ""},
    {"level": 2, "parent": "G200000018", "bom": "00000016", "item_no": "0020", "part_id": "G300000108", "part_name": "2025原材料8", "quantity": "1000.000", "unit": "EA", "scrap_rate": ""},
]

# TC fixture = 根(基本数量 1000) + 18 行 ECC 对齐组件 + 1 行 TC 新增
# （G200000020 下的 G300000108，ECC 中 G200000020 无 BOM）共 20 行。
EXPECTED_DIFF_ROWS = 20

# TC 相对 ECC 预置的确定性差异：
#  - 数量+损耗差异：G200000019 1000.000/无 -> 1200.000/5.00；
#                    G200000020 2000.000/无 -> 2500.000/3.00；
#                    G300000115 3000.000/无 -> 3500.000/2.50；
#                    G300000116 2000.000/20.00 -> 2100.000/15.00
#  - 新增组件：G200000020 下新增 G300000108（1500.000 EA，损耗 4.00）
#  - 其余 13 行与 ECC 基线完全一致（含损耗 10.00/100.00/5.00 的三行）
TC_QUANTITY_DIFFS = {
    "G200000019": "1200.000",
    "G200000020": "2500.000",
    "G300000115": "3500.000",
    "G300000116": "2100.000",
}
TC_SCRAP_DIFFS = {
    "G200000019": "5.00",
    "G200000020": "3.00",
    "G300000115": "2.50",
    "G300000116": "15.00",
}
TC_ADDED_UNDER_G200000020 = {
    "part_id": "G300000108", "part_name": "2025原材料8", "bom_level": 2,
    "bom_number": "00000018", "item_no": "0010", "quantity": "1500.000",
    "unit": "EA", "scrap_rate": "4.00", "parent_id": "G200000020",
}


class SapAlignmentDiffFixtureApiTestCase(unittest.TestCase):
    """20260810-sap-alignment-diff-G100000013.json 的 ECC/TC 差异测试专用断言。"""

    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.client = cls.app.test_client()
        with open(DIFF_FIXTURE_PATH, "rb") as fh:
            cls.fixture_bytes = fh.read()

    def get_json(self, path):
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return json.loads(resp.get_data(as_text=True))

    def load_rows(self):
        return self.get_json("/tc/v1/fixtures/" + DIFF_FIXTURE_NAME)["data"]["items"]

    def test_fixture_listed_with_metadata(self):
        data = self.get_json("/tc/v1/fixtures")
        names = [f["name"] for f in data["data"]["fixtures"]]
        self.assertIn(DIFF_FIXTURE_NAME, names)
        meta = next(f for f in data["data"]["fixtures"] if f["name"] == DIFF_FIXTURE_NAME)
        self.assertEqual(meta["rows"], EXPECTED_DIFF_ROWS)
        self.assertEqual(meta["item_id"], "G100000013")
        self.assertEqual(meta["revision"], "01")
        for field in ("bom_level", "parent_uid", "child_uid", "part_id", "part_name",
                      "revision_id", "quantity", "unit", "bom_number", "bom_alt",
                      "item_category", "item_no", "scrap_rate", "plant", "usage"):
            self.assertIn(field, meta["fields"])

    def test_raw_read_returns_exact_bytes(self):
        resp = self.client.get("/tc/v1/fixtures/" + DIFF_FIXTURE_NAME + "?raw=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "application/json")
        self.assertEqual(resp.data, self.fixture_bytes)

    def test_total_row_count(self):
        data = self.get_json("/tc/v1/fixtures/" + DIFF_FIXTURE_NAME)
        self.assertEqual(data["data"]["total"], EXPECTED_DIFF_ROWS)
        self.assertEqual(len(data["data"]["items"]), EXPECTED_DIFF_ROWS)
        components = [r for r in data["data"]["items"] if r.get("bom_level") > 0]
        self.assertEqual(len(components), EXPECTED_DIFF_ROWS - 1)
        self.assertEqual(max(r["bom_level"] for r in components), 5)

    def test_root_hierarchy_and_parent_child_relations(self):
        rows = self.load_rows()
        root = next(r for r in rows if r.get("bom_level") == 0)
        self.assertEqual(root["part_id"], "G100000013")
        self.assertEqual(root["parent_uid"], "")
        self.assertEqual(root["bom_number"], "00000011")
        self.assertEqual(root["bom_alt"], "01")
        self.assertEqual(root["plant"], "G001")
        self.assertEqual(root["usage"], 1)
        self.assertEqual(root["revision_id"], "01")
        self.assertEqual(root["quantity"], "1000")
        self.assertEqual(root["unit"], "EA")
        children = [r for r in rows if r.get("bom_level") == 1]
        self.assertEqual(len(children), 6)
        self.assertTrue(all(r["parent_uid"] == root["child_uid"] for r in children))
        known_uids = {r["child_uid"] for r in rows}
        self.assertTrue(all(not r["parent_uid"] or r["parent_uid"] in known_uids for r in rows))
        child_uids = [r["child_uid"] for r in rows]
        self.assertEqual(len(child_uids), len(set(child_uids)))

    def test_query_parent_child(self):
        rows = self.load_rows()
        root = next(r for r in rows if r.get("bom_level") == 0)
        by_parent = self.get_json(
            "/tc/v1/fixtures/" + DIFF_FIXTURE_NAME + "/query?parent_uid=" + root["child_uid"]
        )
        self.assertEqual(by_parent["data"]["total"], 6)
        self.assertTrue(
            all(it["parent_id"] == "G100000013" for it in by_parent["data"]["items"])
        )
        changed = next(r for r in rows if r["part_id"] == "G200000019")
        by_child = self.get_json(
            "/tc/v1/fixtures/" + DIFF_FIXTURE_NAME + "/query?child_uid=" + changed["child_uid"]
        )
        self.assertEqual(by_child["data"]["total"], 1)
        item = by_child["data"]["items"][0]
        self.assertEqual(item["part_id"], "G200000019")
        self.assertEqual(item["parent_id"], "G100000013")
        self.assertEqual(item["parent_name"], "2025总成13")

    def test_material_detail(self):
        data = self.get_json("/tc/v1/fixtures/" + DIFF_FIXTURE_NAME + "/materials/G300000108")
        self.assertEqual(data["data"]["part_name"], "2025原材料8")
        items = data["data"]["items"]
        self.assertEqual(len(items), 2)
        parents = {it["parent_id"] for it in items}
        self.assertEqual(parents, {"G200000020", "G200000018"})
        under_020 = next(it for it in items if it["parent_id"] == "G200000020")
        self.assertEqual(under_020["quantity"], "1500.000")
        self.assertEqual(under_020["scrap_rate"], "4.00")

    def test_predefined_diffs(self):
        rows = self.load_rows()
        by_part = {r["part_id"]: r for r in rows}
        uid_to_part = {r["child_uid"]: r["part_id"] for r in rows}
        # 所有 ECC 基线组件都存在（无删除）
        for comp in SAP_BASELINE_COMPONENTS:
            self.assertIn(comp["part_id"], by_part, comp["part_id"])
        # 对齐行逐字段一致；差异行仅数量/损耗不同
        for comp in SAP_BASELINE_COMPONENTS:
            row = by_part[comp["part_id"]]
            self.assertEqual(row["part_name"], comp["part_name"])
            self.assertEqual(row["bom_level"], comp["level"])
            self.assertEqual(uid_to_part[row["parent_uid"]], comp["parent"])
            self.assertEqual(row["bom_number"], comp["bom"])
            self.assertEqual(row["item_no"], comp["item_no"])
            self.assertEqual(row["unit"], comp["unit"])
            self.assertEqual(row["item_category"], "L")
            self.assertEqual(row["revision_id"], "01")
            self.assertEqual(row["quantity"],
                             TC_QUANTITY_DIFFS.get(comp["part_id"], comp["quantity"]))
            self.assertEqual(row["scrap_rate"],
                             TC_SCRAP_DIFFS.get(comp["part_id"], comp["scrap_rate"]))
        # TC 新增：G200000020 下新增 G300000108 原材料（ECC 中该物料只在 G200000018 下）
        added = by_part[TC_ADDED_UNDER_G200000020["part_id"]]
        added_rows = [r for r in rows if r["part_id"] == "G300000108"]
        self.assertEqual(len(added_rows), 2)
        new_row = next(r for r in added_rows
                       if uid_to_part[r["parent_uid"]] == "G200000020")
        for key, value in TC_ADDED_UNDER_G200000020.items():
            if key == "parent_id":
                self.assertEqual(uid_to_part[new_row["parent_uid"]], value)
            elif key == "bom_level":
                self.assertEqual(new_row["bom_level"], value)
            else:
                self.assertEqual(new_row[key], value)
        # part_id 集合与 ECC 基线完全一致（同一物料可以出现在多位置，不重复计数）
        sap_ids = {c["part_id"] for c in SAP_BASELINE_COMPONENTS}
        self.assertEqual(set(by_part) - {"G100000013"}, sap_ids)

    def test_extended_fields_preserved(self):
        data = self.get_json("/tc/v1/fixtures/" + DIFF_FIXTURE_NAME)
        for row in data["data"]["items"]:
            for key in ("bom_number", "bom_alt", "item_category", "item_no",
                        "revision_id", "quantity", "unit", "scrap_rate", "plant", "usage"):
                self.assertIn(key, row, row)
        for row in data["data"]["items"]:
            self.assertEqual(row["bom_alt"], "01")
            self.assertEqual(row["revision_id"], "01")
            self.assertEqual(row["plant"], "G001")
            self.assertEqual(row["usage"], 1)
        # 查询接口同样保留扩展字段并补充父级信息
        items = self.get_json(
            "/tc/v1/fixtures/" + DIFF_FIXTURE_NAME + "/query?part_id=G200000014&exact=1"
        )["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quantity"], "1000.000")
        self.assertEqual(items[0]["unit"], "ST")
        self.assertEqual(items[0]["item_category"], "L")
        self.assertEqual(items[0]["item_no"], "0010")
        self.assertEqual(items[0]["bom_number"], "00000011")
        self.assertEqual(items[0]["bom_alt"], "01")
        self.assertEqual(items[0]["scrap_rate"], "10.00")
        self.assertEqual(items[0]["parent_id"], "G100000013")


if __name__ == "__main__":
    unittest.main()
