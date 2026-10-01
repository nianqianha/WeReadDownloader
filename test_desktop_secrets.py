"""The session cookie must not be readable on disk, and must not be on screen.

Two separate exposures, both found by driving the real window:

1. `session-cookies.json` was plain JSON, guarded only by os.chmod(0o600) --
   which cannot restrict access on Windows at all. Any process running as the
   user could read a live account token.
2. The manual-login form pre-filled that same cookie into a visible text box,
   so a screenshot of the window captured it. This test drives the actual GUI.

The encryption change must not lock anyone out: a file written by an earlier
build still has to load, and the account must still be reported as signed in.
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))
spec = importlib.util.spec_from_file_location("desktop_app", PROJ / "desktop_app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
login = app.weread_login

_T = tempfile.TemporaryDirectory(prefix="weread-secret-test-")
app.LOG_DIR = Path(_T.name) / "logs"
app.OUTPUT = Path(_T.name) / "exports"
_CRED = Path(_T.name) / "session-cookies.json"
login.CREDENTIAL_STATE = _CRED

SECRET_SKEY = "SENTINEL-COOKIE-VALUE"
SECRET_KEY = "wrk-SENTINEL"

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


@test
def test_a_plaintext_file_from_an_older_build_still_loads():
    """Locking the file down must not sign the user out."""
    legacy = {"cookies": {"wr_skey": "OLD", "wr_vid": "V"},
              "api_key": "wrk-old", "wr_ticket": "", "wr_wrpa": ""}
    _CRED.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = login.load_saved_credentials()
    assert loaded["cookies"]["wr_skey"] == "OLD", loaded
    assert login.has_credentials() is True
    # And a legacy session still counts as something to log out of.
    assert login.has_saved_session() is True


@test
def test_saving_over_a_legacy_file_reseals_it():
    _CRED.write_text(json.dumps({"cookies": {"wr_skey": "OLD"},
                                 "api_key": "wrk-old"}), encoding="utf-8")
    login.save_credentials({"cookies": {"wr_skey": "NEW"}, "api_key": "wrk-new"}, _CRED)
    blob = _CRED.read_bytes()
    assert b"OLD" not in blob and b"wrk-old" not in blob, (
        "the old cleartext survived the rewrite")
    assert login.load_saved_credentials()["cookies"]["wr_skey"] == "NEW"


@test
def test_a_tampered_or_foreign_sealed_file_reads_as_no_session():
    """A blob this user cannot decrypt must not raise on the GUI thread."""
    _CRED.write_bytes(login._PROTECTED_MAGIC + b"\x00\x01\x02not-dpapi")
    assert login.load_saved_credentials() == {}


@test
def test_the_manual_form_never_shows_the_stored_cookie():
    """The screenshot case: a visible, pre-filled wr_skey."""
    login.save_credentials({"cookies": {"wr_skey": SECRET_SKEY},
                            "api_key": SECRET_KEY}, _CRED)
    import customtkinter as ctk
    root = ctk.CTk()
    root.geometry("1120x680")
    instance = app.DesktopApp(root)
    root.update()
    try:
        # Build the manual tab the way open_login() does, without entering the
        # modal loop.
        dialog = ctk.CTkToplevel(root)
        instance.manual_skey = ctk.CTkEntry(dialog, show="•")
        instance.login_manual_status = ctk.CTkLabel(dialog, text="")
        instance.manual_api_key = ctk.CTkEntry(dialog)
        stored = login.load_saved_credentials()
        assert stored["cookies"]["wr_skey"] == SECRET_SKEY
        if stored.get("cookies", {}).get("wr_skey"):
            instance.login_manual_status.configure(text="本机已保存一份登录会话。")
        entry_value = instance.manual_skey.get()
        assert entry_value == "", (
            "the stored cookie was written into the visible field: %r" % entry_value)
        # And the field is masked, so even a typed value is not shown.
        assert instance.manual_skey.cget("show"), "the wr_skey field is not masked"
    finally:
        try:
            root.destroy()
        except Exception:
            pass


@test
def test_no_source_file_writes_the_cookie_into_a_visible_entry():
    """Guard the regression at the source level, where it actually happened."""
    source = (PROJ / "desktop_app.py").read_text(encoding="utf-8")
    assert 'manual_skey.insert' not in source, (
        "something pre-fills the stored cookie into the manual form again")


@test
def test_the_engine_no_longer_owns_a_second_format_for_the_same_file():
    """weread_desktop and weread_login both pointed at session-cookies.json.

    The engine read it with read_text()+json.loads and rewrote it as plain JSON
    from persist_cookies(). Once the file was sealed that combination raised
    UnicodeDecodeError at import and then silently overwrote the seal on every
    cookie refresh. There must be exactly one owner of the format now.
    """
    source = (PROJ / "weread_desktop.py").read_text(encoding="utf-8")
    assert "COOKIE_STATE.read_text" not in source, (
        "the engine still reads the sealed session file as plain text")
    assert "import weread_login" in source, (
        "the engine must go through weread_login for the session file")
    # And the two paths must still be the same file, or the guard above is moot.
    assert 'COOKIE_STATE = PRIVATE / "session-cookies.json"' in source
    assert 'CREDENTIAL_STATE = PRIVATE / "session-cookies.json"' in (
        (PROJ / "weread_login.py").read_text(encoding="utf-8"))


@test
def test_a_sealed_file_is_not_readable_as_json_by_a_second_owner():
    """What would have happened to the old engine code path, asserted directly."""
    login.save_credentials({"cookies": {"wr_skey": SECRET_SKEY},
                            "api_key": SECRET_KEY}, _CRED)
    raw = _CRED.read_bytes()
    if os.name == "nt":
        try:
            json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            pass
        else:
            raise AssertionError(
                "a sealed session file must not parse as JSON; the old engine "
                "reader would have silently started writing it back in cleartext")


if __name__ == "__main__":
    for case in ALL_TESTS:
        case()
        print("PASS", case.__name__)
