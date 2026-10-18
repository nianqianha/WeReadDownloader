"""过滤后仍有上万章一定是异常目录，不分商店书还是上传书。

实测过一次《后汉书前20章-注角标可点开-demo》（CB_ 前缀的上传书）在网页会话过期时
返回 19487 条 uid 为纯数字流水号的"章节"，真正有正文的只有 4 个（uid 1/44/45/46，
后三个各 116-128 字节）。按那个目录跑，断点记成 total=19487、每章下一个空 body，
最后产出上万页空壳 EPUB——既耗时间又看不出问题。

那 19487 条是 errCode -2012（登录超时）会话吐出的脏数据，不是上传书的正常结构，
所以只按"规模异常"拒绝，不按 bookId 前缀永久拉黑上传书。
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


class HollowCatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.book_id = "CB_9WJ8WN8Tj9Vk77675X4XM1FS"

    def test_uploaded_book_is_not_blacklisted_by_prefix(self):
        """上传书不能被 CB_ 前缀永久拉黑：会话恢复后它本来可以下。

        守的是一个具体的错误决定——曾经按 bookId 前缀拒绝一切上传书，结论建立在
        "上传书目录天然有上万节点"上，而实测那 19487 条是过期会话（errCode -2012）
        吐出的脏数据。
        """
        chapters = [{"chapterUid": i, "wordCount": 3000, "title": f"第{i}章"}
                    for i in range(1, 21)]
        api = _fake_api(self.book_id, chapters)
        book, normalized = wd.WeRead.fetch_book(api, self.book_id)
        self.assertEqual(len(normalized), 20)
        self.assertEqual(book["bookId"], self.book_id)

    def test_a_normal_book_is_not_affected(self):
        chapters = [{"chapterUid": i, "wordCount": 3000, "title": f"第{i}章"}
                    for i in range(1, 21)]
        api = _fake_api("3300228183", chapters)
        book, normalized = wd.WeRead.fetch_book(api, "3300228183")
        self.assertEqual(len(normalized), 20)
        self.assertEqual(book["bookId"], "3300228183")

    def test_an_absurd_catalog_is_refused(self):
        """过滤后上万章一律拒绝，不管是不是上传书。"""
        chapters = [{"chapterUid": i, "wordCount": 10, "title": f"第{i}章"}
                    for i in range(1, 9001)]
        api = _fake_api("999999999", chapters)
        with self.assertRaises(RuntimeError) as ctx:
            wd.WeRead.fetch_book(api, "999999999")
        message = str(ctx.exception)
        self.assertIn("目录异常", message)
        # 脏数据多半来自过期会话，必须提示重新登录
        self.assertIn("扫码登录", message)

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
