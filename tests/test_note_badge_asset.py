# -*- coding: utf-8 -*-
"""注角标是内嵌位图，不再用 CSS 画圆（issue #96）。

旧做法 `border-radius:50%` 的圆跟字体走：换第三方字体时字宽/行高对不上，圆会被
挤成椭圆。改成透明底正圆 PNG 后不挑字体。这里守住：
  1. 位图确实进了 EPUB（OEBPS/images/note-badge.png，PNG 签名正确）；
  2. content.opf 登记了它，正文角标指向它，alt 保留「注」；
  3. 没有注的书不带这张图（不给每本成品白加 8 KB）。
"""
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py"
spec = importlib.util.spec_from_file_location("wd_note_badge", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

BODY_WITH_NOTE = ("<html><body><h1>第一章</h1>"
                  '<p>正文开始<img class="qqreader-footnote" alt="注文在此"/>继续说。</p>'
                  "</body></html>")
BODY_WITHOUT_NOTE = "<html><body><h1>第一章</h1><p>正文一段。</p></body></html>"


class _FakeLua:
    def table(self):
        return {}

    def globals(self):
        return {b"_EXPORT": {b"inject": self.inject, b"audit": None,
                             b"reader_url": lambda *args: b"https://example.invalid/"}}

    def inject(self, raw, underlines, reviews, book):
        return raw, b""


class _FakeAPI:
    http = None

    def __init__(self, *args):
        self.body = BODY_WITHOUT_NOTE

    def fetch_book(self, book_id):
        return ({"bookId": book_id, "title": "Note book", "author": "T"},
                [{"chapterUid": "1", "title": "第一章", "wordCount": 10}])

    def chapter_html(self, book, chapter):
        return self.body, ""

    def annotations(self, book_id, uid, thoughts, underlines):
        return ({"chapterUid": uid, "underlines": []}, [])

    def persist_cookies(self):
        pass


class NoteBadgeAssetTests(unittest.TestCase):
    def _export(self, body, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name)
        api = _FakeAPI()
        api.body = body
        with patch.object(wd, "WeRead", lambda *args: api), \
                patch.object(wd, "lua_runtime", lambda: _FakeLua()), \
                patch.object(wd, "_set_private_acl", lambda *args: None):
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                bundle = wd.export_book("666", output, {}, jobs=1, **kwargs)
        epub = next(bundle.parent.glob("*.epub"))
        with zipfile.ZipFile(epub) as archive:
            names = set(archive.namelist())
            chapter = archive.read("OEBPS/text/chapter-0001.xhtml").decode("utf-8", "replace")
            opf = archive.read("OEBPS/content.opf").decode("utf-8", "replace")
            badge = archive.read("OEBPS/images/note-badge.png") if "OEBPS/images/note-badge.png" in names else b""
        return names, chapter, opf, badge

    def test_the_badge_bitmap_ships_with_a_book_that_has_notes(self):
        names, chapter, opf, badge = self._export(
            BODY_WITH_NOTE, want_thoughts=False, want_underlines=True,
            unified_appendix=True, appendix_scope="notes")
        self.assertIn("OEBPS/images/note-badge.png", names)
        self.assertTrue(badge.startswith(b"\x89PNG"), badge[:8])
        self.assertIn('src="../images/note-badge.png"', chapter)
        self.assertIn('alt="注"', chapter)
        self.assertIn("note-badge.png", opf, "manifest 没登记位图，阅读器会报缺文件")

    def test_a_book_without_notes_does_not_carry_the_bitmap(self):
        names, _chapter, _opf, _badge = self._export(
            BODY_WITHOUT_NOTE, want_thoughts=False, want_underlines=True,
            unified_appendix=True, appendix_scope="notes")
        self.assertNotIn("OEBPS/images/note-badge.png", names)


class BadgeCssTests(unittest.TestCase):
    def test_css_no_longer_draws_a_circle_with_border_radius(self):
        from unified_appendix import APPENDIX_CSS
        block = APPENDIX_CSS.split(".wr-fn-mark{", 1)[1].split("}", 1)[0]
        self.assertNotIn("border-radius", block,
                         "CSS 又回到画圆了：换字体会变椭圆")
        self.assertIn("vertical-align:super", block)


if __name__ == "__main__":
    unittest.main()
