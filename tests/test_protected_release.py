from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent


class ProtectedMockTCReleaseTests(unittest.TestCase):
    def test_native_release_keeps_mutable_state_outside_image(self):
        launcher = (ROOT / "deployment/protected_launcher.py").read_text("utf-8")
        builder = (ROOT / "scripts/build-protected-release.sh").read_text("utf-8")
        self.assertIn("/var/lib/xiaogang/mocktc/fixtures", launcher)
        self.assertIn("MOCKTC_FIXTURE_DIR", launcher)
        self.assertIn("xg-mocktc", builder)
        for suffix in ("*.py", "*.pyc", "*.pyo", "*.map"):
            self.assertIn(suffix, builder)
        self.assertNotIn("mocktc.db", builder)


if __name__ == "__main__":
    unittest.main()
