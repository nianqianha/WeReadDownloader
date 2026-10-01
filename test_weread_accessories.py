"""Accessories must never cost the reader the chapter.

The downloader treated every unavailable resource as proof that the chapter was
broken. On 盗墓笔记 25079310 that meant a single deleted decorative icon
(/Images/note.png, 404 on every WeRead CDN) threw away every one of the 66
chapters, and the book was refused with "download failed" -- for an image that
does not exist anywhere.

Three accessory paths shared that assumption and are fixed here:
  * the per-chapter image archive (tar/zip)
  * a remote <img> inside the XHTML
  * underlines and reviews
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("weread_desktop.py")
spec = importlib.util.spec_from_file_location("weread_desktop", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

import requests

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


class _Resp:
    def __init__(self, status, payload=b""):
        self.status_code = status
        self._payload = payload
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("%d Client Error" % self.status_code, response=self)

    def iter_content(self, size):
        if self._payload:
            yield self._payload

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Session:
    def __init__(self, status, payload=b""):
        self.status = status
        self.payload = payload

    def get(self, url, **kwargs):
        return _Resp(self.status, self.payload)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
GONE = (403, 404, 410)


@test
def test_a_missing_image_archive_keeps_the_chapter():
    """The tar/zip per-chapter archive is optional artwork."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for status in GONE:
            mapping, dropped = wd.extract_images(
                {"chapterUid": "7", "tar": "/Images/a.tar"}, _Session(status),
                "https://weread.qq.com/r", root, set())
            assert mapping == {}, (status, mapping)
            # The loss is reported, not silently absorbed.
            assert len(dropped) == 1, (status, dropped)
            assert "a.tar" in dropped[0], dropped


@test
def test_a_real_server_error_still_raises_for_the_archive():
    """500 is an outage, not "the artwork is gone"; it must stay visible."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            wd.extract_images({"chapterUid": "7", "tar": "/Images/a.tar"},
                              _Session(500), "https://weread.qq.com/r", Path(tmp), set())
        except requests.HTTPError:
            return
        raise AssertionError("HTTP 500 was swallowed for the image archive")


@test
def test_a_chapter_with_no_tar_is_fine():
    with tempfile.TemporaryDirectory() as tmp:
        mapping, dropped = wd.extract_images({"chapterUid": "7"}, _Session(200),
                                             "https://weread.qq.com/r", Path(tmp), set())
        assert mapping == {} and dropped == [], (mapping, dropped)


@test
def test_a_missing_remote_image_keeps_the_prose_and_leaves_no_empty_tag():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "incoming").mkdir(parents=True, exist_ok=True)
        xhtml = '<html><body><p>正文</p><img src="https://weread.qq.com/Images/note.png"/></body></html>'
        out, mapping, dropped = wd.download_remote_images(
            xhtml, _Session(404), "https://weread.qq.com/r", root, set(), "7")
        assert "正文" in out, out
        assert "<img" not in out, out
        assert mapping == {}


@test
def test_a_real_server_error_still_raises_for_a_remote_image():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "incoming").mkdir(parents=True, exist_ok=True)
        try:
            wd.download_remote_images(
                '<html><body><img src="https://x/y.png"/></body></html>',
                _Session(500), "https://weread.qq.com/r", root, set(), "7")
        except requests.HTTPError:
            return
        raise AssertionError("HTTP 500 was swallowed for a remote image")


class _Api:
    """Minimal WeRead stand-in for the annotation calls."""

    def __init__(self, underline_response=None, review_response=None,
                 underline_error=None, review_error=None):
        self._underlines = underline_response
        self._reviews = review_response
        self._underline_error = underline_error
        self._review_error = review_error

    def gateway(self, path, payload):
        if path == "/book/underlines":
            if self._underline_error:
                raise self._underline_error
            return self._underlines
        if self._review_error:
            raise self._review_error
        return self._reviews


@test
def test_failing_underlines_do_not_fail_the_chapter():
    api = _Api(underline_error=requests.ConnectionError("reset"))
    underlines, reviews = wd.WeRead.annotations(api, "1", "5")
    assert underlines["underlines"] == [], underlines
    assert reviews == []


@test
def test_a_malformed_underline_response_degrades_to_no_underlines():
    api = _Api(underline_response={"underlines": "not-a-list"}, review_response={"reviews": []})
    underlines, reviews = wd.WeRead.annotations(api, "1", "5")
    assert underlines["underlines"] == []
    assert reviews == []


@test
def test_one_failed_review_batch_does_not_discard_the_others():
    """The worst of the old behaviour: a bad batch threw away the good ones."""
    class Flaky(_Api):
        def __init__(self):
            self.calls = 0

        def gateway(self, path, payload):
            if path == "/book/underlines":
                # 75 ranges -> three batches of 30.
                return {"underlines": [{"range": str(i)} for i in range(75)]}
            self.calls += 1
            if self.calls == 2:
                raise requests.HTTPError("502 Server Error")
            return {"reviews": [{"text": f"r{self.calls}"}]}

    api = Flaky()
    underlines, reviews = wd.WeRead.annotations(api, "1", "5")
    assert len(underlines["underlines"]) == 75
    assert len(reviews) == 2, reviews
    assert underlines["chapterUid"] == "5"


@test
def test_a_malformed_review_batch_is_skipped_not_fatal():
    api = _Api(underline_response={"underlines": [{"range": "1"}]},
               review_response={"reviews": "not-a-list"})
    underlines, reviews = wd.WeRead.annotations(api, "1", "5")
    assert len(underlines["underlines"]) == 1
    assert reviews == []


@test
def test_the_happy_path_still_collects_every_review():
    api = _Api(underline_response={"underlines": [{"range": "1"}, {"range": "2"}]},
               review_response={"reviews": [{"text": "a"}, {"text": "b"}]})
    underlines, reviews = wd.WeRead.annotations(api, "1", "5")
    assert len(reviews) == 2, reviews
    assert underlines["chapterUid"] == "5"


class _CssApi:
    """A chapter whose stylesheet shard decodes to several Lua values.

    lupa returns multi-value Lua results as a Python tuple, and py_value passes
    tuples straight through. sanitize_css answering with two values therefore
    produced a tuple that nothing on the assignment line rejected, so it reached
    temp_css.write_text() and raised "TypeError: data must be str, not tuple" --
    failing the whole chapter even though the text had already decoded fine.
    Chapter 2 of 25079310 did this on every attempt, and 65 of 66 chapters were
    discarded with it.
    """

    def __init__(self, decoded, sanitized, xhtml="<html><body><p>t</p></body></html>"):
        self._decoded = decoded
        self._sanitized = sanitized
        self._xhtml = xhtml

    def reader_state(self, book, chapter_uid):
        return {"psvts": "p"}

    def chapter_shard(self, book, chapter, psvts, endpoint):
        if endpoint.endswith("e_0"):
            return "e0-bytes"
        if endpoint.endswith("e_2"):
            return "e2-bytes"
        return "e13-bytes"

    def globals(self):
        outer = self

        class _Fn:
            def __init__(self, value):
                self._value = value

            def __call__(self, *args):
                return self._value

        return {b"_EXPORT": {
            b"decode_shards": _Fn(outer._xhtml),
            b"decode_shard": _Fn(outer._decoded),
            b"sanitize_css": _Fn(outer._sanitized),
            b"txt_to_xhtml": _Fn(outer._xhtml),
        }}


def _fetch_css(api, book=None, chapter=None):
    """Call the real _chapter_html_once so the shipped guard is what is tested."""
    book = book or {"bookId": "1"}
    chapter = chapter or {"chapterUid": 2}
    api.lua = type("L", (), {"globals": api.globals})()
    xhtml, css = wd.WeRead._chapter_html_once(api, book, chapter)
    return xhtml, css


@test
def test_a_tuple_from_the_stylesheet_does_not_fail_the_chapter():
    """The regression: a malformed stylesheet cost a whole finished book.

    It also used to throw the CSS away. lupa returns multi-value Lua results as a
    tuple whose first element is the sanitized stylesheet, so keeping that first
    string recovers the chapter styling instead of shipping a book whose only
    stylesheet is the shared one. The chapter must still survive either way.
    """
    api = _CssApi("p{color:red}", ("p{color:red}", "extra"))
    xhtml, css = _fetch_css(api)
    assert isinstance(css, str), type(css)
    assert css == "p{color:red}", css
    # The text still came through: the whole point of degrading the stylesheet.
    assert "<body" in xhtml, xhtml


@test
def test_a_tuple_with_no_usable_string_still_degrades_to_no_css():
    """A multi-value answer with no text in it is still just no CSS."""
    api = _CssApi("p{color:red}", (None, 7, "   "))
    xhtml, css = _fetch_css(api)
    assert isinstance(css, str), type(css)
    assert css == "", css
    assert "<body" in xhtml, xhtml


@test
def test_a_tuple_from_the_shard_decoder_also_degrades():
    """decode_shard returning several values must not raise on .encode()."""
    api = _CssApi(("raw", "extra"), "")
    xhtml, css = _fetch_css(api)
    assert css == "", css
    assert "<body" in xhtml, xhtml


@test
def test_a_real_stylesheet_still_survives():
    api = _CssApi("p{color:red}", "p{color:red}")
    xhtml, css = _fetch_css(api)
    assert css == "p{color:red}", css


@test
def test_a_failing_stylesheet_shard_still_leaves_the_chapter_readable():
    """A stale e_2 shard is an outage on an optional resource, not a bad book."""

    class _Boom(_CssApi):
        def chapter_shard(self, book, chapter, psvts, endpoint):
            if endpoint.endswith("e_2"):
                raise requests.HTTPError("503 Server Error")
            return super().chapter_shard(book, chapter, psvts, endpoint)

    xhtml, css = _fetch_css(_Boom("p{color:red}", "p{color:red}"))
    assert css == "", css
    assert "<body" in xhtml, xhtml


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
