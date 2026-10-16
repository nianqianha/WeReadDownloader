"""Missing base settings should give an actionable authentication message."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "weread/weread_core.py"
spec = importlib.util.spec_from_file_location("missing_credentials_core", path)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class MissingCredentialsMessageTests(unittest.TestCase):
    def test_missing_base_explains_login_instead_of_download_progress(self):
        message = core.friendly_error("FAILED RuntimeError Credential file not found: synthetic/settings.lua")
        self.assertIn("登录", message)
        self.assertNotIn("章节编号", message)
        self.assertNotIn("已有进度", message)

    def test_other_errors_keep_their_existing_mapping(self):
        self.assertEqual(core.friendly_error("synthetic unknown error"), core.FALLBACK_ZH)
        self.assertIn("登录", core.friendly_error("Private credentials need an API key and WeRead session"))


if __name__ == "__main__":
    unittest.main()
