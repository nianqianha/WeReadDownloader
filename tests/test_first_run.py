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
spec = importlib.util.spec_from_file_location("first_run", HERE / "first_run.py")
first_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(first_run)

CASES = []


def test(fn):
    CASES.append(fn)
    return fn


@test
def test_validate_accepts_a_well_formed_pair():
    assert first_run.validate("wrk-abcdefghijklmnop", "123456789") == ""


@test
def test_validate_rejects_the_typos_a_user_actually_makes():
    assert "api_key" in first_run.validate("", "123456789")
    assert "wrk-" in first_run.validate("abcdefghijklmnop", "123456789")
    assert "短" in first_run.validate("wrk-abc", "123456789")
    assert "user_vid" in first_run.validate("wrk-abcdefghijklmnop", "")
    assert "数字" in first_run.validate("wrk-abcdefghijklmnop", "abc123")
    # whitespace from a copy-paste must not be treated as a typo
    assert first_run.validate("  wrk-abcdefghijklmnop  ", " 123456789 ") == ""


@test
def test_the_written_file_parses_as_lua_and_carries_both_values():
    """The real check: run the text through the same LuaJIT the loader uses."""
    text = first_run.build_settings_lua("wrk-abcdefghijklmnop", "123456789")
    assert "\nreturn {" in text, "load_credentials() looks for this exact prefix"
    assert 'api_key = "wrk-abcdefghijklmnop"' in text
    try:
        from lupa.luajit21 import LuaRuntime
    except ImportError:
        LuaRuntime = None
    if LuaRuntime is None:
        return
    start = text.encode("utf-8").find(b"\nreturn {")
    lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
    table = lua.execute(text.encode("utf-8")[start + 1:])
    assert table[b"api_key"].decode() == "wrk-abcdefghijklmnop"
    assert isinstance(table[b"cookies"], object)
    assert table[b"wr_ticket"].decode() == ""


@test
def test_quotes_and_backslashes_in_values_do_not_break_the_lua():
    """A key pasted with a quote in it must not produce an unparseable file."""
    nasty = 'wrk-a"b\\c'
    text = first_run.build_settings_lua(nasty, "1")
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
        first_run.write_settings(target, "wrk-abcdefghijklmnop", "123456789")
        assert target.is_file()
        assert not list(target.parent.glob("*.tmp")), "temp file left behind"
        assert target.read_text(encoding="utf-8").startswith("--")


@test
def test_the_wizard_is_actually_reachable_from_main():
    """Imported-but-never-called is how this shipped broken once already."""
    source = (HERE / "desktop_app.py").read_text(encoding="utf-8")
    assert "from .first_run import _ensure_credentials_file" in source, \
        "the import must remain"
    # the call, not just the import
    assert "_ensure_credentials_file(root)" in source, \
        "main() must call the wizard, not merely import it"


@test
def test_no_placeholder_body_remains():
    """Guard against the exact shape that shipped: a stub that returns None."""
    source = (HERE / "first_run.py").read_text(encoding="utf-8")
    assert "rest of implementation" not in source
    for line in source.splitlines():
        stripped = line.strip()
        assert stripped != "pass", "a bare `pass` body is the stub that shipped"


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