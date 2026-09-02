from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock

from deployment import protected_launcher


ROOT = Path(__file__).resolve().parent.parent


class ProtectedMockTCReleaseTests(unittest.TestCase):
    def test_native_release_keeps_mutable_state_outside_image(self):
        launcher = (ROOT / "deployment/protected_launcher.py").read_text("utf-8")
        builder = (ROOT / "scripts/build-protected-release.sh").read_text("utf-8")
        self.assertIn("/var/lib/xiaogang/mocktc/fixtures", launcher)
        self.assertIn("MOCKTC_FIXTURE_DIR", launcher)
        self.assertIn("xg-mocktc", builder)
        self.assertIn("xg-mocktc-fixture-sync", builder)
        self.assertIn("sync_production_fixtures.py", builder)
        self.assertIn('"${OUTPUT_DIR}/maintenance/mocktc-fixture-sync"', builder)
        for suffix in ("*.py", "*.pyc", "*.pyo", "*.map"):
            self.assertIn(suffix, builder)
        self.assertNotIn("mocktc.db", builder)

    def test_existing_persistent_state_does_not_resurrect_bundled_fixture(self):
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "fixtures"
            target.mkdir()
            database = Path(tempdir) / "mocktc.db"
            database.touch()
            with mock.patch.dict(os.environ, {
                "MOCKTC_FIXTURE_DIR": str(target), "MOCKTC_DB_PATH": str(database),
            }), mock.patch.object(protected_launcher.sys, "argv", [str(ROOT / "xg-mocktc")]):
                protected_launcher.prepare_persistent_fixtures()
            self.assertEqual(list(target.glob("*.json")), [])
            self.assertTrue((target / ".bundled-fixtures-initialized").is_file())

    def test_fresh_state_seeds_once_then_respects_deletion(self):
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "fixtures"
            environment = {
                "MOCKTC_FIXTURE_DIR": str(target),
                "MOCKTC_DB_PATH": str(Path(tempdir) / "mocktc.db"),
            }
            with mock.patch.dict(os.environ, environment), \
                    mock.patch.object(protected_launcher.sys, "argv", [str(ROOT / "xg-mocktc")]):
                protected_launcher.prepare_persistent_fixtures()
                seeded = sorted(target.glob("*.json"))
                self.assertGreaterEqual(len(seeded), 2)
                seeded[0].unlink()
                protected_launcher.prepare_persistent_fixtures()
                self.assertFalse(seeded[0].exists())


if __name__ == "__main__":
    unittest.main()
