"""Offline contract tests for the Kindle desktop-bundle importer."""
import importlib.util
import time
from pathlib import Path
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
PLUGIN = Path(__file__).resolve().parents[3] / "03_插件源码与解压文件" / "weread.koplugin"
BUNDLE = PLUGIN / "weread" / "lib" / "desktop_bundle.lua"


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

    def fake_annotations(self, book_id, chapter_uid, want_thoughts=True):
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
        bundle = zipfile.ZipFile(result)
        assert set(bundle.namelist()) == {"bundle.json", "manifest.json", "reviews.jsonl", "Test Book - full.epub"}
        manifest = json.loads(bundle.read("manifest.json"))
        rows = [json.loads(x) for x in bundle.read("reviews.jsonl").decode().splitlines()]
        assert manifest["embedded"] is True and [c["chapterUid"] for c in manifest["chapters"]] == ["7", "8"]
        assert [row["chapter_uid"] for row in rows] == ["7", "8"]
        assert all(row["reviews"] == [] for row in rows)
        epub = zipfile.ZipFile(__import__("io").BytesIO(bundle.read(manifest["epub"])))
        try:
            chapter1 = epub.read("OEBPS/text/chapter-0001.xhtml")
            chapter2 = epub.read("OEBPS/text/chapter-0002.xhtml")
            assert b"Alpha" in chapter1 and b"gamma" in chapter1 and b"7</p>" in chapter1
            assert b"8</p>" in chapter2
        finally:
            epub.close()
        bundle.close()


if __name__ == "__main__":
    test_bundle_schema_validation()
    print("PASS bundle schema validation")
    test_desktop_exporter_offline_end_to_end()
    print("PASS offline desktop export -> EPUB + thoughts bundle")
