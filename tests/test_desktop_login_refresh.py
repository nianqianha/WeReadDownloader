"""Verify the successful QR callback without opening a login window."""
import ast
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class LoginRefreshTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / "weread/desktop_app.py"
        sys.path.insert(0, str(source.parent))
        app = next(n for n in ast.parse(source.read_text(encoding="utf-8")).body
                   if isinstance(n, ast.ClassDef) and n.name == "DesktopApp")
        method = next(n for n in app.body if isinstance(n, ast.FunctionDef) and n.name == "_finish_login")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / "settings.lua"
        self.save = Mock()
        namespace = {"weread_login": SimpleNamespace(save_credentials=self.save),
                     "_settings_path": lambda: self.base}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     str(source), "exec"), namespace)
        self.finish = namespace["_finish_login"]
        self.app = SimpleNamespace(
            login_qr_status=Mock(), auth_badge=Mock(), status=Mock(), GREEN="green",
            _set_login_identity=Mock(), _refresh_logout_button=Mock(),
            _refresh_creds_panel=Mock(), close_login=Mock())
        self.credentials = {"api_key": "wrk-fake-login-refresh", "cookies": {"wr_skey": "fake-session"},
                            "account": {"name": "Synthetic account"}}

    def test_success_refreshes_credentials_after_persistence(self):
        self.save.side_effect = lambda value: self.assertTrue(self.base.is_file())
        self.finish(self.app, self.credentials)
        self.save.assert_called_once_with(self.credentials)
        self.app._refresh_creds_panel.assert_called_once_with()
        self.app.close_login.assert_called_once_with()

    def test_session_save_failure_keeps_login_open_without_a_success_refresh(self):
        self.save.side_effect = OSError("synthetic write failure")
        self.finish(self.app, self.credentials)
        self.app._refresh_creds_panel.assert_not_called()
        self.app.close_login.assert_not_called()
        self.assertIn("保存凭证失败", self.app.login_qr_status.configure.call_args.kwargs["text"])


if __name__ == "__main__":
    unittest.main()
