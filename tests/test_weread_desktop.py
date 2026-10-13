import importlib.util
import json
import tempfile
import zipfile
from pathlib import Path
import sys

# The program modules live in weread/ and import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "weread"))
SCRIPT = (Path(__file__).resolve().parent.parent / "weread" / "weread_desktop.py")
spec = importlib.util.spec_from_file_location("weread_desktop", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)


def test_lua_core_injection_and_protocol_are_loaded():
    lua = wd.lua_runtime()
    api = lua.globals()[b"_EXPORT"]
    assert api[b"e"](b"12345")
    assert api[b"reader_url"](b"12345", b"7").startswith(b"https://weread.qq.com/web/reader/")
    html = b"<html><body><p>Alpha beta gamma delta</p></body></html>"
    underlines = {"chapterUid": "7", "underlines": [{"range": "1-5", "markText": "beta"}]}
    result = api[b"inject"](html, wd.lua_table(lua, underlines), wd.lua_table(lua, []), b"12345")
    processed = result[0] if isinstance(result, tuple) else result
    assert b"wr-underline" in processed
    assert api[b"e"](b"33867124")


def test_chapter_xhtml_normalizer_preserves_existing_body():
    lua = wd.lua_runtime()
    valid = '<html><body><p>Existing XHTML</p></body></html>'
    assert wd.normalize_chapter_xhtml(valid, lua) == valid
    plain = 'plain chapter text'
    normalized = wd.normalize_chapter_xhtml(plain, lua)
    assert '<body>' in normalized and 'plain chapter text' in normalized


def test_chapter_body_validation_rejects_empty_paid_or_unpaid_content():
    wd.validate_chapter_body('<html><body><p>Content</p></body></html>', {"chapterUid": "1", "wordCount": 41})
    wd.validate_chapter_body('<html><body><img src="cover.jpg"/></body></html>', {"chapterUid": "2", "wordCount": 1})
    try:
        wd.validate_chapter_body('<html><body></body></html>', {"chapterUid": "992", "wordCount": 41})
    except RuntimeError as exc:
        assert "empty body" in str(exc) and "wordCount=41" in str(exc)
    else:
        raise AssertionError("empty body with positive catalog wordCount was accepted")


def test_chapter_body_validation_accepts_xml_declaration_prefixed_content():
    # Regression: lxml raises ValueError on a *str* that carries an XML encoding
    # declaration, and decode_shards()/txt_to_xhtml() both emit one. The ValueError
    # used to be swallowed, so any real chapter that went through those paths was
    # reported as "decoded to an empty body despite catalog wordCount=N" -- which is
    # what permanently blocked 《超神机械师》UID 992 (a free author note, paid=0).
    decl = '<?xml version="1.0" encoding="utf-8"?>\n'
    note = (decl + '<html xmlns="http://www.w3.org/1999/xhtml"><head><title></title></head>'
            '<body>\n<p>人在外面，还没码字，可能晚上十点左右才能回家开始写。</p>\n</body></html>')
    # Must NOT raise: the text is really there.
    wd.validate_chapter_body(note, {"chapterUid": "992", "wordCount": 41})

    # A genuinely empty body must still be rejected, declaration or not.
    for uid, payload in (("992", decl + '<html xmlns="http://www.w3.org/1999/xhtml">'
                                   '<head><title></title></head><body></body></html>'),
                         ("993", '<html><body></body></html>')):
        try:
            wd.validate_chapter_body(payload, {"chapterUid": uid, "wordCount": 41})
        except RuntimeError as exc:
            assert "empty body" in str(exc) and "wordCount=41" in str(exc)
        else:
            raise AssertionError("empty body with positive catalog wordCount was accepted: " + uid)

    # wordCount absent/zero short-circuits before parsing, declaration included.
    wd.validate_chapter_body(decl + '<html><body></body></html>', {"chapterUid": "994", "wordCount": 0})


def test_body_projection_preserves_inline_content_and_tails():
    source = '<html><body>lead<p>Alpha <b>beta</b> tail</p>after</body></html>'
    fragment = wd.body_fragment(source)
    rewritten = wd.rewrite_images(fragment, {})
    assert "lead" in rewritten and "Alpha" in rewritten and "beta" in rewritten and "after" in rewritten


def test_epub_is_valid_and_embeds_thought_anchor_markup():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        epub = root / "book.epub"
        body = '<p class="wr-underline"><a id="wrthought-123-7-10-20" class="wr-thought-link" href="#wrthought-123-7-10-20">marked</a></p>'
        chapter_body = root / "chapter.body"
        chapter_body.write_text(body, encoding="utf-8")
        assets = root / "assets"
        assets.mkdir()
        wd.write_epub(epub, {"bookId": "123", "title": "Test", "author": "A"},
                      [{"chapterUid": "7", "title": "One"}], [chapter_body], "", assets, None)
        with zipfile.ZipFile(epub) as archive:
            assert archive.namelist()[0] == "mimetype"
            assert archive.read("mimetype") == b"application/epub+zip"
            chapter = archive.read("OEBPS/text/chapter-0001.xhtml")
            assert b"wrthought-123-7-10-20" in chapter
            assert b"wr-underline" in chapter
            assert b"<dc:identifier" in archive.read("OEBPS/content.opf")


def test_checkpoint_discovery_is_uid_based_and_requires_complete_annotation_files():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "chapter-00011.body").write_text('<p class="wr-underline">saved</p>', encoding="utf-8")
        (root / "chapter-00011.json").write_text(json.dumps({
            "chapter_uid": "61", "underline_ranges": 1, "embedded_markers": 1,
        }), encoding="utf-8")
        (root / "chapter-00011.css").write_text("", encoding="utf-8")
        (root / "chapter-00012.body").write_text("no marker", encoding="utf-8")
        (root / "chapter-00012.json").write_text(json.dumps({
            "chapter_uid": "62", "underline_ranges": 2, "embedded_markers": 0,
        }), encoding="utf-8")
        (root / "chapter-00012.css").write_text("", encoding="utf-8")
        chapters = [{"chapterUid": "62"}, {"chapterUid": "61"}, {"chapterUid": "63"}]
        found = wd.discover_checkpoint_files(root, chapters)
        assert set(found) == {"61"}
        assert found["61"][0].name == "chapter-00011.body"


def test_shard_md5_retry_refetches_whole_chapter():
    api = wd.WeRead.__new__(wd.WeRead)
    calls = []
    def fetch(_book, chapter):
        calls.append(str(chapter["chapterUid"]))
        if len(calls) == 1:
            raise RuntimeError("Shard MD5 mismatch")
        return "body", "css"
    api._chapter_html_once = fetch
    original_sleep = wd.time.sleep
    wd.time.sleep = lambda _delay: None
    try:
        assert api.chapter_html({}, {"chapterUid": 61}) == ("body", "css")
    finally:
        wd.time.sleep = original_sleep
    assert calls == ["61", "61"]


def test_bundle_review_stream_matches_manifest():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        rows = [
            {"chapter_uid": "7", "reviews": [{"range": "10-20", "pageReviews": [{"review": {"content": "thought"}}]}]},
            {"chapter_uid": "8", "reviews": []},
        ]
        path = root / "reviews.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        decoded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert [row["chapter_uid"] for row in decoded] == ["7", "8"]
        assert decoded[0]["reviews"][0]["range"] == "10-20"


def test_search_row_uses_real_tab_separators_and_cleans_fields():
    # Regression: search rows used to be joined with a literal backslash-t, so
    # the GUI split on "\t" saw one field and rendered zero result cards.
    row = wd.search_row({
        "bookId": "19480992", "title": "超神\t机械师", "author": "齐佩甲\n",
        "cover": "https://example.test/s.jpg", "newRating": 888,
        "newRatingCount": 23718, "category": "男生小说-游戏竞技",
    })
    fields = row.split("\t")
    assert len(fields) == len(wd.SEARCH_FIELDS) == 7
    assert fields[0] == "19480992"
    assert fields[1] == "超神 机械师"
    assert fields[2] == "齐佩甲"
    assert fields[4] == "888" and fields[5] == "23718"
    assert fields[6] == "男生小说-游戏竞技"
    assert "\\t" not in row and "\n" not in row
    assert row == wd.search_row({"bookId": 19480992, "title": "超神 机械师",
                                 "author": "齐佩甲", "cover": "https://example.test/s.jpg",
                                 "newRating": 888, "newRatingCount": 23718,
                                 "category": "男生小说-游戏竞技"})


def test_search_meta_line_carries_book_id_and_category():
    assert wd.search_meta("19480992", "男生小说-游戏竞技") == \
        "SEARCH_META\t19480992\t男生小说-游戏竞技"
    assert wd.search_meta(19480992, None) == "SEARCH_META\t19480992\t"
    assert wd.search_meta("41794030", "玄幻\n修真") == \
        "SEARCH_META\t41794030\t玄幻 修真"


def test_force_utf8_stdio_replaces_stdout_with_utf8():
    original = wd.sys.stdout
    try:
        wd.force_utf8_stdio()
        assert wd.sys.stdout.encoding.lower().replace("-", "") == "utf8"
    finally:
        wd.sys.stdout = original


if __name__ == "__main__":
    for test in (test_lua_core_injection_and_protocol_are_loaded,
                 test_chapter_xhtml_normalizer_preserves_existing_body,
                   test_chapter_body_validation_rejects_empty_paid_or_unpaid_content,
                   test_chapter_body_validation_accepts_xml_declaration_prefixed_content,
                 test_body_projection_preserves_inline_content_and_tails,
                 test_epub_is_valid_and_embeds_thought_anchor_markup,
                 test_checkpoint_discovery_is_uid_based_and_requires_complete_annotation_files,
                 test_shard_md5_retry_refetches_whole_chapter,
                 test_bundle_review_stream_matches_manifest,
                 test_search_row_uses_real_tab_separators_and_cleans_fields,
                 test_search_meta_line_carries_book_id_and_category,
                 test_force_utf8_stdio_replaces_stdout_with_utf8):
        test()
        print("PASS", test.__name__)
