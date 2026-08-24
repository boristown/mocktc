import json
import os
import sys
import tempfile
import unittest

_TMPDIR = tempfile.mkdtemp(prefix="mocktc-test-")
os.environ["MOCKTC_DATA_DIR"] = _TMPDIR
os.environ["MOCKTC_DB_PATH"] = os.path.join(_TMPDIR, "test.db")
os.environ["MOCKTC_ADMIN_TOKEN"] = "test-admin-token"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mocktc_app"))
import app as app_module

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "mocktc_app", "fixtures", "20260808-bom1-2.json"
)


class MockTcTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.client = cls.app.test_client()

    def get_json(self, path):
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return json.loads(resp.get_data(as_text=True))

    def test_health(self):
        data = self.get_json("/tc/v1/health")
        self.assertEqual(data["status"], 200)
        self.assertEqual(data["data"]["status"], "up")

    def test_list_items(self):
        data = self.get_json("/tc/v1/items")
        self.assertGreaterEqual(data["data"]["total"], 20)
        self.assertTrue(data["data"]["items"])

    def test_search_items(self):
        data = self.get_json("/tc/v1/items?q=变速箱")
        self.assertGreaterEqual(data["data"]["total"], 1)
        ids = [i["item_id"] for i in data["data"]["items"]]
        self.assertIn("P-1000", ids)

    def test_item_detail(self):
        data = self.get_json("/tc/v1/items/item-p1000")
        self.assertEqual(data["data"]["item_id"], "P-1000")
        self.assertEqual(data["data"]["revision"], "A")

    def test_item_not_found(self):
        resp = self.client.get("/tc/v1/items/item-nope")
        self.assertEqual(resp.status_code, 404)
        data = json.loads(resp.get_data(as_text=True))
        self.assertEqual(data["status"], 404)

    def test_revisions(self):
        data = self.get_json("/tc/v1/items/item-p1000/revisions")
        self.assertEqual(len(data["data"]["revisions"]), 1)

    def test_bom_single_level(self):
        data = self.get_json("/tc/v1/items/item-p1000/bom")
        lines = data["data"]["bom_lines"]
        self.assertEqual(len(lines), 4)
        for line in lines:
            self.assertEqual(line["children"], [])

    def test_bom_expand(self):
        data = self.get_json("/tc/v1/items/item-p1000/bom/expand")
        lines = data["data"]["bom_lines"]
        child_ids = [l["child_item"]["item_id"] for l in lines]
        self.assertIn("SA-1001", child_ids)
        sa1001 = [l for l in lines if l["child_item"]["item_id"] == "SA-1001"][0]
        self.assertTrue(sa1001["children"])

    def test_bom_depth(self):
        data = self.get_json("/tc/v1/items/item-p1000/bom?depth=1")
        lines = data["data"]["bom_lines"]
        sa1001 = [l for l in lines if l["child_item"]["item_id"] == "SA-1001"][0]
        self.assertTrue(sa1001["children"])
        grand = sa1001["children"][0]["children"]
        self.assertTrue(all(c["children"] == [] for c in grand))

    def test_bomline_detail_and_children(self):
        data = self.get_json("/tc/v1/bomlines/bl-p1000-1")
        self.assertEqual(data["data"]["child_item"]["item_id"], "SA-1001")
        children = self.get_json("/tc/v1/bomlines/bl-p1000-1/children")
        self.assertEqual(len(children["data"]["children"]), 3)
        leaf = self.get_json("/tc/v1/bomlines/bl-sa1001-1/children")
        self.assertEqual(leaf["data"]["children"], [])

    def test_structure_alias(self):
        data = self.get_json("/tc/v1/structures/item-p1000?depth=-1")
        self.assertTrue(data["data"]["bom_lines"])

    def test_fixture_item_registered(self):
        data = self.get_json("/tc/v1/items?item_id=LITHO-001")
        self.assertGreaterEqual(data["data"]["total"], 1)
        detail = self.get_json("/tc/v1/items/item-litho-001")
        self.assertEqual(detail["data"]["item_id"], "LITHO-001")

    def test_fixture_bom_exact(self):
        with open(FIXTURE_PATH, "rb") as fh:
            expected = fh.read()
        for path in [
            "/tc/v1/items/item-litho-001/bom",
            "/tc/v1/items/item-litho-001/bom/expand",
            "/tc/v1/structures/item-litho-001",
        ]:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            self.assertEqual(resp.content_type, "application/json", path)
            self.assertEqual(resp.data, expected, path)
            data = json.loads(resp.get_data(as_text=True))
            self.assertIsInstance(data, list, path)
            self.assertEqual(data[0]["part_id"], "LITHO-001", path)
            self.assertGreater(len(data), 3000, path)

    def test_create_item(self):
        resp = self.client.post(
            "/tc/v1/items",
            data=json.dumps({"item_id": "TEST-001", "item_name": "测试零件", "item_type": "Part"}),
            content_type="application/json",
            headers={"X-MockTC-Admin-Token": "test-admin-token"},
        )
        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.get_data(as_text=True))
        self.assertEqual(data["data"]["item_id"], "TEST-001")
        dup = self.client.post(
            "/tc/v1/items",
            data=json.dumps({"item_id": "TEST-001", "item_name": "重复"}),
            content_type="application/json",
            headers={"X-MockTC-Admin-Token": "test-admin-token"},
        )
        self.assertEqual(dup.status_code, 409)

    def test_api_logging(self):
        self.client.get("/tc/v1/health")
        resp = self.client.get("/logs/table?page=1")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("共", html)
        self.assertIn("/tc/v1/health", html)


if __name__ == "__main__":
    unittest.main()
