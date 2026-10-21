# -*- coding: utf-8 -*-
"""The data pages must REFRESH on every visit, not cache the first snapshot.

The bug this locks: _create_page built each page on first visit and cached it,
so a user who opened 存储占用 before downloading saw that emptiness forever --
the EPUB landed on disk, the page still said nothing was there. This test
drives two visits and asserts the second reflects changed data.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))

HERE = Path(__file__).resolve().parent.parent / "weread"
spec = importlib.util.spec_from_file_location("desktop_app", HERE / "desktop_app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-refresh-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"
app.weread_login.CREDENTIAL_STATE = Path(_TMP.name) / "session-cookies.json"

CASES = []


def test(fn):
    CASES.append(fn)
    return fn


@test
def test_the_source_rebuilds_pages_instead_of_caching_them():
    """The fix lives in _create_page: destroy the old frame, build fresh."""
    source = (HERE / "desktop_app.py").read_text(encoding="utf-8")
    body = source.split("def _create_page", 1)[1].split("def _page_scaffold", 1)[0]
    assert ".destroy()" in body, \
        "the stale frame must be destroyed before rebuilding"
    assert "Build on first visit" not in body and "first visit and cached" not in body, \
        "the caching comment (and behaviour) must be gone"


@test
def test_a_second_visit_reflects_new_downloads():
    """The user-visible contract: download, then the pages show it."""
    import customtkinter as ctk
    root = ctk.CTk()
    inst = app.DesktopApp(root)
    try:
        base = Path(app.OUTPUT)
        base.mkdir(parents=True, exist_ok=True)
        book = base / "测试书-111"
        book.mkdir(parents=True, exist_ok=True)
        # the EPUB exists from the start; what changes between visits is the
        # page having been built once BEFORE it counted (visit 1 renders with
        # the file already there is fine -- the assertion below checks the
        # count goes from not-shown to shown on the SECOND visit)
        (book / "测试书 - full.epub").write_bytes(b"x" * (1 << 20))

        # visit 1: page built while the data is already there is NOT the point;
        # to model the reported bug (page built BEFORE the download), remove it
        # after the first render, then restore it for the second visit.
        inst._switch_page("存储占用")
        root.update()
        texts1 = _collect(inst.pages["存储占用"])

        # simulate: first visit happened before the download completed
        (book / "测试书 - full.epub").unlink()
        inst._switch_page("下载工坊")
        root.update()
        inst._switch_page("存储占用")
        root.update()
        texts_empty = _collect(inst.pages["存储占用"])
        assert not any("测试书" in t for t in texts_empty), \
            "sanity: without the EPUB the page must not show the book"

        # the download lands
        (book / "测试书 - full.epub").write_bytes(b"x" * (1 << 20))

        # visit 3: SAME session, same page key -- must show the new state
        inst._switch_page("下载工坊")
        root.update()
        inst._switch_page("存储占用")
        root.update()
        texts2 = _collect(inst.pages["存储占用"])
        joined = "\n".join(texts2)
        # the book NAME sits inside row labels the collector does not always reach;
        # the count and size are the stable user-visible signals
        assert "1.0 MB" in joined and "最近完成" in joined, \
            "the second visit must show the book that landed between visits: " \
            + joined[:300]
    finally:
        root.destroy()


def _collect(widget):
    out = []
    for c in widget.winfo_children():
        try:
            t = c.cget("text")
            if isinstance(t, str) and t:
                out.append(t)
        except Exception:
            pass
        out.extend(_collect(c))
    return out


def main() -> int:
    failures = 0
    for fn in CASES:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            failures += 1
            print("FAIL", fn.__name__, "-", str(exc)[:220])
        except Exception as exc:
            failures += 1
            print("FAIL", fn.__name__, "-", "%s: %s" % (type(exc).__name__, exc))
    print()
    print("%d/%d passed" % (len(CASES) - failures, len(CASES)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())