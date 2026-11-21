"""划线标记与标注方式正交：关掉「划线」，正文无高亮，想法角标照常（issue #93）。

想法的 range 寄生在 /book/underlines 的返回上，所以「无划线」不是「不取数据」，
而是把空划线表交给注入层。这里守住四件事：正文里没有 wr-underline、想法角标
还在、接口照常请求、manifest 如实记 underline_marks=false——此时划线计数为 0
是「没有尝试落点」，不是「落点全部成功」，两种 0 不得混淆。
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
spec = importlib.util.spec_from_file_location("wd_marks", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

BODY = "<html><body><h1>第一章</h1><p>划线会落在这句话上。</p></body></html>"
MARK = '<span class="wr-underline" href="#wrthought-1-1-0-9">划线会落在这句话上。</span>'
BADGE = '<a class="wr-thought-count">[1]</a>'

UNDERLINES = {"chapterUid": "1", "underlines": [{"range": "0-9", "score": 1, "type": 1}]}
REVIEWS = [{"range": "0-9", "abstract": "划线会落在这句话上。", "content": "这句真好"}]


class _FakeLua:
    def table(self):
        return {}

    def globals(self):
        return {b"_EXPORT": {b"inject": self.inject, b"audit": None,
                             b"reader_url": lambda *args: b"https://example.invalid/"}}

    def inject(self, raw, underlines, reviews, book):
        data = wd.py_value(underlines) or {}
        thoughts = wd.py_value(reviews) or []
        inner = "<h1>第一章</h1><p>划线会落在这句话上。</p>"
        if data.get("underlines"):
            inner += MARK
        if thoughts:
            inner += BADGE
        return ("<html><body>" + inner + "</body></html>").encode(), b""


class _FakeAPI:
    http = None

    def fetch_book(self, book_id):
        return ({"bookId": book_id, "title": "Marked book", "author": "T"},
                [{"chapterUid": "1", "title": "第一章", "wordCount": 10}])

    def chapter_html(self, book, chapter):
        return BODY, ""

    def annotations(self, book_id, uid, thoughts, underlines):
        self.calls.append((thoughts, underlines))
        return dict(UNDERLINES), list(REVIEWS)

    def persist_cookies(self):
        pass


class MarksInEpubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.api = _FakeAPI()
        self.api.calls = []
        for name, value in (("WeRead", lambda *args: self.api),
                            ("lua_runtime", lambda: _FakeLua()),
                            ("_set_private_acl", lambda *args: None)):
            patcher = patch.object(wd, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _export(self, marks):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            bundle = wd.export_book("888", self.output, {}, jobs=1,
                                    want_thoughts=True, want_underlines=True,
                                    underline_marks=marks)
        epub = next(bundle.parent.glob("*.epub"))
        with zipfile.ZipFile(epub) as archive:
            chapter = archive.read("OEBPS/text/chapter-0001.xhtml").decode("utf-8", "replace")
            manifest = json.loads(next(bundle.parent.glob("manifest.json"))
                                  .read_text(encoding="utf-8"))
        return chapter, manifest

    def test_marks_off_drops_highlights_but_keeps_thought_badges(self):
        chapter, manifest = self._export(False)
        assert "wr-underline" not in chapter, chapter[:400]
        assert "wr-thought-count" in chapter, "关掉划线不该连想法角标一起丢"
        assert manifest["omissions"]["underline_marks"] is False
        assert manifest["omissions"]["underlines_not_placed"] == 0
        assert manifest["omissions"]["thoughts_requested"] is True

    def test_marks_on_keeps_the_previous_behavior(self):
        chapter, manifest = self._export(True)
        assert "wr-underline" in chapter
        assert manifest["omissions"]["underline_marks"] is True

    def test_annotations_are_still_fetched_without_marks(self):
        """想法的 range 寄生在划线接口上：不画划线不等于不请求。"""
        self._export(False)
        assert self.api.calls and self.api.calls[0] == (True, True), self.api.calls


class CheckpointCompatibilityTests(unittest.TestCase):
    def test_legacy_state_without_the_key_still_resumes(self):
        """旧 state.json 没有这个键，按「画划线」对待，不强迫老断点作废。"""
        old = {"book_id": "9", "want_thoughts": True, "want_underlines": True}
        assert wd._checkpoint_compatible(old, {"book_id": "9", "want_thoughts": True,
                                               "want_underlines": True,
                                               "underline_marks": True})

    def test_toggling_marks_invalidates_the_checkpoint(self):
        old = {"book_id": "9", "want_thoughts": True, "want_underlines": True}
        assert not wd._checkpoint_compatible(old, {"book_id": "9", "want_thoughts": True,
                                                   "want_underlines": True,
                                                   "underline_marks": False})

    def test_a_different_book_never_resumes(self):
        assert not wd._checkpoint_compatible(
            {"book_id": "8"}, {"book_id": "9", "want_thoughts": True,
                               "want_underlines": True, "underline_marks": True})


if __name__ == "__main__":
    unittest.main()
