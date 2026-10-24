"""Offline contract tests for the Kindle desktop-bundle importer."""
import importlib.util
import os
import time
from pathlib import Path
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
# The plugin tree is a SIBLING of the repo's parent (repo: 各类/weread-downloader,
# plugin: 各类/Kindle/03_插件源码与解压文件) -- parents[2] from this file since the
# 2026-10 move; it was parents[3] when the repo sat inside 05_补丁与其他.
def _plugin_dir():
    """外部 Lua 插件目录：WEREAD_KINDLE_ROOT 优先，其次仓库同级的 Kindle 树。"""
    roots = []
    env_root = os.environ.get("WEREAD_KINDLE_ROOT")
    if env_root:
        roots.append(Path(env_root))
    roots.append(Path(__file__).resolve().parents[2] / "Kindle")
    for root in roots:
        candidate = root / "03_插件源码与解压文件" / "weread.koplugin"
        if candidate.is_dir():
            return candidate
    return roots[-1] / "03_插件源码与解压文件" / "weread.koplugin"


PLUGIN = _plugin_dir()
BUNDLE = PLUGIN / "weread" / "lib" / "desktop_bundle.lua"

# 这个用例读的是仓库外部的 Lua 插件树（它在 Kindle 目录里，不随仓库分发）。缺席时
# 必须跳过而不是崩：否则 pytest 在收集阶段就 FileNotFoundError，报出来像"代码坏了"，
# 实际只是这台机器/这个 runner 没有那个目录。与 test_desktop_bundle_lua.py 同一约定。
if not BUNDLE.is_file():
    import pytest
    pytest.skip("本机没有外部 Lua 插件目录（可用 WEREAD_KINDLE_ROOT 指定）",
                allow_module_level=True)


def test_bundle_schema_validation():
    try:
        from lupa.luajit21 import LuaRuntime
    except ImportError:
        from lupa import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
    lua.execute(b'''package.loaded["json"] = {decode=function() return {} end, encode=function() return "{}" end}
package.loaded["weread.lib.content"] = {}
package.loaded["weread.lib.thought_db"] = {}
package.loaded["weread.lib.plugin_util"] = {tr=function(v) return v end}
''')
    source = BUNDLE.read_bytes()
    lua.execute(b'package.loaded["weread.lib.desktop_bundle"] = (function()\n' + source + b'\nend)()')
    lua.execute(b'''
local B=package.loaded["weread.lib.desktop_bundle"]
local descriptor={version=1,manifest="manifest.json",reviews="reviews.jsonl",epub="test.epub"}
local manifest={version=1,book_id="12345",title="Test",epub="test.epub",embedded=true,
 chapters={{chapterUid="7",chapterIdx=1,title="One"}}}
local reviews={{chapter_uid="7",reviews={}}}
local epub=string.char(80,75,3,4).."epub"
local ok,value=B.validate_parts(descriptor,manifest,reviews,epub)
assert(ok and value.book_id=="12345")
assert(B.is_safe_member("bundle.json"))
assert(not B.is_safe_member("../secret"))
assert(not B.is_safe_member("nested/test.epub"))
local bad={version=1,manifest="../manifest.json",reviews="reviews.jsonl",epub="test.epub"}
assert(not B.validate_parts(bad,manifest,reviews,epub))
assert(not B.validate_parts(descriptor,manifest,{},epub))
''')


def test_desktop_exporter_offline_end_to_end():
    exporter_path = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
    spec = importlib.util.spec_from_file_location("weread_desktop", exporter_path)
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    sample_reviews = [{"range": "1-5", "totalCount": 1,
        "pageReviews": [{"review": {"abstract": "beta", "content": "thought", "author": {"nick": "reader"}}}]}]
    import threading
    chapter_barrier = threading.Barrier(2)
    concurrency_lock = threading.Lock()
    in_flight = {"current": 0, "maximum": 0}

    def fake_fetch_book(self, book_id):
        return ({"bookId": book_id, "title": "Test Book", "author": "Author", "cover": None},
                [{"chapterUid": str(uid), "chapterIdx": index, "title": f"Chapter {index}", "wordCount": 4}
                 for index, uid in enumerate(("7", "8"), 1)])

    def fake_chapter_html(self, book, chapter):
        with concurrency_lock:
            in_flight["current"] += 1
            in_flight["maximum"] = max(in_flight["maximum"], in_flight["current"])
        try:
            chapter_barrier.wait(timeout=5)
            return f"<html><body><p>Alpha beta gamma delta {chapter['chapterUid']}</p></body></html>", ""
        finally:
            with concurrency_lock: in_flight["current"] -= 1

    def fake_annotations(self, book_id, chapter_uid, want_thoughts=True,
                        want_underlines=True):
        return ({"chapterUid": chapter_uid, "underlines": []}, [])

    exporter.WeRead.fetch_book = fake_fetch_book
    exporter.WeRead.chapter_html = fake_chapter_html
    exporter.WeRead.annotations = fake_annotations
    with __import__("tempfile").TemporaryDirectory() as temp:
        started = time.perf_counter()
        result = exporter.export_book("12345", Path(temp), {"api_key": "dummy", "cookies": {"wr_skey": "dummy"}, "wr_ticket": "", "wr_wrpa": ""}, jobs=2)
        elapsed = time.perf_counter() - started
        assert in_flight["maximum"] == 2
        assert elapsed < 1.5, elapsed
        import zipfile, json
        # 成品就是 EPUB 本身；导入包（.weread.zip）已停用，不要再把同一本
        # 书压第二份。manifest/reviews 作为旁挂文件留在同一目录。
        epub_path = Path(result)
        assert epub_path.suffix.lower() == ".epub", result
        assert epub_path.is_file()
        manifest = json.loads((epub_path.parent / "manifest.json").read_text(encoding="utf-8"))
        rows = [json.loads(x) for x in
                (epub_path.parent / "reviews.jsonl").read_text(encoding="utf-8").splitlines()]
        assert manifest["embedded"] is True and [c["chapterUid"] for c in manifest["chapters"]] == ["7", "8"]
        assert [row["chapter_uid"] for row in rows] == ["7", "8"]
        assert all(row["reviews"] == [] for row in rows)
        assert not list(epub_path.parent.glob("*.weread.zip")), "导入包又被生成了"
        epub = zipfile.ZipFile(result)
        try:
            chapter1 = epub.read("OEBPS/text/chapter-0001.xhtml")
            chapter2 = epub.read("OEBPS/text/chapter-0002.xhtml")
            assert b"Alpha" in chapter1 and b"gamma" in chapter1 and b"7</p>" in chapter1
            assert b"8</p>" in chapter2
        finally:
            epub.close()


if __name__ == "__main__":
    test_bundle_schema_validation()
    print("PASS bundle schema validation")
    test_desktop_exporter_offline_end_to_end()
    print("PASS offline desktop export -> EPUB + thoughts bundle")
