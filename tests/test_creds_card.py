# -*- coding: utf-8 -*-
"""Tests for the inline credential card.

The card replaced a popup wizard that appeared, resized and vanished. What must
be locked here:

  1. the download buttons are replaced by the card, and come back when the
     credentials are complete
  2. each missing-credential combination shows only what applies -- an api_key
     field for a user who still needs to enter it, a single scan button for one
     who only lacks wr_skey
  3. saving through the card writes a file the real loader accepts
  4. saving without api_key is refused with a message, not silently
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))

# The merged-view check reads weread_login.CREDENTIAL_STATE. Point it at a
# scratch path so the real profile's overlay (a genuine session on this machine)
# cannot leak into fixtures that describe base-file states -- the same leak that
# made these assertions fail while the program behaved correctly.
_OVERLAY_ISOLATION = tempfile.TemporaryDirectory(prefix="weread-overlay-")
import weread_login as _weread_login
_weread_login.CREDENTIAL_STATE = (
    Path(_OVERLAY_ISOLATION.name) / "session-cookies.json")

HERE = Path(__file__).resolve().parent.parent / "weread"
spec = importlib.util.spec_from_file_location("first_run", HERE / "first_run.py")
first_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(first_run)

CASES = []


def test(fn):
    CASES.append(fn)
    return fn


COMPLETE = ('\nreturn {\n  api_key = "wrk-fake-0000000000000000",\n'
            '  cookies = { wr_skey = "s" },\n  wr_ticket = "",\n  wr_wrpa = "",\n}\n')
API_ONLY = ('\nreturn {\n  api_key = "wrk-fake-0000000000000000",\n'
            '  cookies = {\n  },\n  wr_ticket = "",\n  wr_wrpa = "",\n}\n')


@test
def test_the_download_buttons_are_replaced_when_credentials_are_missing():
    """The card takes the place of the buttons; they return when complete."""
    source = (HERE / "desktop_app.py").read_text(encoding="utf-8")
    refresh = source.split("def _refresh_creds_panel", 1)[1].split("def ", 1)[0]
    assert "probe_button.grid_remove()" in refresh, \
        "a missing credential must take the buttons out of the way"
    assert "export_button.grid_remove()" in refresh
    assert "creds_panel.grid()" in refresh, "and the card must take their place"
    # ...and the reverse: a complete file restores them
    assert "creds_panel.grid_remove()" in refresh, \
        "a complete credential file must hide the card"
    assert "probe_button.grid()" in refresh, \
        "and the buttons must come back"
    assert "export_button.grid()" in refresh


@test
def test_a_user_who_only_lacks_wr_skey_is_not_shown_an_api_key_field():
    """api_key is already on disk; asking for it again is what caused the loop."""
    source = (HERE / "desktop_app.py").read_text(encoding="utf-8")
    refresh = source.split("def _refresh_creds_panel", 1)[1].split("def ", 1)[0]
    # the has_key branch must hide the two-field form and show the scan button
    branch = refresh.split("if has_key:", 1)[1]
    assert "fields_frame.pack_forget()" in branch, \
        "the api_key form must be hidden when api_key already exists"
    assert "scan_only_frame.pack" in branch, \
        "and the scan button shown instead"


@test
def test_the_card_says_that_wr_skey_comes_from_the_qr_login():
    """The question the user asked: what is wr_skey, and do I need a dev page?"""
    source = (HERE / "desktop_app.py").read_text(encoding="utf-8")
    assert "扫码登录后自动填入" in source or "扫码登录」自动获取" in source, \
        "the wr_skey field must say it is filled by the QR login"
    assert "不需要开开发者模式" in source, \
        "the api_key hint must say the skills page needs no developer mode"


@test
def test_an_empty_api_key_is_refused_with_a_reason():
    """A silent no-op save is how a user ends up clicking twice."""
    assert first_run._validate_creds("", "") != ""
    assert "API Key" in first_run._validate_creds("", "")
    assert "wrk-" in first_run._validate_creds("not-a-key", "")
    assert first_run._validate_creds("wrk-fake-0000000000000000", "") == "", \
        "wr_skey is optional: the QR login supplies it"


@test
def test_saving_through_the_card_produces_a_loadable_file():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "weread-live-settings.lua"
        body = first_run._build_settings_lua("wrk-fake-0000000000000000", "skey123")
        target.write_text(body, encoding="utf-8")
        # the real loader's contract
        assert first_run._credentials_usable(target) is True
        # and with no skey, it is saved but not yet usable -- which is correct,
        # because the QR login is the next step
        target2 = Path(tmp) / "no_skey.lua"
        target2.write_text(first_run._build_settings_lua(
            "wrk-fake-0000000000000000", ""), encoding="utf-8")
        assert first_run._credentials_usable(target2) is False
        assert first_run._has_api_key(target2) is True


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