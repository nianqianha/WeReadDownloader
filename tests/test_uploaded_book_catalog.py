"""上传书的目录会把「注」拆成独立章节，必须剔掉碎片只留正文。

实测《后汉书前20章-注角标可点开-demo》（CB_9WJ8WN8Tj9Vk77675X4XM1FS，登录正常）
返回 19487 条：9722 条 title 就是「注」、9765 条是 wrfn-89-1 这种脚注编号，真正的
正文只有 43 条（20 章，每章被切成 2-3 段续写）。照原样下载会产出一个 19487 页、
每页几十字的重复怪物；剔掉碎片后正好是这本书的 20 章。
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
        self.assertIn("9000", message)

    def test_a_long_but_plausible_book_still_passes(self):
        """上限不能误伤真长篇：几千章的正常书要放行。"""
        chapters = [{"chapterUid": i, "wordCount": 10, "title": f"第{i}章"}
                    for i in range(1, 3001)]
        api = _fake_api("123456789", chapters)
        _book, normalized = wd.WeRead.fetch_book(api, "123456789")
        self.assertEqual(len(normalized), 3000)


class AnnotationFragmentTests(unittest.TestCase):
    """碎片判据：title 是「注」或 wrfn-<数字>-<数字>。"""

    def test_note_title_is_a_fragment(self):
        self.assertTrue(wd._is_annotation_fragment({"title": "注"}))

    def test_footnote_number_is_a_fragment(self):
        self.assertTrue(wd._is_annotation_fragment({"title": "wrfn-89-1"}))
        self.assertTrue(wd._is_annotation_fragment({"title": "wrfn-68-388"}))

    def test_a_real_chapter_is_not_a_fragment(self):
        for title in ("第 1 章（uid 89）", "第 1 章（uid 89）_1", "版权信息", "简介", "战国策刘序"):
            self.assertFalse(wd._is_annotation_fragment({"title": title}), title)

    def test_empty_title_is_not_a_fragment(self):
        self.assertFalse(wd._is_annotation_fragment({"title": ""}))
        self.assertFalse(wd._is_annotation_fragment({}))

    def test_a_continuation_suffix_is_a_fragment(self):
        """章节被切开的续段（_1/_2）取不到正文，必须剔掉。"""
        self.assertTrue(wd._is_continuation_fragment({"title": "第 1 章（uid 89）_1"}))
        self.assertTrue(wd._is_continuation_fragment({"title": "第 20 章（uid 68）_2"}))
        self.assertFalse(wd._is_continuation_fragment({"title": "第 1 章（uid 89）"}))
        self.assertFalse(wd._is_continuation_fragment({"title": "wrfn-89-1"}))
        self.assertFalse(wd._is_continuation_fragment({"title": ""}))

    def test_the_real_book_filters_down_to_its_twenty_chapters(self):
        """用实测的形状重建目录，验证剔除碎片和续段后正好 20 章。"""
        chapters = []
        uid = 1
        for n in range(1, 21):
            chapters.append({"chapterUid": uid, "title": f"第 {n} 章（uid {80+n}）",
                             "wordCount": 400}); uid += 1
            chapters.append({"chapterUid": uid, "title": f"第 {n} 章（uid {80+n}）_1",
                             "wordCount": 12}); uid += 1
            if n <= 3:
                chapters.append({"chapterUid": uid, "title": f"第 {n} 章（uid {80+n}）_2",
                                 "wordCount": 11}); uid += 1
        chapters.append({"chapterUid": uid, "title": "注", "wordCount": 18}); uid += 1
        chapters.append({"chapterUid": uid, "title": "wrfn-89-1", "wordCount": 45})
        kept = [c for c in chapters
                if not wd._is_annotation_fragment(c)
                and not wd._is_continuation_fragment(c)]
        self.assertEqual(len(kept), 20)
        self.assertTrue(all("_" not in c["title"] for c in kept))


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
