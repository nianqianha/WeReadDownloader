# -*- coding: utf-8 -*-
"""成品封面要取高清变体（issue #95）。

实测微信读书 CDN 对同一张封面提供多档尺寸前缀：
    s_(70×100) / t3_(143×205) / t6_(250×360) / t7_(285×411) / t9_(428×616)
`book["cover"]` 给的是 t6_ 档，成品 EPUB 的封面因此只有 250×360。这里守住：
  1. 任意尺寸前缀都归一化成 t9_，query 串保持原样；
  2. 取不到高清时回退原 URL，封面不因此丢失；
  3. 成品 EPUB 里的封面字节就是高清那一份。
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
spec = importlib.util.spec_from_file_location("wd_hd_cover", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

BODY = "<html><body><h1>第一章</h1><p>正文一段。</p></body></html>"


def _png(color: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 6), (color, 100, 150)).save(buf, format="PNG")
    return buf.getvalue()


class HdCoverUrlTests(unittest.TestCase):
    def test_every_size_prefix_becomes_t9(self):
        base = "https://cdn.weread.qq.com/weread/cover/90/YueWen_1/%sYueWen_1.jpg"
        for prefix in ("s_", "t3_", "t6_", "t7_", "t9_"):
            got = wd.hd_cover_url(base % prefix)
            self.assertTrue(got.endswith("/t9_YueWen_1.jpg"), (prefix, got))

    def test_query_string_is_preserved(self):
        url = "https://example.invalid/cover/1/t6_1.jpg?sign=abc&exp=9"
        self.assertEqual(wd.hd_cover_url(url),
                         "https://example.invalid/cover/1/t9_1.jpg?sign=abc&exp=9")

    def test_unknown_shape_is_returned_unchanged(self):
        url = "https://example.invalid/plain/cover-1.jpg"
        self.assertEqual(wd.hd_cover_url(url), url)


class _Resp:
    def __init__(self, payload: bytes, fail: bool = False):
        self._payload = payload
        self._fail = fail
        self.status_code = 500 if fail else 200

    def raise_for_status(self):
        if self._fail:
            raise wd.requests.RequestException("HTTP 500")

    def iter_content(self, size):
        for start in range(0, len(self._payload), size):
            yield self._payload[start:start + size]


class _Http:
    def __init__(self, handler):
        self.handler = handler
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.handler(url)


class _FakeLua:
    def table(self):
        return {}

    def globals(self):
        return {b"_EXPORT": {b"inject": self.inject, b"audit": None,
                             b"reader_url": lambda *args: b"https://example.invalid/"}}

    def inject(self, raw, underlines, reviews, book):
        return raw, b""


class _FakeAPI:
    def __init__(self, *args):
        self.http = None

    def fetch_book(self, book_id):
        return ({"bookId": book_id, "title": "Cover book", "author": "T",
                 "cover": "https://cdn.invalid/weread/cover/9/X/t6_X.jpg"},
                [{"chapterUid": "1", "title": "第一章", "wordCount": 10}])

    def chapter_html(self, book, chapter):
        return BODY, ""

    def annotations(self, book_id, uid, thoughts, underlines):
        return ({"chapterUid": uid, "underlines": []}, [])

    def persist_cookies(self):
        pass


class CoverDownloadTests(unittest.TestCase):
    def _export(self, handler):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name)
        api = _FakeAPI()
        api.http = _Http(handler)
        with patch.object(wd, "WeRead", lambda *args: api), \
                patch.object(wd, "lua_runtime", lambda: _FakeLua()), \
                patch.object(wd, "_set_private_acl", lambda *args: None):
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                bundle = wd.export_book("777", output, {}, jobs=1,
                                        want_thoughts=False, want_underlines=False)
        epub = next(bundle.parent.glob("*.epub"))
        with zipfile.ZipFile(epub) as archive:
            cover = archive.read("OEBPS/images/cover.png")
        return api.http.urls, cover

    def test_the_high_resolution_cover_is_the_one_packaged(self):
        hd, sd = _png(200), _png(20)

        def handler(url):
            return _Resp(hd if "/t9_" in url else sd)
        urls, cover = self._export(handler)
        self.assertTrue(urls[0].endswith("/t9_X.jpg"), urls)
        self.assertEqual(cover, hd)

    def test_a_missing_high_resolution_variant_falls_back_to_the_original(self):
        sd = _png(20)

        def handler(url):
            return _Resp(b"", fail=True) if "/t9_" in url else _Resp(sd)
        urls, cover = self._export(handler)
        self.assertTrue(any("/t9_" in u for u in urls), urls)
        self.assertTrue(urls[-1].endswith("/t6_X.jpg"), urls)
        self.assertEqual(cover, sd)


if __name__ == "__main__":
    unittest.main()
