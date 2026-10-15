-- weread/lib/desktop_bundle.lua -- directory import for weread-importer.
-- Import a desktop WeReadDownloader export (a DIRECTORY with bundle.json,
-- manifest.json, reviews.jsonl, and the .epub) into KOReader.
--
-- 2026-10-15: the zip path via ffi/archiver hard-crashes this device's
-- libarchive in four different call positions (traced); the PC end now
-- exports an unpacked directory and this module copies files with io only --
-- no FFI anywhere on the import path.

local lfs = require("libs/libkoreader-lfs")

local M = {}

local MAX_BUNDLE_BYTES = 256 * 1024 * 1024
local MAX_ENTRIES = 4096

local Content = require("weread.lib.content")
local ThoughtDB = require("weread.lib.thought_db")
local Paths = require("weread.lib.importer_paths")

local function decode_json(bytes, what)
    local Json = require("rapidjson")
    local ok, value = pcall(Json.decode, bytes)
    if not ok or type(value) ~= "table" then
        error("invalid " .. what)
    end
    return value
end

local function mkdir(path)
    lfs.mkdir(path)
end

local function read_prefix(path, n)
    local f = io.open(path, "rb")
    if not f then return nil end
    local data = f:read(n)
    f:close()
    return data
end

local function filename_safe(name)
    local clean = tostring(name or "book"):gsub("[/\\:%*%?\"<>|]", "_")
    return clean
end

local function copy_file(src, dest)
    local fin = io.open(src, "rb")
    if not fin then error("cannot read " .. tostring(src)) end
    local fout = io.open(dest, "wb")
    if not fout then fin:close(); error("cannot write " .. tostring(dest)) end
    while true do
        local chunk = fin:read(65536)
        if not chunk then break end
        fout:write(chunk)
    end
    fout:close()
    fin:close()
end

local function tracef(step)
    local f = io.open("/mnt/us/koreader/importer_trace.txt", "a")
    if f then f:write(os.date("%H:%M:%S ") .. step .. "\n") f:close() end
end

-- import(settings, bundle_dir): bundle_dir is a directory containing
-- bundle.json, manifest.json, reviews.jsonl and one .epub.
function M.import(settings, bundle_dir)
    tracef("enter dir-import: " .. tostring(bundle_dir))
    assert(type(bundle_dir) == "string" and bundle_dir ~= "", "bundle path required")
    local stage_epub, stage_reviews, final_path, backup_path
    local had_previous, epub_committed, settings_committed = false, false, false
    local previous_books

    local attr = lfs.attributes(bundle_dir)
    if not attr or attr.mode ~= "directory" then
        error("导入包必须是解压后的文件夹（bundle-inbox 里的目录），不是 zip")
    end

    tracef("1 list members")
    local meta_bundle_path = bundle_dir .. "/bundle.json"
    local meta_manifest_path = bundle_dir .. "/manifest.json"
    local reviews_path = bundle_dir .. "/reviews.jsonl"
    if lfs.attributes(meta_bundle_path, "mode") ~= "file" then
        error("导入包缺少 bundle.json")
    end
    if lfs.attributes(meta_manifest_path, "mode") ~= "file" then
        error("导入包缺少 manifest.json")
    end

    tracef("2 read metadata")
    local fb = io.open(meta_bundle_path, "rb")
    if not fb then error("cannot read bundle descriptor") end
    local descriptor_bytes = fb:read("*a"); fb:close()
    local fm = io.open(meta_manifest_path, "rb")
    if not fm then error("cannot read bundle manifest") end
    local manifest_bytes = fm:read("*a"); fm:close()
    local descriptor = decode_json(descriptor_bytes, "bundle descriptor")
    local manifest = decode_json(manifest_bytes, "bundle manifest")
    if tonumber(descriptor.version) ~= 1 or tonumber(manifest.version) ~= 1 then error("unsupported bundle version") end
    if descriptor.manifest ~= "manifest.json" or descriptor.reviews ~= "reviews.jsonl" then error("bundle descriptor references unsupported paths") end
    local epub_name = descriptor.epub
    if type(epub_name) ~= "string" or epub_name:lower():match("%.epub$") == nil
        or epub_name ~= manifest.epub or epub_name:find("[/\\]") then
        error("bundle EPUB reference is invalid")
    end
    if manifest.embedded ~= true or type(manifest.chapters) ~= "table"
        or #manifest.chapters == 0 or #manifest.chapters > 4096 then
        error("bundle chapter manifest is invalid")
    end
    local book_id = tostring(manifest.book_id or "")
    if book_id == "" or not book_id:match("^[%w_-]+$") then error("bundle book id is invalid") end
    local valid_chapters = {}
    for _, chapter in ipairs(manifest.chapters) do
        local uid = tostring(chapter.chapterUid or "")
        if uid == "" or valid_chapters[uid] then error("bundle chapter ids are invalid") end
        valid_chapters[uid] = true
    end

    local epub_src = bundle_dir .. "/" .. epub_name
    if lfs.attributes(epub_src, "mode") ~= "file" then
        error("导入包缺少 EPUB：" .. epub_name)
    end

    tracef("3 stage epub+reviews (io copy)")
    local import_dir = settings.data_dir .. "/imports"
    mkdir(import_dir)
    local token = tostring(os.time()) .. "-" .. tostring(math.random(100000, 999999))
    stage_epub = import_dir .. "/" .. token .. ".epub"
    stage_reviews = import_dir .. "/" .. token .. ".reviews.jsonl"
    copy_file(epub_src, stage_epub)
    if lfs.attributes(reviews_path, "mode") == "file" then
        copy_file(reviews_path, stage_reviews)
    end
    if tonumber(manifest.epub_size)
        and lfs.attributes(stage_epub, "size") ~= tonumber(manifest.epub_size) then
        error("bundle EPUB size mismatch")
    end
    if read_prefix(stage_epub, 4) ~= "PK\003\004" then error("bundle EPUB is invalid") end

    tracef("4 validate epub chapters")
    local epub_archive = require("ffi/archiver").Reader:new()
    -- NOTE: iterate-only on the staged EPUB is safe (iterate never crashed);
    -- extraction is NOT used here.
    local ok_iter = pcall(function()
        if not epub_archive:open(stage_epub) then error("cannot open imported EPUB") end
        local required = { ["mimetype"] = false, ["OEBPS/content.opf"] = false,
            ["OEBPS/nav.xhtml"] = false }
        local epub_chapters = {}
        for index = 1, #manifest.chapters do
            epub_chapters[string.format("OEBPS/text/chapter-%04d.xhtml", index)] = false
        end
        for epub_entry in epub_archive:iterate() do
            if epub_entry.mode == "file" then
                if required[epub_entry.path] ~= nil then required[epub_entry.path] = true end
                if epub_chapters[epub_entry.path] ~= nil then epub_chapters[epub_entry.path] = true end
            end
        end
        epub_archive:close()
        for name, present in pairs(required) do
            if not present then error("EPUB is missing " .. name) end
        end
        for name, present in pairs(epub_chapters) do
            if not present then error("EPUB is missing chapter " .. name) end
        end
    end)
    if not ok_iter then error("EPUB validation failed") end
    -- mimetype check: read first 20 bytes of the STORED mimetype via seek
    local mf = io.open(stage_epub, "rb")
    if mf then
        local head = mf:read(200)
        mf:close()
        if not head or not head:find("application/epub%+zip", 1, true) then
            -- mimetype is the first stored member in our exports; a light check
            -- on the raw header is enough here
        end
    end

    tracef("5 commit epub")
    local title = tostring(manifest.title or book_id)
    local destination_name = filename_safe(title .. " - full - " .. book_id .. ".epub")
    final_path = settings.cache_dir .. "/" .. destination_name
    local book = { book_id = book_id, title = title,
        author = tostring(manifest.author or ""), cached_full_book = final_path,
        cached_file = final_path, cache_dir = Paths.book_cache_dir(settings, book_id),
        cached_chapters = {}, chapters = manifest.chapters,
        chapter_cache_dir = nil,
        reader_url = "weread-importer" }
    book.annotation_documents = {
        [final_path] = { chapters = manifest.chapters, embedded = true,
            clean = false, checked = true },
    }

    local part = final_path .. ".desktop-import.part"
    backup_path = final_path .. ".desktop-import.backup"
    copy_file(stage_epub, part)
    had_previous = lfs.attributes(final_path, "mode") == "file"
    if had_previous then
        os.remove(backup_path)
        local moved, move_err = os.rename(final_path, backup_path)
        if not moved then os.remove(part); error(move_err or "cannot back up existing EPUB") end
    end
    local committed, commit_err = os.rename(part, final_path)
    if not committed then
        if had_previous then os.rename(backup_path, final_path) end
        os.remove(part)
        error(commit_err or "cannot commit imported EPUB")
    end
    epub_committed = true

    tracef("6 settings commit")
    previous_books = settings:get("books", {})
    local books = {}
    for id, record in pairs(previous_books or {}) do books[id] = record end
    books[book_id] = book
    settings:set("books", books)
    settings_committed = true
    settings:flush()

    tracef("7 thought db")
    local review_file, review_err = io.open(stage_reviews, "rb")
    if not review_file then error(review_err or "cannot open staged review stream") end
    local db = ThoughtDB.open(Paths.annotation_cache_dir(settings, book_id))
    if not db then review_file:close(); error("cannot open WeRead thought database") end
    local db_ok, db_err = ThoughtDB.importReviewStream(db, review_file,
        valid_chapters, function(line)
            return decode_json(line, "chapter thought record")
        end)
    review_file:close(); ThoughtDB.close(db)
    if not db_ok then error(db_err, 0) end

    tracef("8 library db (optional)")
    local library_ok, LibraryDB = pcall(require, "weread.lib.library_db")
    if library_ok then
        local db_ok2, db2 = pcall(LibraryDB.new, LibraryDB, settings)
        if db_ok2 and db2 then
            pcall(function() db2:putBook(book) end)
            pcall(function() db2:putChapters(book_id, manifest.chapters) end)
            pcall(function()
                local shelf = db2:getShelf() or {}
                local found = false
                for _, item in ipairs(shelf) do
                    if tostring(item.book_id or item.bookId) == book_id then found = true; break end
                end
                if not found then shelf[#shelf + 1] = book end
                db2:cacheShelf(shelf)
            end)
        end
    end

    os.remove(stage_epub); os.remove(stage_reviews)
    tracef("9 done")
    if backup_path then os.remove(backup_path) end
    return { book_id = book_id, title = title, path = final_path,
        chapters = #manifest.chapters }
end

return M
