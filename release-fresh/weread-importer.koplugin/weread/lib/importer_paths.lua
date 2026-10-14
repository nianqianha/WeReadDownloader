-- Importer-local copy of the three path helpers desktop_bundle needs.
-- Extracted from weread.koplugin/weread/lib/content.lua (verbatim bodies)
-- so the importer does not ship content.lua, whose header requires the
-- network-side modules (protocol/crypto/reader_state) that have no business
-- in an offline importer.
local function basename_safe(value)
    value = tostring(value or ""):gsub("[^%w%._-]", "_")
    if value == "" then
        value = "weread"
    end
    return value
end

function Content.book_dir_name(book_id)
    return basename_safe(book_id)
end

function Content.book_cache_dir(settings, book_id, _book)
    return settings.data_dir .. "/book-cache/" .. Content.book_dir_name(book_id)
end

function Content.annotation_cache_dir(settings, book_id)
    return settings.data_dir .. "/annotation-cache/" .. Content.book_dir_name(book_id)
end

return Content
