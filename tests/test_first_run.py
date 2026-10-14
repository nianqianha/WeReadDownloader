# -*- coding: utf-8 -*-
"""The first-run wizard must actually produce a file load_credentials() accepts.

WHY THIS TEST EXISTS
    This module shipped to a release while its function body was still
    "# ... rest of implementation" + pass, and nothing noticed because it had no
    test and -- worse -- was imported but never called. Both failure modes are
    locked here:

      1. the file it writes must be parseable by the real loader
      2. main() must actually invoke it

    The round-trip is the important part: a wizard that writes a file the loader
    rejects is indistinguishable, to the user, from a wizard that does nothing.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))

HERE = Path(__file__).resolve().parent.parent / "weread"

# A deliberately self-describing fixture: it carries the word "fake" so
# the repository's privacy gate classifies it as a stub rather than a
# leaked credential, and so a reader knows it is not one.
FAKE_KEY = "wrk-fake-0000000000000000"
spec = importlib.util.spec_from_file_location("first_run", HERE / "first_run.py")
first_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(first_run)

CASES = []


def test(fn):
    CASES.append(fn)
    return fn


@test
def test_validate_accepts_a_well_formed_key():
    assert first_run.validate(FAKE_KEY) == ""


@test
def test_validate_rejects_the_typos_a_user_actually_makes():
    assert "api_key" in first_run.validate("")
    assert "wrk-" in first_run.validate("abcdefghijklmnop")
    assert "短" in first_run.validate("wrk-fake-1")
    # whitespace from a copy-paste must not be treated as a typo
    assert first_run.validate("  " + FAKE_KEY + "  ") == ""


@test
def test_the_written_file_parses_as_lua_and_carries_both_values():
    """The real check: run the text through the same LuaJIT the loader uses."""
    text = first_run.build_settings_lua(FAKE_KEY)
    assert "\nreturn {" in text, "load_credentials() looks for this exact prefix"
    assert 'api_key = "%s"' % FAKE_KEY in text
    try:
        from lupa.luajit21 import LuaRuntime
    except ImportError:
        LuaRuntime = None
    if LuaRuntime is None:
        return
    start = text.encode("utf-8").find(b"\nreturn {")
    lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
    table = lua.execute(text.encode("utf-8")[start + 1:])
    assert table[b"api_key"].decode() == FAKE_KEY
    assert isinstance(table[b"cookies"], object)
    assert table[b"wr_ticket"].decode() == ""


@test
def test_quotes_and_backslashes_in_values_do_not_break_the_lua():
    """A key pasted with a quote in it must not produce an unparseable file."""
    nasty = 'wrk-fake-a"b\\c'
    text = first_run.build_settings_lua(nasty)
    try:
        from lupa.luajit21 import LuaRuntime
    except ImportError:
        return
    start = text.encode("utf-8").find(b"\nreturn {")
    lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
    table = lua.execute(text.encode("utf-8")[start + 1:])
    assert table[b"api_key"].decode() == nasty, table[b"api_key"]


@test
def test_write_settings_is_atomic_and_leaves_no_tmp_behind():
    with tempfile.TemporaryDirectory(prefix="weread-firstrun-") as tmp:
        target = Path(tmp) / "sub" / "weread-live-settings.lua"
        first_run.write_settings(target, FAKE_KEY)
        assert target.is_file()
        assert not list(target.parent.glob("*.tmp")), "temp file left behind"
        assert target.read_text(encoding="utf-8").startswith("--")


@test
def test_the_main_window_opens_before_any_credential_dialog():
    """The window must open first, and the credential check must not gate it.

    The startup order used to be: hide the window, show a wizard, only then
    deiconify. A new user therefore saw a small dialog appear and vanish, and on
    one path it came back a second time. The window is the program, so it opens
    unconditionally and the check runs afterwards -- a check that raises must not
    prevent it either.
    """
    source = (HERE / "desktop_app.py").read_text(encoding="utf-8")
    body = source.split("def main()", 1)[1]

    assert "root.withdraw()" not in body, \
        "the main window must not be hidden at startup"
    assert "DesktopApp(root)" in body, "the window is built"
    idx_check = body.find("check_credentials_on_start()")
    idx_build = body.find("DesktopApp(root)")
    assert idx_check > idx_build > 0, \
        "the credential check runs on a live window, not before it"


@test
def test_a_credential_check_failure_does_not_stop_the_window():
    """A raising check is logged, not fatal."""
    source = (HERE / "desktop_app.py").read_text(encoding="utf-8")
    assert "def check_credentials_on_start" in source, "the method must exist"
    call_region = source.split("def main()", 1)[1].split("mainloop", 1)[0]
    assert "check_credentials_on_start()" in call_region
    assert "except Exception" in call_region, \
        "a credential check failure must not stop the window from opening"


@test
def test_no_placeholder_body_remains():
    """Guard against the exact shape that shipped: a stub that returns None."""
    source = (HERE / "first_run.py").read_text(encoding="utf-8")
    assert "rest of implementation" not in source
    for line in source.splitlines():
        stripped = line.strip()
        assert stripped != "pass", "a bare `pass` body is the stub that shipped"


@test
def test_the_credential_path_comes_from_a_module_that_defines_it():
    """The wizard must not import SETTINGS from desktop_app.

    desktop_app has no module-level SETTINGS -- the only definitions are inside
    logout(), one imported from weread_desktop and one a local fallback. Writing
    'from desktop_app import SETTINGS' therefore raised ImportError at the moment
    the wizard was needed, which is how the first attempt at this fix failed its
    own runtime check.
    """
    source = (HERE / "first_run.py").read_text(encoding="utf-8")
    assert "from desktop_app import SETTINGS" not in source, \
        "desktop_app does not define SETTINGS"
    assert "desktop_app import SETTINGS" not in source, \
        "desktop_app does not define SETTINGS"

    path = first_run._settings_path()
    assert str(path).endswith("weread-live-settings.lua"), path


@test
def test_the_credential_path_does_not_need_the_excluded_module():
    """The GUI bundle EXCLUDES weread_desktop, so the path must not require it.

    WeReadDesktop.spec lists weread_desktop in excludes (it pulls in LuaJIT and
    lxml, ~5 s of startup). Importing SETTINGS from it raised ImportError inside
    the frozen GUI, main() swallowed that, and the main window opened with no
    wizard -- the user then saw 'Credential file not found' on every action.
    weread_core is present in every bundle and owns PRIVATE, so the path must
    resolve from it.
    """
    spec = (HERE.parent / "packaging" / "WeReadDesktop.spec").read_text(encoding="utf-8")
    assert '"weread_desktop"' in spec, "the exclusion this test guards against is gone"
    assert "weread_desktop" in spec.split("excludes=[")[1].split("]")[0], \
        "weread_desktop is no longer excluded; this guard may need revisiting"

    source = (HERE / "first_run.py").read_text(encoding="utf-8")
    assert "weread_core" in source, \
        "the wizard must resolve its path from weread_core, which the GUI keeps"

    # Prove it with the module weread_desktop absent, which is the frozen case.
    import importlib
    saved = sys.modules.pop("weread_desktop", None)
    try:
        first_run_path = Path(HERE / "first_run.py")
        spec2 = importlib.util.spec_from_file_location("first_run_isolated", first_run_path)
        iso = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(iso)
        p = iso._settings_path()
        assert str(p).endswith("weread-live-settings.lua"), p
    finally:
        if saved is not None:
            sys.modules["weread_desktop"] = saved


@test
def test_the_wizard_is_no_longer_a_startup_gate():
    """The wizard is not what decides whether the program starts.

    It used to be: the window was hidden, the wizard ran, and only a "ready"
    result deiconified it. That produced the observed sequence -- a small dialog
    appearing, then the window, and on one path the dialog again -- because a
    wizard failure was silently treated as consent. The window now opens first
    and always; this asserts that design is still in place.
    """
    source = (HERE / "desktop_app.py").read_text(encoding="utf-8")
    main_body = source.split("def main()", 1)[1]
    assert "_ensure_credentials_file(root)" not in main_body, \
        "the wizard must not gate startup"
    assert "root.withdraw()" not in main_body, \
        "the window must not be hidden while anything else runs"


# --- when does the wizard trigger? -------------------------------------------
# Measured against the frozen build, not assumed. Existence of the file is not
# the same as a usable file, and the difference is a state a user reaches for
# real: a logout leaves no file, but a corrupted one leaves a file behind.

COMPLETE = ('\nreturn {\n  api_key = "%s",\n'
            '  cookies = { wr_skey = "fakeskey" },\n'
            '  wr_ticket = "",\n  wr_wrpa = "",\n}\n' % FAKE_KEY)

API_ONLY = ('\nreturn {\n  api_key = "%s",\n'
            '  cookies = {\n  },\n'
            '  wr_ticket = "",\n  wr_wrpa = "",\n}\n' % FAKE_KEY)


@test
def test_the_wizard_does_not_trigger_for_a_complete_file():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "ok.lua"
        p.write_text(COMPLETE, encoding="utf-8")
        assert first_run._credentials_usable(p) is True


@test
def test_a_file_the_loader_rejects_is_not_usable():
    """api_key without wr_skey, or a broken table: not usable.

    load_credentials() raises for both, so a wizard that skips them would open a
    window whose every action fails. This is the case existence-only checking
    got wrong.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        only_api = Path(tmp) / "only_api.lua"
        only_api.write_text(API_ONLY, encoding="utf-8")
        assert first_run._credentials_usable(only_api) is False, \
            "load_credentials() rejects api_key without wr_skey"

        broken = Path(tmp) / "broken.lua"
        broken.write_text("this is not a lua table at all\n", encoding="utf-8")
        assert first_run._credentials_usable(broken) is False

        truncated = Path(tmp) / "truncated.lua"
        truncated.write_text("\nreturn {\n  api_key = \n", encoding="utf-8")
        assert first_run._credentials_usable(truncated) is False


@test
def test_the_wizard_triggers_when_there_is_no_file():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "missing.lua"
        assert first_run._credentials_usable(p) is False


@test
def test_the_trigger_is_not_wired_to_a_marker_file():
    """"First run" must not be decided by a flag file.

    A .wizard-done marker would mean a user who deletes their credentials never
    sees the wizard again. The trigger is the credential file's own state, so
    it fires on every launch until a usable file exists -- which is also why
    "first run" is the wrong name for it.
    """
    source = (HERE / "first_run.py").read_text(encoding="utf-8")
    for marker in ("wizard-done", "wizard_done", ".first-run", "firstrun_done"):
        assert marker not in source, "a first-run flag file must not decide this"


# --- the state that caused the loop ------------------------------------------
# The wizard writes api_key with an empty cookies table; wr_skey only arrives
# after the QR login. At that moment the file is present and api_key is usable,
# but load_credentials() still refuses it -- so a check on "usable" alone
# re-showed the wizard forever, and the user never reached the QR login that
# would have finished the job. This is the state the program is in right after
# the first successful wizard submission.

WIZARD_OUTPUT = ('\nreturn {\n  api_key = "%s",\n'
                 '  cookies = {\n  },\n'
                 '  wr_ticket = "",\n  wr_wrpa = "",\n}\n' % FAKE_KEY)


@test
def test_an_api_key_only_file_opens_the_main_window():
    """api_key present, wr_skey not yet: the QR login is the next step."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "just_after_wizard.lua"
        p.write_text(WIZARD_OUTPUT, encoding="utf-8")
        assert first_run._credentials_usable(p) is False, \
            "load_credentials() does need wr_skey; that part stays true"
        assert first_run._has_api_key(p) is True, \
            "the user already did their part and must not be bounced"


@test
def test_nothing_and_broken_still_open_the_wizard():
    """The loop must not be broken by letting everything through."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "none.lua"
        assert first_run._has_api_key(missing) is False

        broken = Path(tmp) / "broken.lua"
        broken.write_text("not a lua table\n", encoding="utf-8")
        assert first_run._has_api_key(broken) is False


def main() -> int:
    failures = 0
    for fn in CASES:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            failures += 1
            print("FAIL", fn.__name__, "-", exc)
        except Exception as exc:
            failures += 1
            print("FAIL", fn.__name__, "-", "%s: %s" % (type(exc).__name__, exc))
    print()
    print("%d/%d passed" % (len(CASES) - failures, len(CASES)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())