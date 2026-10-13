# -*- coding: utf-8 -*-
"""The 我的书架 page must show the REAL WeRead shelf, and a click downloads.

Locks three things:
  1. the shelf half renders the account's books (cover/title/author/category)
     and each row offers a download action
  2. clicking a row selects that book, switches to the workshop, and asks for
     the export confirmation -- the same path a search result uses
  3. the SHELF worker line is parsed, and a malformed one is reported rather
     than silently swallowed
"""
import importlib.util
import tempfile
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("desktop_app.py")
spec = importlib.util.spec_from_file_location("desktop_app", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_TMP = tempfile.TemporaryDirectory(prefix="weread-shelf-")
app.LOG_DIR = Path(_TMP.name) / "logs"
app.OUTPUT = Path(_TMP.name) / "exports"
app.weread_login.CREDENTIAL_STATE = Path(_TMP.name) / "session-cookies.json"

ALL = []


def test(fn):
    ALL.append(fn)
    return fn


def _make_instance():
    import customtkinter as ctk
    root = ctk.CTk()
    instance = app.DesktopApp(root)
    root.geometry("1120x680")
    root.update()
    root.update()
    return root, instance


SHELF_LINE = ('SHELF\t['
              '{"bookId":"19398284","title":"逆流纯真年代","author":"人间武库",'
              '"cover":"https://example.invalid/a.jpg","category":"男生小说-年代幻想"},'
              '{"bookId":"41100833","title":"北派盗墓笔记","author":"云峰",'
              '"cover":"","category":"悬疑"}'
              ']')


@test
def test_the_shelf_page_shows_the_accounts_books():
    root, inst = _make_instance()
    try:
        inst._switch_page("我的书架")
        root.update()
        # Stub the worker call: switching pages triggers a real shelf fetch,
        # and this test must render exactly the rows it supplies.
        inst.load_shelf = lambda: None
        rows = inst._parse_shelf(SHELF_LINE)
        assert isinstance(rows, list) and len(rows) == 2, rows
        inst._show_shelf(rows)
        root.update()
        texts = []

        def collect(w):
            for c in w.winfo_children():
                try:
                    t = c.cget("text")
                    if isinstance(t, str) and t:
                        texts.append(t)
                except Exception:
                    pass
                collect(c)
        collect(inst.shelf_frame)
        joined = "\n".join(texts)
        assert "逆流纯真年代" in joined, joined[:300]
        assert "人间武库" in joined, joined[:300]
        assert "男生小说-年代幻想" in joined, joined[:300]
        assert "北派盗墓笔记" in joined, joined[:300]
        # each row must offer the download action
        buttons = [b for b in _walk(inst.shelf_frame)
                   if type(b).__name__ == "CTkButton"]
        labels = [b.cget("text") for b in buttons]
        assert labels.count("下载") == 2, labels
        assert "已读取微信书架，共 2 本。" in inst.status.get(), inst.status.get()
    finally:
        root.destroy()


def _walk(w):
    out = []
    for c in w.winfo_children():
        out.append(c)
        out.extend(_walk(c))
    return out


@test
def test_clicking_a_shelf_row_starts_a_download():
    root, inst = _make_instance()
    try:
        # record what the export confirmation would say, without running it
        asked = {}
        inst._ask = lambda kind, title, msg: asked.update(
            {"kind": kind, "title": title, "msg": msg}) or False
        switched = []
        original = inst._switch_page

        def spy(name):
            switched.append(name)
            return original(name)
        inst._switch_page = spy
        # _select_book would start a real --book-info worker; the click under
        # test is the download, so stop at the selection and assert on it.
        original_select = inst._select_book

        def stub_select(item):
            inst.selected_id = str(item.get("bookId", ""))
            inst.selected_title = str(item.get("title", ""))
        inst._select_book = stub_select

        inst._download_from_shelf("19398284", "逆流纯真年代")
        root.update()

        assert inst.selected_id == "19398284", inst.selected_id
        assert "下载工坊" in switched, switched
        assert asked.get("kind") == "askyesno", asked
        assert "逆流纯真年代" in asked.get("msg", ""), asked
    finally:
        root.destroy()


@test
def test_an_empty_shelf_says_so_instead_of_rendering_nothing():
    root, inst = _make_instance()
    try:
        inst._switch_page("我的书架")
        root.update()
        inst._show_shelf([])
        root.update()
        texts = []

        def collect(w):
            for c in w.winfo_children():
                try:
                    t = c.cget("text")
                    if isinstance(t, str) and t:
                        texts.append(t)
                except Exception:
                    pass
                collect(c)
        collect(inst.shelf_frame)
        assert any("书架是空的" in t for t in texts), texts
    finally:
        root.destroy()


@test
def test_a_malformed_shelf_line_is_reported_not_swallowed():
    root, inst = _make_instance()
    try:
        assert inst._parse_shelf("SHELF\t[]") == []
        assert inst._parse_shelf("SHELF\tnot json") is None
        inst._switch_page("我的书架")
        root.update()
        inst._handle_line("SHELF\tnot json")
        root.update()
        assert "读不到" in inst.status.get(), inst.status.get()
    finally:
        root.destroy()


@test
def test_the_local_export_half_still_offers_delete_and_reclaim():
    """The shelf addition must not cost the on-machine cleanup tools."""
    root, inst = _make_instance()
    try:
        base = Path(app.OUTPUT)
        (base / "成品书-111").mkdir(parents=True, exist_ok=True)
        (base / "成品书-111" / "成品书 - full.epub").write_bytes(b"x" * (2 << 20))
        (base / "半成品-222.work").mkdir(parents=True, exist_ok=True)
        (base / "半成品-222.work" / "chapter-uid-1.body").write_bytes(b"x" * (5 << 20))

        inst._switch_page("我的书架")
        root.update()
        texts = []

        def collect(w):
            for c in w.winfo_children():
                try:
                    t = c.cget("text")
                    if isinstance(t, str) and t:
                        texts.append(t)
                except Exception:
                    pass
                collect(c)
        collect(inst.pages["我的书架"])
        joined = "\n".join(texts)
        assert "微信书架" in joined, joined[:300]
        assert "本机已导出" in joined, joined[:300]
        assert "全部清理" in joined, "the reclaim button is gone"
        # the local rows show the folder name (which is what identifies it),
        # not the epub filename
        assert "成品书-111" in joined, joined[:400]
        assert "半成品-222.work（半成品）" in joined, joined[:400]
        assert "可回收" in joined, joined[:400]
    finally:
        root.destroy()


def main() -> int:
    failures = 0
    for fn in ALL:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            failures += 1
            print("FAIL", fn.__name__, "-", exc)
        except Exception as exc:
            failures += 1
            print("FAIL", fn.__name__, "-", f"{type(exc).__name__}: {exc}")
    print()
    print("%d/%d passed" % (len(ALL) - failures, len(ALL)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())