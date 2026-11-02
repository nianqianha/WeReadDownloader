"""外文书的机器译文必须按用户选的模式落进 EPUB，而且不能被随书样式藏起来。

背景（2026-11-02）：微信读书给外文书配的机器翻译不是独立接口、也不是另一本书，
它就在正文分片里——每个原文段落后面紧跟一段 class="wr-translation" 的译文。
随书样式表配了一条 `.wr-translation{display:none!important}`，由阅读器自己的
解析开关决定显示哪一半。桌面端没有那个开关，原样打包的结果是「译文在文件里、
屏幕上没有」，所以默认模式必须同时做两件事：留两半、解除隐藏。

判定用真实 struct：真正的 .body 缓存始终存完整双语，取舍发生在打包时，因此换
模式不需要重新下载。这三类用例分别守住：过滤逻辑、样式解除、端到端产物。
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
spec = importlib.util.spec_from_file_location("wd_translation", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

HIDING_CSS = ".wr-translation { display: none !important; wr-translate: normal; }"
OTHER_CSS = ".frontCover { qrfullpage: 1; }"

BODY = ("<h1>第一章</h1>"
        "<p>It is a truth universally acknowledged.</p>"
        '<p class="wr-translation">这是一条举世公认的真理。</p>'
        "<p>However little known the feelings.</p>"
        '<p class="wr-translation">无论这种人的感情多么不为人知。</p>'
        "<p>Mr Bennet replied that he had not.</p>")


class TranslationFilterTests(unittest.TestCase):
    def test_bilingual_keeps_both_halves_and_counts_translation(self):
        body, count = wd.apply_translation_mode(BODY, "bilingual")
        assert body == BODY
        assert count == 2, count

    def test_translated_headings_are_filtered_too(self):
        """章节标题也带译文：实测《英文原版 无人生还》全是 <h1 class="wr-translation">。

        只按 <p> 过滤会漏掉全书 49 条标题译文，「仅原文」里仍留着中文标题。
        """
        body = ('<h1>Chapter 1</h1>'
                '<h1 class="wr-translation">第一章</h1>'
                "<p>Text one.</p>"
                '<p class="wr-translation">正文一。</p>')
        original, count = wd.apply_translation_mode(body, "original")
        assert count == 2, count
        assert "Chapter 1" in original and "第一章" not in original
        assert "正文一。" not in original

        only, _count = wd.apply_translation_mode(body, "translation")
        assert "第一章" in only and "Chapter 1" not in only
        assert "正文一。" in only and "Text one." not in only

    def test_original_drops_translation_and_keeps_everything_else(self):
        body, count = wd.apply_translation_mode(BODY, "original")
        assert "wr-translation" not in body
        assert "It is a truth universally acknowledged." in body
        assert "Mr Bennet replied that he had not." in body
        assert "第一章" in body, "标题不是译文，不能在只留原文时被删掉"
        assert count == 2

    def test_translation_drops_only_the_paragraph_each_translation_belongs_to(self):
        body, count = wd.apply_translation_mode(BODY, "translation")
        assert "这是一条举世公认的真理。" in body
        assert "无论这种人的感情多么不为人知。" in body
        assert "universally acknowledged" not in body
        assert "However little known" not in body
        # 最后一段没有对应译文（后汉书式的不完整译本），必须原样留着，
        # 否则「仅译文」会把读者还没翻译的内容整段吞掉。
        assert "Mr Bennet replied that he had not." in body
        assert "第一章" in body
        assert count == 2

    def test_book_without_translation_is_untouched_in_every_mode(self):
        plain = "<h1>目 录</h1><p>正文一段。</p><p>正文二段。</p>"
        for mode in wd.TRANSLATION_MODES:
            body, count = wd.apply_translation_mode(plain, mode)
            assert body == plain, mode
            assert count == 0, mode

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            wd.apply_translation_mode(BODY, "chinese-only")

    def test_bytes_helper_decodes_utf8_and_passes_binary_through(self):
        count, data = wd.translate_body_bytes(BODY.encode(), "original")
        assert count == 2, count
        assert b"wr-translation" not in data
        # 不是 UTF-8 的正文（正常路径不会出现）原样返回，不拿坏编码当正文。
        binary = b"\xff\xfe<p>raw</p>"
        assert wd.translate_body_bytes(binary, "original") == (0, binary)
        # bilingual 回原字节，避免无谓重编码；篇数照样数出来。
        assert wd.translate_body_bytes(BODY.encode(), "bilingual") == (2, BODY.encode())


class RevealStyleTests(unittest.TestCase):
    def test_display_none_is_dropped_for_translation_rule_only(self):
        css = HIDING_CSS + "\n" + OTHER_CSS
        fixed = wd.reveal_translation_style(css)
        assert "display: none" not in fixed, fixed
        assert "wr-translate: normal" in fixed, "只该去掉 display，其它声明保留"
        assert OTHER_CSS in fixed, "别的规则不能被顺手改掉"

    def test_css_without_translation_rule_is_returned_unchanged(self):
        assert wd.reveal_translation_style(OTHER_CSS) == OTHER_CSS


class _FakeLua:
    def table(self):
        return {}

    def globals(self):
        return {b"_EXPORT": {b"inject": self.inject, b"audit": None,
                             b"reader_url": lambda *args: b"https://example.invalid/"}}

    def inject(self, raw, underlines, reviews, book):
        return ("<html><body><h1>第一章</h1>" + BODY.split("</h1>", 1)[1]
                + "</body></html>").encode(), b""


class _FakeAPI:
    http = None

    def __init__(self, *args):
        pass

    def fetch_book(self, book_id):
        return ({"bookId": book_id, "title": "Translated book", "author": "T"},
                [{"chapterUid": "1", "title": "第一章", "wordCount": 10}])

    def chapter_html(self, book, chapter):
        return "<html><body>" + BODY + "</body></html>", HIDING_CSS

    def annotations(self, book_id, uid, thoughts, underlines):
        return ({"chapterUid": uid, "underlines": []}, [])

    def persist_cookies(self):
        pass


class TranslationInEpubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        for name, value in (("WeRead", _FakeAPI), ("lua_runtime", lambda: _FakeLua()),
                            ("_set_private_acl", lambda *args: None)):
            patcher = patch.object(wd, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _export(self, translation):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            bundle = wd.export_book("999", self.output, {}, jobs=1,
                                    want_thoughts=False, want_underlines=False,
                                    translation=translation)
        epub = next(bundle.parent.glob("*.epub"))
        with zipfile.ZipFile(epub) as archive:
            chapter = archive.read("OEBPS/text/chapter-0001.xhtml").decode("utf-8", "replace")
            css = archive.read("OEBPS/style.css").decode("utf-8", "replace")
            manifest = json.loads(next(bundle.parent.glob("manifest.json")).read_text(encoding="utf-8"))
        return chapter, css, manifest

    def test_bilingual_epub_shows_the_translation(self):
        chapter, css, manifest = self._export("bilingual")
        assert "wr-translation" in chapter
        assert "这是一条举世公认的真理。" in chapter
        assert "display: none" not in css, "译文会被这条规则藏起来：%s" % css
        assert manifest["omissions"]["translation_mode"] == "bilingual"
        assert manifest["omissions"]["translation_paragraphs"] == 2
        assert manifest["omissions"]["chapters_with_translation"] == 1

    def test_original_epub_keeps_only_the_source_language(self):
        chapter, css, manifest = self._export("original")
        assert "wr-translation" not in chapter
        assert "universally acknowledged" in chapter
        # 只留原文时不动样式表，输出与引入译文模式前一致。
        assert "display: none" in css
        assert manifest["omissions"]["translation_mode"] == "original"

    def test_translation_epub_keeps_only_the_target_language(self):
        chapter, _css, _manifest = self._export("translation")
        assert "这是一条举世公认的真理。" in chapter
        assert "universally acknowledged" not in chapter


if __name__ == "__main__":
    unittest.main()
