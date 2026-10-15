-- Import a desktop-generated WeRead EPUB + thought bundle without loading the
-- large EPUB or complete thought dataset into Kindle RAM.
local json_ok, Json = pcall(require, "json")
if not json_ok then Json = require("rapidjson") end
local Content = require("weread.lib.content")
local ThoughtDB = require("weread.lib.thought_db")
local PluginUtil = require("weread.lib.plugin_util")
local _ = PluginUtil.tr

local M = {}
local MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
local MAX_MANIFEST_BYTES = 8 * 1024 * 1024
local MAX_DESCRIPTOR_BYTES = 64 * 1024
local MAX_REVIEW_LINE_BYTES = 16 * 1024 * 1024
local MAX_ENTRIES = 8
local MAX_CHAPTERS = 10000
local COPY_CHUNK = 64 * 1024

local function safe_member(name)
    if type(name) ~= "string" or name == "" or name:find("%z") then return false end
    name = name:gsub("\\", "/")
    if name:find("/", 1, true) or name:match("^%a:") then return false end
    for part in name:gmatch("[^/]+") do
        if part == "." or part == ".." then return false end
    end
    return name:find("/", 1, true) == nil
end

local function decode_json(data, label)
    local ok, value = pcall(function() return Json.decode(data) end)
    if not ok or type(value) ~= "table" then error((label or "bundle JSON") .. " is invalid") end
    return value
end

M.is_safe_member = safe_member

function M.validate_parts(descriptor, manifest, review_rows, epub_data)
    if type(descriptor) ~= "table" or type(manifest) ~= "table"
        or type(review_rows) ~= "table" or type(epub_data) ~= "string" then
        return false, "bundle parts have invalid types"
    end
    if tonumber(descriptor.version) ~= 1 or tonumber(manifest.version) ~= 1 then
        return false, "unsupported desktop bundle version"
    end
    if descriptor.manifest ~= "manifest.json" or descriptor.reviews ~= "reviews.jsonl"
        or not safe_member(descriptor.manifest) or not safe_member(descriptor.reviews) then
        return false, "bundle descriptor references unsupported paths"
    end
    local epub_name = descriptor.epub
    if type(epub_name) ~= "string" or not safe_member(epub_name)
        or epub_name ~= manifest.epub or not epub_name:lower():match("%.epub$") then
        return false, "bundle EPUB reference is invalid"
    end
    if tostring(manifest.book_id or "") == ""
        or not tostring(manifest.book_id):match("^[%w_-]+$") then
        return false, "bundle book id is invalid"
    end
    if manifest.embedded ~= true or type(manifest.chapters) ~= "table"
        or #manifest.chapters == 0 or #manifest.chapters > MAX_CHAPTERS then
        return false, "bundle chapter manifest is invalid"
    end
    if type(review_rows) ~= "table" or #review_rows ~= #manifest.chapters then
        return false, "bundle thought data does not match chapter manifest"
    end
    if #epub_data < 4 or epub_data:sub(1, 4) ~= string.char(80, 75, 3, 4) then
        return false, "bundle EPUB is invalid"
    end
    local valid, seen = {}, {}
    for _, chapter in ipairs(manifest.chapters) do
        local uid = tostring(chapter.chapterUid or "")
        if uid == "" or valid[uid] then return false, "bundle chapter ids are invalid" end
        valid[uid] = true
    end
    for _, row in ipairs(review_rows) do
        local uid = tostring(row.chapter_uid or "")
        if not valid[uid] or seen[uid] or type(row.reviews) ~= "table" then
            return false, "bundle thought chapter mapping is invalid"
        end
        seen[uid] = true
    end
    for uid in pairs(valid) do if not seen[uid] then return false, "bundle is missing thought chapter " .. uid end end
    return true, { book_id = tostring(manifest.book_id), chapters = manifest.chapters }
end

local function filename_safe(name)
    name = tostring(name or "WeRead - full.epub")
        :gsub("[%z\1-\31/\\:*?\"<>|]", "_")
        :gsub("^%s+", ""):gsub("%s+$", "")
    if not name:lower():match("%.epub$") then name = name .. ".epub" end
    if name == ".epub" or name == "" then name = "WeRead - full.epub" end
    return name
end

local function mkdir(path)
    local ok, FFIUtil = pcall(require, "ffi/util")
    if ok and type(FFIUtil.makePath) == "function" then return FFIUtil.makePath(path) end
    local result = os.execute("mkdir -p " .. string.format("%q", path))
    if result == true or result == 0 then return true end
    error("cannot create import staging directory")
end

local function copy_file(source, destination)
    local input, input_err = io.open(source, "rb")
    if not input then error(input_err or "cannot open staged EPUB") end
    local output, output_err = io.open(destination, "wb")
    if not output then input:close(); error(output_err or "cannot create destination EPUB") end
    local ok, err = pcall(function()
        while true do
            local chunk = input:read(COPY_CHUNK)
            if not chunk then break end
            assert(output:write(chunk))
        end
    end)
    input:close(); output:close()
    if not ok then os.remove(destination); error(err, 0) end
end

local function read_prefix(path, count)
    local file = io.open(path, "rb")
    if not file then return nil end
    local value = file:read(count)
    file:close()
    return value
end

function M.import(settings, bundle_path)
    assert(type(bundle_path) == "string" and bundle_path ~= "", "bundle path required")
    local stage_epub, stage_reviews, final_path, backup_path
    local had_previous, epub_committed, settings_committed = false, false, false
    local previous_books
    -- TRACE (remove after diagnosis): step log for the hard-crash hunt
    local tracef
    tracef = function(step)
        local f = io.open("/mnt/us/koreader/importer_trace.txt", "a")
        if f then f:write(os.date("%H:%M:%S ") .. step .. "\n") f:close() end
    end
    tracef("enter import: " .. tostring(bundle_path))
    -- The device's libarchive faults on any SECOND extraction from one
    -- Reader (memory AND path modes, traced 2026-10-15 16:26 and 16:32).
    -- Safe pattern here: one fresh Reader per extraction.
    local function extract_one(bundle, member, dest_path)
        local ar = Archiver.Reader:new()
        if not ar:open(bundle) then
            error(ar.err or "cannot reopen bundle for " .. tostring(member))
        end
        local ok_ex, err_ex = ar:extractToPath(member, dest_path)
        ar:close()
        if not ok_ex then
            error(err_ex or ar.err or "cannot extract " .. tostring(member))
        end
        return true
    end

    local lfs = require("libs/libkoreader-lfs")
tracef("1 lfs.attributes")
    local attr = lfs.attributes(bundle_path)
    if not attr or attr.mode ~= "file" then error("bundle file not found") end
    if tonumber(attr.size or 0) > MAX_BUNDLE_BYTES then error("bundle exceeds size limit") end

tracef("2 require archiver")
    local Archiver = require("ffi/archiver")
    local allowed = { ["bundle.json"] = true, ["manifest.json"] = true,
        ["reviews.jsonl"] = true }
tracef("3 archiver new")
    local archive = Archiver.Reader:new()
    local sizes, count = {}, 0
    local ok, result = xpcall(function()
        tracef("4 archive open")
        if not archive:open(bundle_path) then error(archive.err or "cannot open desktop bundle") end
        tracef("5 iterate begin")
        for entry in archive:iterate() do
            if entry.mode == "directory" then
                -- Directory entries carry no content and are ignored; file
                -- members remain restricted to the flat allowlist below.
            elseif entry.mode == "file" then
                count = count + 1
                local is_epub = type(entry.path) == "string"
                    and entry.path:lower():match("%.epub$") ~= nil
                if count > MAX_ENTRIES or not safe_member(entry.path)
                    or (not allowed[entry.path] and not is_epub) then
                    error("bundle contains an unsupported or unsafe entry")
                end
                if sizes[entry.path] then error("bundle contains duplicate entries") end
                sizes[entry.path] = tonumber(entry.size) or 0
                if entry.path == "bundle.json" and sizes[entry.path] > MAX_DESCRIPTOR_BYTES then error("bundle descriptor is too large") end
                if entry.path == "manifest.json" and sizes[entry.path] > MAX_MANIFEST_BYTES then error("bundle manifest is too large") end
            end
        end
        for name in pairs(allowed) do if not sizes[name] then error("bundle is missing " .. name) end end
        if sizes["bundle.json"] <= 0 or sizes["manifest.json"] <= 0 then error("bundle metadata is empty") end
        tracef("6a fresh-reader stage bundle.json")
        local meta_bundle_path = import_dir .. "/bundle.json.tmp"
        extract_one(bundle_path, "bundle.json", meta_bundle_path)
        tracef("6b fresh-reader stage manifest.json")
        local meta_manifest_path = import_dir .. "/manifest.json.tmp"
        extract_one(bundle_path, "manifest.json", meta_manifest_path)
        local fb = io.open(meta_bundle_path, "rb")
        if not fb then error("cannot read staged bundle descriptor") end
        local descriptor_bytes = fb:read("*a"); fb:close(); os.remove(meta_bundle_path)
        local fm = io.open(meta_manifest_path, "rb")
        if not fm then error("cannot read staged bundle manifest") end
        local manifest_bytes = fm:read("*a"); fm:close(); os.remove(meta_manifest_path)
        local descriptor = decode_json(descriptor_bytes, "bundle descriptor")
        local manifest = decode_json(manifest_bytes, "bundle manifest")
        if tonumber(descriptor.version) ~= 1 or tonumber(manifest.version) ~= 1 then error("unsupported bundle version") end
        if descriptor.manifest ~= "manifest.json" or descriptor.reviews ~= "reviews.jsonl" then error("bundle descriptor references unsupported paths") end
        local epub_name = descriptor.epub
        if type(epub_name) ~= "string" or not safe_member(epub_name)
            or epub_name ~= manifest.epub or not epub_name:lower():match("%.epub$") then
            error("bundle EPUB reference is invalid")
        end
        if manifest.embedded ~= true or type(manifest.chapters) ~= "table"
            or #manifest.chapters == 0 or #manifest.chapters > MAX_CHAPTERS then
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

        local token = tostring(os.time()) .. "-" .. tostring(math.random(100000, 999999))
        stage_epub = import_dir .. "/" .. token .. ".epub"
        stage_reviews = import_dir .. "/" .. token .. ".reviews.jsonl"
        tracef("8 close pass1")
        archive:close()
        tracef("9a fresh-reader stage epub")
        extract_one(bundle_path, epub_name, stage_epub)
        tracef("9b fresh-reader stage reviews")
        extract_one(bundle_path, "reviews.jsonl", stage_reviews)
        local archive = Archiver.Reader:new()  -- keep the cleanup-path variable defined
        if tonumber(manifest.epub_size)
            and lfs.attributes(stage_epub, "size") ~= tonumber(manifest.epub_size) then
            error("bundle EPUB size mismatch")
        end
        if read_prefix(stage_epub, 4) ~= "PK\003\004" then error("bundle EPUB is invalid") end
        tracef("12 epub archiver")
        local epub_archive = Archiver.Reader:new()
        tracef("13 epub open")
        if not epub_archive:open(stage_epub) then error(epub_archive.err or "cannot open imported EPUB") end
        local required = { ["mimetype"] = false, ["OEBPS/content.opf"] = false,
            ["OEBPS/nav.xhtml"] = false }
        local epub_chapters = {}
        for index = 1, #manifest.chapters do
            epub_chapters[string.format("OEBPS/text/chapter-%04d.xhtml", index)] = false
        end
        local epub_entries = 0
        tracef("14 epub iterate")
        for epub_entry in epub_archive:iterate() do
            if epub_entry.mode == "file" then
                epub_entries = epub_entries + 1
                if epub_entry.size > 256 * 1024 * 1024 then
                    epub_archive:close()
                    error("EPUB entry exceeds 256 MiB safety limit")
                end
                if required[epub_entry.path] ~= nil then required[epub_entry.path] = true end
                if epub_chapters[epub_entry.path] ~= nil then epub_chapters[epub_entry.path] = true end
            end
        end
        epub_archive:close()
        for name, present in pairs(required) do
            if not present then error("EPUB is missing " .. name) end
        end
        local mime_path = import_dir .. "/mimetype.check"
        if not epub_archive:extractToPath("mimetype", mime_path) then
            error(epub_archive.err or "cannot extract EPUB mimetype")
        end
        local mf = io.open(mime_path, "rb")
        if not mf then error("cannot read EPUB mimetype") end
        local mime = mf:read("*a"); mf:close(); os.remove(mime_path)
        if mime ~= "application/epub+zip" then
            error("EPUB mimetype is invalid")
        end
        for name, present in pairs(epub_chapters) do
            if not present then error("EPUB is missing chapter " .. name) end
        end
        if epub_entries > 20000 then error("EPUB has too many entries") end

        local title = tostring(manifest.title or book_id)
        local destination_name = filename_safe(title .. " - full - " .. book_id .. ".epub")
        final_path = settings.cache_dir .. "/" .. destination_name
        local book = { book_id = book_id, title = title,
            author = tostring(manifest.author or ""), cached_full_book = final_path,
            cached_file = final_path, cache_dir = Content.book_cache_dir(settings, book_id),
            cached_chapters = {}, chapters = manifest.chapters,
            chapter_cache_dir = nil,
            reader_url = require("weread.lib.protocol").reader_url(book_id) }
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

        previous_books = settings:get("books", {})
        local books = {}
        for id, record in pairs(previous_books or {}) do books[id] = record end
        books[book_id] = book
        settings:set("books", books)
        settings_committed = true
        settings:flush()

        -- Stream one chapter's reviews at a time; ThoughtDB wraps the import in
        -- one SQL transaction and replaces each included chapter's old rows.
        local review_file, review_err = io.open(stage_reviews, "rb")
        if not review_file then error(review_err or "cannot open staged review stream") end
        tracef("15 thought db open")
        local db = ThoughtDB.open(Content.annotation_cache_dir(settings, book_id))
        if not db then review_file:close(); error("cannot open WeRead thought database") end
        tracef("16 import review stream")
        local db_ok, db_err = ThoughtDB.importReviewStream(db, review_file,
            valid_chapters, function(line)
                if #line > MAX_REVIEW_LINE_BYTES then error("chapter thought record exceeds size limit") end
                return decode_json(line, "chapter thought record")
            end)
        review_file:close(); ThoughtDB.close(db)
        if not db_ok then error(db_err, 0) end
        local library_ok, LibraryDB = pcall(require, "weread.lib.library_db")
        if library_ok then
            local db_ok, db = pcall(LibraryDB.new, LibraryDB, settings)
            if db_ok and db then
                pcall(function() db:putBook(book) end)
                pcall(function() db:putChapters(book_id, manifest.chapters) end)
                pcall(function()
                    local shelf = db:getShelf() or {}
                    local found = false
                    for _, item in ipairs(shelf) do
                        if tostring(item.book_id or item.bookId) == book_id then found = true; break end
                    end
                    if not found then shelf[#shelf + 1] = book end
                    db:cacheShelf(shelf)
                end)
            end
        end
        os.remove(stage_epub); os.remove(stage_reviews)
        return { book_id = book_id, title = title, path = final_path,
            chapters = #manifest.chapters }
    end, debug.traceback)
    pcall(function() archive:close() end)
    if not ok then
        if settings_committed and previous_books then
            pcall(function()
                settings:set("books", previous_books)
                settings:flush()
            end)
        end
        if epub_committed and final_path then
            os.remove(final_path)
            if had_previous and backup_path then os.rename(backup_path, final_path) end
        end
        if stage_epub then os.remove(stage_epub) end
        if stage_reviews then os.remove(stage_reviews) end
        error(result, 0)
    end
    if backup_path then os.remove(backup_path) end
    return result
end

return M
