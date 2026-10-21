#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一附录（注释和想法）测试。

对应 WD 技术文档 §1.4 的验收场景：
  1. 小书：角标改写指向附录锚点，条目数 == 角标数；
  2. 数据不一致（有角标无条目 / 有条目无角标）：AppendixError，不产残缺 EPUB；
  3. 附录是**单个**文件（W12：Calibre 上限在 XHTML 文件总数，分册会卡死）；
  4. 有划线链接但 reviews.jsonl 无对应想法：如实进 unresolved，不静默；
  5. 注不进附录（R3：注量级是想法的 38 倍，附录只放想法）。
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "weread"))
spec = importlib.util.spec_from_file_location(
    "unified_appendix", PROJ / "weread" / "unified_appendix.py")
ua = importlib.util.module_from_spec(spec)
sys.modules["unified_appendix"] = ua
spec.loader.exec_module(ua)

EPUBNS = "http://www.idpf.org/2007/ops"


def _body_with_hrefs(hrefs, extra=""):
    """构造下载阶段 Lua 插件注入后的章节正文片段。

    真实形态（三字经语料实测）：<a id 与 href 同值，class="wr-thought-link">，
    后随 WeRead 原生 <span class="wr-thought-count">[N]</span>。
    """
    links = "".join(
        '<a id="%s" class="wr-thought-link" href="%s">'
        '<span class="wr-underline">文</span></a>'
        '<span class="wr-thought-count">[3]</span>' % (h.lstrip("#"), h)
        for h in hrefs)
    return "<p>正文%s%s</p>" % (links, extra)


def _known(uid, *ranges, n_items=1):
    """构造该章 known 字典（range -> ThoughtEntry）。"""
    return {r: ua.ThoughtEntry(chapter_uid=uid, range_key=r, quote="",
                               items=[ua.ThoughtItem("作者", "内容%d" % i)
                                      for i in range(n_items)])
            for r in ranges}


class TestRewriteBadges(unittest.TestCase):
    def test_visible_badge_inserted_after_anchor(self):
        # 参考旧产物：划线链接保留，后面插入可见 [N] 角标，href 带附录文件名
        body = _body_with_hrefs(["#wrthought-111-222-0-10"])
        out, badges, missing = ua.rewrite_badges(
            body, "222", _known("222", "0-10", n_items=3), "appendix-thoughts.xhtml")
        self.assertIn('class="wr-thought-link"', out)          # 划线链接保留
        self.assertIn('class="wr-fn-ref"', out)                 # 可见角标
        self.assertIn('href="appendix-thoughts.xhtml#wrfn-222-0-10"', out)
        self.assertIn('id="wrbadge-222-0-10"', out)
        self.assertIn(">[3]</a>", out)                          # 条数
        self.assertEqual(badges, {"0-10"})
        self.assertEqual(missing, [])

    def test_native_count_badge_removed(self):
        body = _body_with_hrefs(["#wrthought-111-222-0-10"])
        out, _, _ = ua.rewrite_badges(
            body, "222", _known("222", "0-10"), "appendix-thoughts.xhtml")
        self.assertNotIn("wr-thought-count", out)

    def test_other_chapter_href_untouched(self):
        body = _body_with_hrefs(["#wrthought-111-999-0-10",
                                 "#wrthought-111-222-5-20"])
        out, badges, missing = ua.rewrite_badges(
            body, "222", _known("222", "5-20"), "appendix-thoughts.xhtml")
        self.assertIn("#wrthought-111-999-0-10", out)
        self.assertNotIn("wrbadge-999-", out)
        self.assertEqual(len(badges), 1)

    def test_unresolved_range_reported_not_dropped(self):
        body = _body_with_hrefs(["#wrthought-111-222-0-10"])
        out, badges, missing = ua.rewrite_badges(
            body, "222", {}, "appendix-thoughts.xhtml")
        self.assertEqual(missing, ["0-10"])
        self.assertEqual(len(badges), 0)
        self.assertIn("#wrthought-111-222-0-10", out)  # 链接保留，不静默删
        self.assertNotIn('class="wr-fn-ref"', out)     # 不插假角标


class TestBuildAppendix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.reviews = self.root / "reviews.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_reviews(self, rows):
        with self.reviews.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _write_body(self, uid, hrefs, extra=""):
        p = self.root / ("chapter-%s.body" % uid)
        p.write_text(_body_with_hrefs(hrefs, extra), encoding="utf-8")
        return p

    def test_small_book_ok_and_single_file(self):
        self._write_reviews([{
            "chapter_uid": "222",
            "reviews": [{"range": "0-10",
                         "pageReviews": [{"likesCount": 2,
                                          "review": {"author": {"nick": "阿甲"},
                                                     "content": "妙"}}]}],
        }])
        body = self._write_body("222", ["#wrthought-111-222-0-10"])
        files, rewritten, manifest = ua.build_appendix(
            [("222", "第一章", body)], self.reviews)
        xhtml = b"".join(d for _n, d in files)
        self.assertEqual(manifest["appendix"]["thoughts"], 1)
        self.assertEqual(manifest["appendix"]["thought_badges"], 1)
        self.assertEqual(manifest["appendix"]["notes_in_appendix"], True)
        # 单文件附录（W12）
        self.assertTrue(any(n.startswith("appendix-") for n, _ in files))
        # 附录正文含想法与返回链接。序列化后 epub:type 带前缀（EPUB 合规），
        # lxml 内存形式是 {ns}type——断言前者。
        text = xhtml.decode("utf-8")
        self.assertIn("阿甲", text)
        self.assertIn("#wrbadge-222-0-10", text)
        self.assertIn('epub:type="footnote"', text)
        self.assertIn('xmlns:epub="http://www.idpf.org/2007/ops"', text)
        # 改写后的正文被返回（角标指向附录）
        self.assertIn("#wrfn-222-0-10", dict(rewritten)["222"])

    def test_badge_without_thought_group_is_reported_not_fatal(self):
        # 有划线链接但 reviews 无对应想法组（只划线没写想法，后汉书实测
        # 17 处）：链接保留原样、不入附录、如实上报，不因此让整本书失败。
        self._write_reviews([])
        body = self._write_body("222", ["#wrthought-111-222-0-10"])
        files, rewritten, manifest = ua.build_appendix(
            [("222", "第一章", body)], self.reviews)
        xhtml = b"".join(d for _n, d in files)
        self.assertEqual(manifest["appendix"]["thoughts"], 0)
        self.assertEqual(manifest["appendix"]["thought_badges"], 0)
        self.assertEqual(manifest["appendix"]["unlinked_underlines"], ["222:0-10"])
        # 链接原样保留，没有被静默删掉
        self.assertIn("#wrthought-111-222-0-10", dict(rewritten)["222"])

    def test_rewritten_badges_always_have_entries(self):
        # R6 不变式：凡被改写、指向附录的角标，附录里必有条目。正常数据流下
        # 「改写」要求该 range 在 reviews 里有条目，所以这条断言是防自身逻辑
        # 错误的护栏——混合「有想法的 range」与「只划线没想法的 range」，
        # 前者必须改写且附录有落点，后者如实上报、原样保留。
        self._write_reviews([{
            "chapter_uid": "222",
            "reviews": [{"range": "0-10", "pageReviews": [
                {"likesCount": 0, "review": {"content": "妙"}}]}],
        }])
        body = self._write_body("222", ["#wrthought-111-222-0-10",
                                        "#wrthought-111-222-9-99"])
        files, _, manifest = ua.build_appendix(
            [("222", "第一章", body)], self.reviews)
        xhtml = b"".join(d for _n, d in files)
        ap = xhtml.decode("utf-8")
        self.assertIn('id="wrfn-222-0-10"', ap)
        self.assertEqual(manifest["appendix"]["unlinked_underlines"], ["222:9-99"])

    def test_entry_without_badge_is_reported_not_fatal(self):
        # 有条目但正文没角标（划线注入失败）：想法保留进附录、如实上报，
        # 不静默丢数据，也不让整本书导不出来。
        self._write_reviews([{
            "chapter_uid": "222",
            "reviews": [{"range": "0-10",
                         "pageReviews": [{"likesCount": 0,
                                          "review": {"content": "妙"}}]}],
        }])
        body = self._write_body("222", [])
        files, _, manifest = ua.build_appendix([("222", "第一章", body)], self.reviews)
        xhtml = b"".join(d for _n, d in files)
        self.assertIn("妙", xhtml.decode("utf-8"))
        self.assertEqual(manifest["appendix"]["thoughts"], 1)
        self.assertEqual(manifest["appendix"]["thought_badges"], 0)
        self.assertEqual(manifest["appendix"]["orphan_entries"], ["222:0-10"])

    def test_notes_enter_appendix_and_become_clickable(self):
        # R3：注与想法都进附录。下载器的死图注角标（无 src 的 img）必须转成
        # 可点 [注n]，注文从 alt 解出，并作为附录条目出现——否则阅读器里
        # 注释就是"不见了"（W13 根因）。
        self._write_reviews([{
            "chapter_uid": "222",
            "reviews": [{"range": "0-10",
                         "pageReviews": [{"likesCount": 0,
                                          "review": {"content": "妙"}}]}],
        }])
        body = self._write_body(
            "222", ["#wrthought-111-222-0-10"],
            extra='<img class="qqreader-footnote" alt="注文在此"/>')
        files, rewritten, manifest = ua.build_appendix(
            [("222", "第一章", body)], self.reviews)
        xhtml = b"".join(d for _n, d in files)
        text = xhtml.decode("utf-8")
        # 注文进附录
        self.assertIn("注文在此", text)
        self.assertIn('id="note-222-1"', text)
        self.assertEqual(manifest["appendix"]["notes"], 1)
        # 正文里死图消失、变成可点角标
        body_out = dict(rewritten)["222"]
        self.assertNotIn("qqreader-footnote", body_out)
        self.assertIn('href="appendix-notes-01.xhtml#note-222-1"', body_out)
        self.assertIn("wr-fn-mark", body_out)
        self.assertIn(">注</a>", body_out)

    def test_note_alt_hex_entities_decoded(self):
        # 真实语料里 alt 是 HTML 字符引用（&#x82E5; = 若），必须 unescape。
        self._write_reviews([])
        body = self._write_body(
            "222", [],
            extra='<img class="qqreader-footnote" alt="&#x82E5;&#xFF1A;&#x6307;&#x793A;&#x4EE3;&#x8BCD;&#xFF0C;&#x4F60;&#xFF1B;&#x5C14;&#x3002;"/>')
        files, rewritten, manifest = ua.build_appendix(
            [("222", "第一章", body)], self.reviews)
        xhtml = b"".join(d for _n, d in files)
        self.assertIn("若：指示代词，你；尔。", xhtml.decode("utf-8"))
        self.assertEqual(manifest["appendix"]["notes"], 1)

    def test_empty_note_alt_is_dropped(self):
        # alt 缺失/空：整段死图删掉，不留空角标
        self._write_reviews([])
        body = self._write_body(
            "222", [], extra='<img class="qqreader-footnote" alt=""/>')
        files, rewritten, manifest = ua.build_appendix(
            [("222", "第一章", body)], self.reviews)
        xhtml = b"".join(d for _n, d in files)
        self.assertNotIn("qqreader-footnote", dict(rewritten)["222"])
        self.assertEqual(manifest["appendix"]["notes"], 0)
        self.assertNotIn("注文在此", xhtml.decode("utf-8"))

    def test_multi_chapter_sections_in_order(self):
        self._write_reviews([
            {"chapter_uid": "1", "reviews": [{"range": "0-1", "pageReviews": [
                {"likesCount": 0, "review": {"content": "一说"}}]}]},
            {"chapter_uid": "2", "reviews": [{"range": "2-3", "pageReviews": [
                {"likesCount": 0, "review": {"content": "二话"}}]}]},
        ])
        b1 = self._write_body("1", ["#wrthought-111-1-0-1"])
        b2 = self._write_body("2", ["#wrthought-111-2-2-3"])
        files, _, manifest = ua.build_appendix(
            [("1", "甲章", b1), ("2", "乙章", b2)], self.reviews)
        xhtml = b"".join(d for _n, d in files)
        text = xhtml.decode("utf-8")
        self.assertLess(text.index("甲章"), text.index("乙章"))
        self.assertEqual(manifest["appendix"]["chapters"], 2)

    def test_same_range_paged_groups_are_merged_not_overwritten(self):
        # WeRead 分页会把一条划线的想法拆成多个 review 组（同 range），
        # 三字经实测有 2 组。覆盖写法会静默丢掉一整组想法。
        self._write_reviews([
            {"chapter_uid": "222", "reviews": [
                {"range": "0-10", "pageReviews": [
                    {"likesCount": 0, "review": {"content": "第一组"}}]},
                {"range": "0-10", "pageReviews": [
                    {"likesCount": 0, "review": {"content": "第二组"}}]},
            ]},
        ])
        body = self._write_body("222", ["#wrthought-111-222-0-10"])
        files, _, manifest = ua.build_appendix(
            [("222", "第一章", body)], self.reviews)
        xhtml = b"".join(d for _n, d in files)
        text = xhtml.decode("utf-8")
        self.assertIn("第一组", text)
        self.assertIn("第二组", text)
        # 一个 range = 附录里一条条目（items 合并），角标数仍为 1
        self.assertEqual(manifest["appendix"]["thoughts"], 1)
        self.assertEqual(manifest["appendix"]["thought_badges"], 1)


class TestAppendixScope(unittest.TestCase):
    """GUI 四档里「注释」「想法」各只要一类，附录不能把另一类也带进来。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.reviews = self.root / "reviews.jsonl"
        with self.reviews.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "chapter_uid": "222",
                "reviews": [{"range": "0-10", "pageReviews": [
                    {"likesCount": 0, "review": {"content": "妙"}}]}],
            }, ensure_ascii=False) + "\n")
        self.body = self.root / "chapter-222.body"
        self.body.write_text(
            _body_with_hrefs(["#wrthought-111-222-0-10"],
                             extra='<img class="qqreader-footnote" alt="注文在此"/>'),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_notes_only_scope_emits_no_thoughts_file(self):
        files, rewritten, manifest = ua.build_appendix(
            [("222", "第一章", self.body)], self.reviews,
            include_notes=True, include_thoughts=False)
        names = [n for n, _d in files]
        self.assertTrue(any("appendix-notes" in n for n in names), names)
        self.assertFalse(any("thoughts" in n for n in names), names)
        body_out = dict(rewritten)["222"]
        # 注转成可点角标
        self.assertIn('href="appendix-notes-01.xhtml#note-222-1"', body_out)
        # 想法不参与：不插 [N] 角标，划线链接原样保留
        self.assertNotIn('class="wr-fn-ref"', body_out)
        self.assertIn("#wrthought-111-222-0-10", body_out)
        self.assertFalse(manifest["appendix"]["thoughts_in_appendix"])
        self.assertTrue(manifest["appendix"]["notes_in_appendix"])

    def test_thoughts_only_scope_leaves_note_markers_untouched(self):
        files, rewritten, manifest = ua.build_appendix(
            [("222", "第一章", self.body)], self.reviews,
            include_notes=False, include_thoughts=True)
        names = [n for n, _d in files]
        self.assertFalse(any("notes" in n for n in names), names)
        self.assertTrue(any("thoughts" in n for n in names), names)
        body_out = dict(rewritten)["222"]
        # 注不在附录里，所以注角标也不能指向一个不存在的页
        self.assertIn("qqreader-footnote", body_out)
        self.assertNotIn("appendix-notes", body_out)
        # 想法照常插角标
        self.assertIn('class="wr-fn-ref"', body_out)
        self.assertTrue(manifest["appendix"]["thoughts_in_appendix"])
        self.assertFalse(manifest["appendix"]["notes_in_appendix"])


if __name__ == "__main__":
    unittest.main()
