"""Check actual export products and resume behavior with synthetic chapters."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / "weread/weread_desktop.py"
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location("checkpoint_engine", SOURCE)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)


class FakeLua:
    def table(self):
        return {}

    def globals(self):
        return {b"_EXPORT": {b"inject": self.inject, b"audit": None,
                              b"reader_url": lambda *args: b"https://example.invalid/"}}

    def inject(self, raw, underlines, reviews, book):
        uid = underlines[b"chapterUid"].decode()
        text = "Test"
        if underlines[b"underlines"]:
            text = '<a class="wr-underline" href="#wrthought-123-' + uid + '-0-4">Test</a>'
        if reviews:
            text += " Synthetic thought"
        return ("<html><body><p>" + text + "</p></body></html>").encode(), b""


class CheckpointModesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.fetched = []
        owner = self

        class FakeAPI:
            http = None

            def __init__(self, *args):
                pass

            def fetch_book(self, book_id):
                return ({"bookId": book_id, "title": "Synthetic book", "author": "Test"},
                        [{"chapterUid": str(n), "title": "Synthetic chapter", "wordCount": 4}
                         for n in (1, 2)])

            def chapter_html(self, book, chapter):
                owner.fetched.append(chapter["chapterUid"])
                return "<html><body><p>Test</p></body></html>", ""

            def annotations(self, book_id, uid, thoughts, underlines):
                return ({"chapterUid": uid, "underlines": [{"range": "0-4"}] if underlines else []},
                        [{"range": "0-4", "pageReviews": [{"review": {"content": "Synthetic thought"}}]}]
                        if thoughts else [])

            def persist_cookies(self):
                pass

        for name, value in (("WeRead", FakeAPI), ("lua_runtime", lambda: FakeLua()),
                            ("_set_private_acl", lambda *args: None)):
            patcher = patch.object(wd, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @property
    def workspace(self):
        return self.output / "Synthetic book-123.work"

    def export(self, thoughts, underlines):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            bundle = wd.export_book("123", self.output, {}, jobs=1,
                                    want_thoughts=thoughts, want_underlines=underlines)
        self.assertTrue(bundle.is_file())
        epub = next(bundle.parent.glob("*.epub"))
        with zipfile.ZipFile(epub) as archive:
            self.assertIsNone(archive.testzip())
            body = b"".join(archive.read(n) for n in archive.namelist() if "/text/chapter-" in n)
        self.assertEqual(b"wr-underline" in body, underlines)
        self.assertEqual(b"Synthetic thought" in body, thoughts)
        state = json.loads((self.workspace / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["completed"], state["total"]), (2, 2))
        return state

    def test_all_mode_transitions_preserve_the_requested_product(self):
        modes = ((True, True), (False, True), (False, False))
        for before in modes:
            for after in modes:
                with self.subTest(before=before, after=after):
                    self.output = Path(self.temp.name) / (str(before) + str(after))
                    self.fetched.clear()
                    self.export(*before)
                    state = self.export(*after)
                    self.assertEqual(len(self.fetched), 2 if before == after else 4)
                    self.assertEqual(state.get("want_underlines"), after[1])

    def test_old_state_without_underline_mode_is_refreshed_once(self):
        self.export(False, False)
        state_path = self.workspace / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.pop("want_underlines", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.export(False, True)
        self.export(False, True)
        self.assertEqual(len(self.fetched), 4)

    def test_mode_changes_also_invalidate_numbered_checkpoints(self):
        self.export(True, True)
        for uid in (1, 2):
            for ext in ("body", "json", "css"):
                (self.workspace / f"chapter-uid-{uid}.{ext}").rename(
                    self.workspace / f"chapter-{uid:05d}.{ext}")
        self.export(False, False)
        self.assertEqual(len(self.fetched), 4)


if __name__ == "__main__":
    unittest.main()
