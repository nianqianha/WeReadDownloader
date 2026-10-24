"""分片必须按原始字节到达 Lua：latin-1→utf-8 往返会改坏非 ASCII 密文。

实测（外部用户，后汉书 3300228183 UID 90）：分片响应含非 UTF-8 字节时，
chapter_shard 的 latin-1 兜底解码 + _chapter_html_once 的 .encode()（utf-8）
一来一回改掉所有 >=0x80 的字节，分片自带的 MD5 必挂且 4 次重试全败；
相邻 154 章全过。现在 chapter_shard 返回原始 bytes，_chapter_html_once
原样传给 Lua，密文一个字节都不动。
"""
import hashlib
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
spec = importlib.util.spec_from_file_location("wd_shard_bytes", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

# 一个"分片响应"：32 位大写 MD5 前缀 + 含非 UTF-8 字节的密文体。
PAYLOAD = b"\x01\x02cipher\xff\xfe\x00\x80body\x9c" + b"A" * 64
SHARD = hashlib.md5(PAYLOAD).hexdigest().upper().encode() + PAYLOAD
XHTML = b"<html><body><p>Test</p></body></html>"


class _Response:
    def __init__(self, content):
        self.content = content
        self.encoding = None          # 服务器不声明字符集——正是出问题的形态

    def raise_for_status(self):
        pass


class _Http:
    def __init__(self, content):
        self._content = content

    def post(self, url, **kwargs):
        return _Response(self._content)


class _Fn:
    def __init__(self, fn):
        self._fn = fn

    def __call__(self, *args):
        return self._fn(*args)


class _ShardApi:
    """chapter_shard 走真方法（http.post 返回原始字节），Lua 侧捕获入参。"""

    def __init__(self, shard_content, decoded):
        self.http = _Http(shard_content)
        self.captured = []
        self._decoded = decoded
        self.lua = self

    def globals(self):
        outer = self
        return {b"_EXPORT": {
            b"make_content_params": _Fn(lambda *a: {}),
            b"reader_url": _Fn(lambda *a: b"https://weread.qq.com/reader/x"),
            b"decode_shards": _Fn(lambda *shards: outer._verify_and_decode(shards, XHTML)),
            b"decode_shard": _Fn(lambda shard: outer._verify_and_decode((shard,), outer._decoded)),
            b"sanitize_css": _Fn(lambda css: css),   # 真 Lua 收 bytes 回 bytes
            b"txt_to_xhtml": _Fn(lambda text: text),
        }}

    def _verify_and_decode(self, shards, result):
        # 模拟 content.lua 的 checked_body：核对每个分片"前 32 字节 MD5 == 密文"。
        self.captured.extend(shards)
        for shard in shards:
            if not shard:
                continue
            expected, body = shard[:32], shard[32:]
            actual = hashlib.md5(body).hexdigest().upper().encode()
            if actual != expected:
                raise AssertionError(
                    "shard bytes were corrupted in transit: expected %r, got %r"
                    % (expected, actual))
        return result

    def reader_state(self, book, chapter_uid):
        return {"psvts": "p"}

    # chapter_html 的重试包装调用真方法；用真实现（凭据/Lua 由 fake 提供）。
    _chapter_html_once = wd.WeRead._chapter_html_once
    chapter_shard = wd.WeRead.chapter_shard


def _run(shard_content, decoded=XHTML):
    api = _ShardApi(shard_content, decoded)
    book = {"bookId": "3300228183"}
    chapter = {"chapterUid": 90, "title": "Synthetic", "wordCount": 4}
    xhtml, css = wd.WeRead.chapter_html(api, book, chapter)
    return xhtml, css, api.captured


def test_binary_shard_reaches_lua_byte_for_byte():
    """非 UTF-8 分片必须逐字节到达 Lua，MD5 才对得上。"""
    xhtml, css, captured = _run(SHARD)
    assert XHTML.decode() in xhtml, xhtml[:120]
    assert SHARD in captured, "shard bytes were altered before reaching Lua"


def test_css_shard_also_travels_as_raw_bytes():
    """e_2 样式表分片同样走字节直通；解出的 CSS 正常返回。"""
    css_text = ".frontCover { qrfullpage: 1; }"
    decoded = css_text.encode("utf-8")
    _, css, captured = _run(SHARD, decoded)
    assert css == css_text, css
    assert SHARD in captured, "css shard bytes were altered"


def test_empty_shard_degrades_to_no_css():
    _, css, _ = _run(b"", b"")
    assert css == "", css
