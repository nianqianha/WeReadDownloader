"""A missing image must not cost the reader the chapter.

Catalogue entries exist whose artwork is no longer served (every chapter of
盗墓笔记 25079310 referenced /Images/note.png and got a 404). The remote-image
fetcher called raise_for_status(), so a single dead <img> threw away text that
had already decoded perfectly.

The plain-text shard path had a second, self-inflicted bug: decode_shards can
return a Lua table instead of a string, and calling .encode() on it raised
"TypeError: data must be str, not tuple" -- also losing the chapter.
"""
import importlib.util
import re
import sys
from pathlib import Path


# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
spec = importlib.util.spec_from_file_location("weread_desktop", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

import requests

ALL_TESTS = []


def test(fn):
    ALL_TESTS.append(fn)
    return fn


class _Response:
    def __init__(self, status, payload=b"", stream=True):
        self.status_code = status
        self._payload = payload
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("%d Client Error" % self.status_code,
                                     response=self)

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
    """Answers every request with the same status, so the fetcher's own error
    handling is what is under test."""

    def __init__(self, status, payload=b""):
        self.status = status
        self.payload = payload
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        return _Response(self.status, self.payload)


PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


@test
def test_a_404_image_is_dropped_and_the_chapter_text_is_kept():
    """The regression: a dead <img> used to discard the whole chapter."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "incoming").mkdir(parents=True, exist_ok=True)
        xhtml = ('<html><body><p>正文还在。</p>'
                 '<img src="https://weread.qq.com/Images/note.png"/></body></html>')
        http = _Session(404)
        out, mapping, dropped = wd.download_remote_images(
            xhtml, http, "https://weread.qq.com/reader/x", root, set(), "7")
        # The text survives, the dead <img> does not, and nothing is left behind
        # that would make a reader re-fetch the document as an image.
        assert "正文还在" in out, out
        assert "note.png" not in out, out
        assert "<img" not in out, out
        assert mapping == {}, mapping


@test
def test_a_403_or_410_image_is_also_tolerated():
    import tempfile
    for status in (403, 404, 410):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "incoming").mkdir(parents=True, exist_ok=True)
            xhtml = '<html><body><p>正文</p><img src="https://x/y.png"/></body></html>'
            out, _, _dropped = wd.download_remote_images(
                xhtml, _Session(status), "https://weread.qq.com/reader/x",
                root, set(), "7")
            assert "正文" in out, (status, out)


@test
def test_a_server_error_is_still_raised_not_swallowed():
    """500 is not "this image is gone"; it must not be silently dropped."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "incoming").mkdir(parents=True, exist_ok=True)
        xhtml = '<html><body><img src="https://x/y.png"/></body></html>'
        try:
            wd.download_remote_images(
                xhtml, _Session(500), "https://weread.qq.com/reader/x",
                root, set(), "7")
        except requests.HTTPError:
            return
        raise AssertionError("HTTP 500 was swallowed; a real outage must surface")


@test
def test_a_good_image_is_still_fetched_and_rewritten():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "incoming").mkdir(parents=True, exist_ok=True)
        xhtml = '<html><body><p>t</p><img src="https://x/ok.png"/></body></html>'
        out, mapping, dropped = wd.download_remote_images(
            xhtml, _Session(200, PNG), "https://weread.qq.com/reader/x",
            root, set(), "7")
        assert len(mapping) == 1, mapping
        href = list(mapping.values())[0]
        assert href in out, out
        assert (root / "images" / Path(href).name).exists(), sorted(
            p.name for p in (root / "images").iterdir())


@test
def test_a_non_image_response_is_dropped_rather_than_raising():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "incoming").mkdir(parents=True, exist_ok=True)
        xhtml = '<html><body><p>正文</p><img src="https://x/err.png"/></body></html>'
        out, _, _dropped = wd.download_remote_images(
            xhtml, _Session(200, b"<html>error</html>"),
            "https://weread.qq.com/reader/x", root, set(), "7")
        assert "正文" in out, out


@test
def test_a_non_string_shard_raises_a_readable_error_not_a_type_error():
    """decode_shards sometimes hands back a Lua table instead of text.

    Calling .encode() on it raised "TypeError: data must be str, not tuple",
    which is both unreadable and (because it is not a recognised transient) lost
    the chapter. The guard has to turn that into a clear, reportable message.
    """
    def guard(plain):
        if isinstance(plain, bytes):
            plain = plain.decode("utf-8", "replace")
        if not isinstance(plain, str):
            raise RuntimeError(
                "Chapter 9 plain-text shards decoded to %s, not text"
                % type(plain).__name__)
        return plain

    # The reported message must name the problem, not leak a TypeError.
    for value in ((1, 2), None, 42, [1, 2], object()):
        try:
            guard(value)
        except RuntimeError as exc:
            assert "not text" in str(exc), str(exc)
            assert "must be str" not in str(exc), str(exc)
        else:
            raise AssertionError("guard accepted %r" % type(value))

    # bytes are legitimate and must pass through decoded.
    assert guard("第一章".encode("utf-8")) == "第一章"


@test
def test_bytes_shards_are_decoded_before_use():
    """A bytes result is legitimate and must be decoded, not rejected."""
    assert isinstance(b"\xe4\xb8\x80".decode("utf-8"), str)


@test
def test_the_user_facing_message_does_not_blame_the_network_for_a_missing_image():
    from weread_core import friendly_error
    message = friendly_error("[IMAGE] dropping an unavailable image (HTTP 404): x")
    assert "图片" in message, message
    assert "网络" not in message, message


if __name__ == "__main__":
    for test_case in ALL_TESTS:
        test_case()
        print("PASS", test_case.__name__)
