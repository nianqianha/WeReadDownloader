local Bundle = require("weread.lib.desktop_bundle")
local function expect(ok, message)
    if not ok then error(message or "assertion failed") end
end

expect(Bundle.is_safe_member("bundle.json"), "simple bundle path should be safe")
expect(Bundle.is_safe_member("book.epub"), "EPUB filename should be safe")
expect(not Bundle.is_safe_member("../outside"), "parent traversal must be rejected")
expect(not Bundle.is_safe_member("dir/book.epub"), "nested paths must be rejected")
expect(not Bundle.is_safe_member("C:\\outside"), "drive paths must be rejected")

local manifest = {
    version = 1,
    book_id = "12345",
    title = "Test Book",
    epub = "Test Book - full.epub",
    embedded = true,
    chapters = {
        { chapterUid = "7", chapterIdx = 1, title = "Chapter" },
    },
}
local reviews = {
    { chapter_uid = "7", reviews = {
        { range = "1-5", totalCount = 1,
            pageReviews = { { review = { content = "thought" } } } },
    } },
}
local ok, validated = Bundle.validate_parts({
    version = 1, manifest = "manifest.json", reviews = "reviews.jsonl",
    epub = "Test Book - full.epub",
}, manifest, reviews, "PK\003\004epub")
expect(ok and validated.book_id == "12345" and #validated.chapters == 1,
    "valid desktop bundle should validate")
local bad_ok = Bundle.validate_parts({
    version = 1, manifest = "../manifest.json", reviews = "reviews.jsonl",
    epub = "Test Book - full.epub",
}, manifest, reviews, "PK\003\004epub")
expect(not bad_ok, "unsafe metadata path must be rejected")
local mismatch_ok = Bundle.validate_parts({
    version = 1, manifest = "manifest.json", reviews = "reviews.jsonl",
    epub = "Test Book - full.epub",
}, manifest, {}, "PK\003\004epub")
expect(not mismatch_ok, "missing chapter thought rows must be rejected")
print("PASS desktop bundle path/schema validation")
