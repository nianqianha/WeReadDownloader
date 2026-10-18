"""上传书（CB_ 前缀）的目录不是按内容切的，不能拿它下载。

实测《后汉书前20章-注角标可点开-demo》（CB_9WJ8WN8Tj9Vk77675X4XM1FS）过滤后仍有
19487 个 uid 为纯数字流水号的"章节"，真正有正文的只有前 4 个（uid 1/44/45/46，
后三个各 116-128 字节）。按这个目录跑，断点记成 total=19487、completed=4，最后产出
一个上万页的空壳 EPUB。所以目录层直接拒绝，并把已写下的坏断点作废。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "weread"))
import weread_desktop as wd


def _catalog_payload(book_id, chapters):
    """拼一个 /web/book/chapterInfos 的响应形状。"""
    return {"data": [{"bookId": book_id, "updated": chapters}]}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fake_api(book_id, chapters, book_extra=None):
    """构造一个只够 fetch_book 用的 WeRead 替身。"""
    api = wd.WeRead.__new__(wd.WeRead)
    book = {"bookId": book_id, "title": "上传的书"}
    book.update(book_extra or {})
    api.gateway = lambda path, body: {"book": book}
    api.http = type("H", (), {"post": staticmethod(
        lambda url, **kw: _FakeResponse(_catalog_payload(book_id, chapters)))})()
    api.reader_state = lambda book: {}
    # fetch_book 用 reader_url 拼 chapterInfos 的 Referer；替身按 globals()[b"_EXPORT"]
    # [b"reader_url"](bid, None) 的形状给一个可调用对象。
    api.lua = type("L", (), {"globals": staticmethod(lambda: {
        b"_EXPORT": {b"reader_url": lambda bid, _chn: (
            b"https://weread.qq.com/web/reader/" + bid)}})})()
    return api


class UploadedBookCatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.book_id = "CB_9WJ8WN8Tj9Vk77675X4XM1FS"

    def test_uploaded_book_is_refused_with_an_explanation(self):
        chapters = [{"chapterUid": 1, "wordCount": 2000, "title": "一"}]
        chapters += [{"chapterUid": i, "wordCount": 0, "title": f"空 {i}"}
                     for i in range(44, 19488)]
        api = _fake_api(self.book_id, chapters)
        with self.assertRaises(RuntimeError) as ctx:
            wd.WeRead.fetch_book(api, self.book_id)
        message = str(ctx.exception)
        self.assertIn("上传", message)
        self.assertIn("CB_", message)
        # 必须说清"直接用本地 EPUB"，而不是甩一个目录错误
        self.assertIn("EPUB", message)

    def test_a_normal_book_is_not_affected(self):
        chapters = [{"chapterUid": i, "wordCount": 3000, "title": f"第{i}章"}
                    for i in range(1, 21)]
        api = _fake_api("3300228183", chapters)
        book, normalized = wd.WeRead.fetch_book(api, "3300228183")
        self.assertEqual(len(normalized), 20)
        self.assertEqual(book["bookId"], "3300228183")

    def test_an_absurd_store_catalog_is_refused_too(self):
        """商店书也可能命中异常切分：上万章一定是坏了，不出空壳 EPUB。"""
        chapters = [{"chapterUid": i, "wordCount": 10, "title": f"第{i}章"}
                    for i in range(1, 9001)]
        api = _fake_api("999999999", chapters)
        with self.assertRaises(RuntimeError) as ctx:
            wd.WeRead.fetch_book(api, "999999999")
        self.assertIn("目录异常", str(ctx.exception))

    def test_a_long_but_plausible_book_still_passes(self):
        """上限不能误伤真长篇：几千章的正常书要放行。"""
        chapters = [{"chapterUid": i, "wordCount": 10, "title": f"第{i}章"}
                    for i in range(1, 3001)]
        api = _fake_api("123456789", chapters)
        _book, normalized = wd.WeRead.fetch_book(api, "123456789")
        self.assertEqual(len(normalized), 3000)


class StaleHollowCheckpointTests(unittest.TestCase):
    """已经写下的坏断点（total=19487）必须被作废，而不是反复续传。"""

    def test_hollow_checkpoint_is_discarded(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        workspace = Path(temp.name)
        state_path = workspace / "state.json"
        state_path.write_text(json.dumps({
            "version": 1, "book_id": "CB_9WJ8WN8Tj9Vk77675X4XM1FS", "title": "上传的书",
            "total": 19487, "completed": 4, "chapter_uids": [str(i) for i in range(1, 19488)],
            "want_thoughts": False, "want_underlines": False,
        }), encoding="utf-8")
        # 一个已暂存的章节文件：作废时必须被清掉
        (workspace / "chapter-uid-1.body").write_text("<p>x</p>", encoding="utf-8")
        (workspace / "chapter-uid-1.css").write_text("", encoding="utf-8")

        old = json.loads(state_path.read_text(encoding="utf-8"))
        current_total = 20
        compatible = (old.get("book_id") == "CB_9WJ8WN8Tj9Vk77675X4XM1FS"
                      and all(old.get(k) is v for k, v in
                              (("want_thoughts", False), ("want_underlines", False))))
        self.assertTrue(compatible, "前提：模式字段一致，旧逻辑认为可以续传")

        old_total = int(old.get("total") or 0)
        if old_total > current_total * 2 and old_total > 200:
            compatible = False
        self.assertFalse(compatible, "total 远超当前目录时必须作废")

    def test_a_normal_checkpoint_is_kept(self):
        old = {"total": 155, "completed": 136}
        current_total = 155
        discarded = int(old["total"]) > current_total * 2 and int(old["total"]) > 200
        self.assertFalse(discarded)


if __name__ == "__main__":
    unittest.main()
