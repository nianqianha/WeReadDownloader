"""GUI readiness must agree with the actual loader for supported settings."""
import builtins
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / "weread"
sys.path.insert(0, str(HERE))
import first_run
import weread_login

spec = importlib.util.spec_from_file_location("readiness_engine", HERE / "weread_desktop.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class CredentialReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / "base.lua"
        self.overlay = Path(self.temp.name) / "session.json"
        for obj, name, value in ((engine, "SETTINGS", self.base),
                                 (engine, "COOKIE_STATE", self.overlay),
                                 (weread_login, "CREDENTIAL_STATE", self.overlay)):
            patcher = patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def check(self, text, overlay, expected, has_key):
        if text is not None:
            self.base.write_text(text, encoding="utf-8")
        self.overlay.write_text(json.dumps(overlay), encoding="utf-8")
        try:
            accepted = bool(engine.load_credentials())
        except Exception:
            accepted = False
        self.assertEqual(accepted, expected)
        real_import = builtins.__import__

        def without_lua(name, *args, **kwargs):
            if name == "lupa" or name.startswith("lupa."):
                raise ImportError("GUI does not bundle Lua")
            return real_import(name, *args, **kwargs)

        for no_lua in (False, True):
            with self.subTest(no_lua=no_lua):
                with patch("builtins.__import__", without_lua if no_lua else real_import):
                    self.assertEqual(first_run._credentials_usable(self.base), expected)
                    self.assertEqual(first_run._has_api_key(self.base), has_key)

    def test_base_and_overlay_states(self):
        cases = (
            ("base", "wrk-fake-readiness", {"wr_skey": "fake-session"}, {}, True, True),
            ("split", "wrk-fake-readiness", {}, {"cookies": {"wr_skey": "fake-session"}}, True, True),
            ("only-key", "wrk-fake-readiness", {}, {}, False, True),
            ("empty-key", "", {}, {"api_key": "wrk-fake-overlay", "cookies": {"wr_skey": "fake-session"}}, False, False),
            ("overlay-clears-session", "wrk-fake-readiness", {"wr_skey": "fake-session"}, {"cookies": {"wr_skey": ""}}, False, True),
        )
        for label, key, cookies, overlay, expected, has_key in cases:
            with self.subTest(case=label):
                self.check(first_run.build_settings_lua(key, cookies), overlay, expected, has_key)

    def test_complete_overlay_cannot_replace_missing_base(self):
        self.check(None, {"api_key": "wrk-fake-overlay", "cookies": {"wr_skey": "fake-session"}}, False, False)

    def test_malformed_table_does_not_pass_from_words_in_comments(self):
        self.check('-- api_key wr_skey\nreturn { api_key = "wrk-fake-readiness", cookies = {\n', {}, False, False)

    def test_quoted_keys_comments_and_escaped_strings_match_the_loader(self):
        text = ('-- api_key wr_skey\nreturn {\n'
                ' ["api_key"] = "wrk-fake-quote\\\"--not-comment",\n'
                ' ["cookies"] = { ["wr_skey"] = "fake-\\\\session"; },\n}\n')
        self.check(text, {}, True, True)

    def test_last_assignment_wins_instead_of_matching_an_earlier_key(self):
        text = ('\nreturn { api_key = "wrk-fake-readiness", cookies = {wr_skey="fake-session"},'
                ' api_key = "" }\n')
        self.check(text, {}, False, False)

    def test_fallback_does_not_accept_code_or_an_incomplete_table(self):
        for text in ('{api_key="fake"', '{api_key="fake"} unexpected',
                     '{api_key=os.getenv("FAKE_KEY"),cookies={}}',
                     '{api_key="fake" cookies={}}'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    first_run._settings_literal(text)

    def test_invalid_optional_credential_fields_do_not_pass_readiness(self):
        self.check('\nreturn {api_key="wrk-fake-readiness",cookies={wr_skey="fake-session"},wr_ticket={}}',
                   {}, False, False)


if __name__ == "__main__":
    unittest.main()
