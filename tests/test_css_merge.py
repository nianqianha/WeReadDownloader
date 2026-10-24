"""章节样式表与注入样式必须同时进 EPUB 的 style.css。

背景（2026-10-24）：书籍样式表曾被 tuple bug 清空（见 test_weread_accessories
的 bytes 用例），当时 `chapter_css = annotation_css or chapter_css` 的写法是
唯一能让划线样式进 EPUB 的兜底；书籍样式修回来后，这行会把刚恢复的样式再次
覆盖掉。正确行为：两者拼接，交给 style.css 的去重合并。
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
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
spec = importlib.util.spec_from_file_location("wd_css_merge", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

BOOK_CSS = ".frontCover { qrfullpage: 1; }"
ANNOTATION_CSS = ".wr-underline { border-bottom: 2px dashed #666666; }"


class _FakeLua:
    def table(self):
        return {}

    def globals(self):
        return {b"_EXPORT": {b"inject": self.inject, b"audit": None,
                              b"reader_url": lambda *args: b"https://example.invalid/"}}

    def inject(self, raw, underlines, reviews, book):
        return ("<html><body><p>Test</p></body></html>").encode(), ANNOTATION_CSS.encode()


class _FakeAPI:
    http = None
    book_css = BOOK_CSS

    def __init__(self, *args):
        pass

    def fetch_book(self, book_id):
        return ({"bookId": book_id, "title": "CSS book", "author": "T"},
                [{"chapterUid": str(n), "title": "Synthetic chapter", "wordCount": 4}
                 for n in (1, 2)])

    def chapter_html(self, book, chapter):
        return "<html><body><p>Test</p></body></html>", self.book_css

    def annotations(self, book_id, uid, thoughts, underlines):
        return ({"chapterUid": uid, "underlines": [{"range": "0-4"}] if underlines else []}, [])

    def persist_cookies(self):
        pass


class CssMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        for name, value in (("WeRead", _FakeAPI), ("lua_runtime", lambda: _FakeLua()),
                            ("_set_private_acl", lambda *args: None)):
            patcher = patch.object(wd, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _style_css(self, thoughts, underlines):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            bundle = wd.export_book("123", self.output, {}, jobs=1,
                                    want_thoughts=thoughts, want_underlines=underlines)
        epub = next(bundle.parent.glob("*.epub"))
        with zipfile.ZipFile(epub) as archive:
            return archive.read("OEBPS/style.css").decode("utf-8", "replace")

    def test_book_and_annotation_css_both_land_in_style_css(self):
        css = self._style_css(True, True)
        assert BOOK_CSS in css, css[:300]
        assert ANNOTATION_CSS in css, css[:300]

    def test_book_css_lands_even_in_plain_text_mode(self):
        css = self._style_css(False, False)
        assert BOOK_CSS in css, css[:300]
        assert ANNOTATION_CSS in css, css[:300]   # inject 仍会回它的样式块

    def test_empty_book_css_falls_back_to_annotation_css(self):
        _FakeAPI.book_css = ""
        try:
            css = self._style_css(True, True)
            assert ANNOTATION_CSS in css, css[:300]
        finally:
            _FakeAPI.book_css = BOOK_CSS
