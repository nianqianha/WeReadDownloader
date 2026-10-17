"""The graphics app must offer a way to log out.

This button was requested and silently never built: `logout`, `退出登录` and
`sign_out` had zero hits in desktop_app.py, so the only way out of a signed-in
session was to delete session-cookies.json by hand. The web front end had
`logout()` the whole time, which is exactly why it looked like it "should"
already exist.

Logging out has to be honest, too. The engine treats weread-live-settings.lua
as a mandatory base and merges session-cookies.json over it, so deleting the
overlay is a refresh reset rather than a full sign-out.
"""
import importlib.util
import json
import tempfile
from functools import wraps
from pathlib import Path
from unittest.mock import patch
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-logout-test-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"
_CRED = Path(_TMP.name) / "session-cookies.json"
app.weread_login.CREDENTIAL_STATE = _CRED

# 基础凭证文件（weread-live-settings.lua）必须一起重定向。logout 以前自己用
# %LOCALAPPDATA% 拼这个路径，任何重定向都拦不住它——跑这个文件会删掉开发者本人
# 的真实凭证，之后每个操作都报 "Credential file not found"。desktop_app 现在改
# 走 _settings_path()，而那个路径归 weread_core.PRIVATE 所有，所以两处都要
# patch，与 test_desktop_app.py 的做法一致。
import weread_core as _weread_core

_PROFILE = Path(_TMP.name) / "WeReadDesktop"
_weread_core.PRIVATE = _PROFILE
app._settings_path = lambda: _PROFILE / "weread-live-settings.lua"

# 重定向一旦失效，这里立刻失败，而不是等真实凭证被删掉才发现。
assert str(app._settings_path()).startswith(_TMP.name), \
    "凭证路径必须被重定向出真实用户目录，否则测试会删掉真实凭证"

ALL_TESTS = []


def test(fn):
    # Reapply this file's profile after all test modules have been collected.
    @wraps(fn)
    def isolated(*args, **kwargs):
        with patch.object(app.weread_login, "CREDENTIAL_STATE", _CRED), \
                patch.object(_weread_core, "PRIVATE", _PROFILE):
            return fn(*args, **kwargs)
    ALL_TESTS.append(isolated)
    return isolated


def _make_gui():
    import customtkinter as ctk
    root = ctk.CTk()
    root.geometry("1120x700")
    instance = app.DesktopApp(root)
    # A modal dialog takes over the Tk main loop until it is clicked, so an
    # unexpected one hangs the whole suite instead of failing it. Any path that
    # still opens one is a bug worth seeing as a failure, not a hang.
    instance._ask = lambda kind, title, message, **kw: (
        setattr(instance, "status", type(instance.status)(value=message)) or "ok")
    root.update()
    return root, instance


def _write_credentials(name="钦小糖"):
    _CRED.write_text(json.dumps({
        "api_key": "wrk-test",
        "cookies": {"wr_skey": "s"},
        "account": {"name": name},
    }), encoding="utf-8")


def _logs():
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(app.LOG_DIR.glob("*.log"))
    )


@test
def test_there_is_a_logout_button_and_it_says_what_it_does():
    root, instance = _make_gui()
    try:
        assert hasattr(instance, "logout_button"), (
            "the graphics app has no 退出登录 button at all")
        assert instance.logout_button.cget("text") == "退出登录"
    finally:
        root.destroy()


@test
def test_logout_is_only_offered_when_there_is_a_session_to_drop():
    root, instance = _make_gui()
    try:
        assert instance.logout_button.cget("state") == "disabled", (
            "offered 退出登录 with nothing to log out of")
        _write_credentials()
        instance._refresh_logout_button()
        assert instance.logout_button.cget("state") == "normal"
    finally:
        root.destroy()


@test
def test_logging_out_deletes_the_saved_session():
    root, instance = _make_gui()
    try:
        _write_credentials()
        instance._refresh_logout_button()
        instance.logout_button.invoke()
        root.update()
        assert not _CRED.exists(), "session-cookies.json survived the logout"
    finally:
        root.destroy()


@test
def test_logging_out_resets_the_account_button_and_disables_itself():
    root, instance = _make_gui()
    try:
        _write_credentials()
        instance._set_login_identity("钦小糖")
        instance._refresh_logout_button()
        instance.logout_button.invoke()
        root.update()
        assert instance.login_button.cget("text") == "登录 / 切换账号", (
            instance.login_button.cget("text"))
        assert instance.logout_button.cget("state") == "disabled"
    finally:
        root.destroy()


@test
def test_logout_is_recorded_in_the_log():
    root, instance = _make_gui()
    try:
        _write_credentials()
        instance._refresh_logout_button()
        instance.logout_button.invoke()
        root.update()
        assert "[LOGOUT]" in _logs(), "a logout left no trace in the log"
    finally:
        root.destroy()


@test
def test_logging_out_with_nothing_stored_is_harmless():
    """A double-click must not raise, and must not claim it signed you out.

    Called directly rather than through invoke(): with no stored session the
    button is disabled, and a disabled button never runs its command, which is
    the correct behaviour and is covered by the test above.
    """
    root, instance = _make_gui()
    try:
        assert not _CRED.exists()
        instance._refresh_logout_button()
        instance.logout()
        root.update()
        assert "无需退出" in instance.status.get(), instance.status.get()
    finally:
        root.destroy()


@test
def test_logout_is_offered_for_a_session_that_has_no_api_key():
    """The reported case: signed in, working downloads, button greyed out.

    A session written by an older build (or one where the api_key was never
    persisted) carries cookies only. has_credentials() demands an api_key too, so
    the button never enabled -- on the machines that were actually logged in, the
    only way to clear the session was deleting the file by hand.
    """
    root, instance = _make_gui()
    try:
        _CRED.write_text(json.dumps({
            "cookies": {"wr_skey": "s", "wr_vid": "425340025"},
            "wr_ticket": "", "wr_wrpa": "",
        }), encoding="utf-8")
        instance._refresh_logout_button()
        assert instance.logout_button.cget("state") == "normal", (
            "a working cookie session still signs the user in, so 退出登录 must be "
            "offered; the api_key is not what this button deletes")
        # And it must actually work, not just look clickable.
        instance.logout_button.invoke()
        root.update()
        assert not _CRED.exists(), "logout did nothing for an api_key-less session"
    finally:
        root.destroy()


@test
def test_logout_is_refused_while_a_job_is_running():
    root, instance = _make_gui()
    try:
        _write_credentials()
        instance._refresh_logout_button()
        instance._is_busy = lambda: True
        instance.logout()
        root.update()
        assert _CRED.exists(), "logged out in the middle of a download"
        assert "任务" in instance.status.get(), instance.status.get()
    finally:
        root.destroy()


@test
def test_clear_credentials_reports_whether_it_removed_anything():
    from weread_login import clear_credentials
    # Its own path: an earlier test deliberately leaves a session behind.
    target = Path(_TMP.name) / "clear-probe.json"
    assert clear_credentials(target) is False, "claimed to remove a missing file"
    target.write_text("{}", encoding="utf-8")
    assert clear_credentials(target) is True
    assert not target.exists()


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
