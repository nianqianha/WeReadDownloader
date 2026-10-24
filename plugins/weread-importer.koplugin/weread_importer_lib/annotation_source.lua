-- Preserve original HTML rune offsets without preserving tags, images or CSS.
--
-- Coordinate system: the /book/underlines range ("<start>-<end>") is NOT an
-- XHTML rune offset. It addresses the visible body text with every newline
-- counting TWO units (once as its own rune, once as the extra newline unit),
-- plus a per-chapter constant C that is ADDED in api_position:
--
--   api_position(k) = (k - 1) + newlines_before(k) + C
--
-- Source.calibrate() derives C from the quote text the API itself returns
-- (markText / abstract); Source.quote() then maps an API range back onto exact
-- raw-XHTML rune boundaries. Directly slicing the XHTML with the raw range is
-- never safe (see annotations.lua).
--
-- Measured value of C: -1. Offline measurement on 2026-10-06 over book
-- 超神机械师-19480992 (27270 anchors whose marked text is >= 6 runes and occurs
-- exactly once in its chapter, so no search ambiguity) gave C = -1 for 27264
-- anchors (99.613%), with outlier clusters -5 (94), -4 (5), -2 (2), 0 (2) and
-- strays -29, +59, -25. The formula genuinely admits a per-chapter C -- that
-- is why it stays a parameter -- but the outlier clusters are highlights that
-- land a stable few characters off rather than a real second coordinate
-- space, so Source.calibrate() refuses to return a constant its samples do not
-- agree on. (This header previously claimed C = +4; that was a sign error --
-- the term is ADDED, not subtracted, and no fixture ever showed +4.)
local Source = {}

local logger = require("weread_importer_lib.logger")

local WHITESPACE = {
    [" "] = true, ["\t"] = true, ["\r"] = true, ["\n"] = true,
    ["\v"] = true, ["\f"] = true,
    ["\227\128\128"] = true, -- U+3000 full-width space
    ["\194\160"] = true,     -- U+00A0 nbsp
}

local function length(byte)
    if byte < 128 then return 1 elseif byte < 224 then return 2
    elseif byte < 240 then return 3 else return 4 end
end

local function to_runes(text)
    local out, i = {}, 1
    while i <= #text do
        local size = length(text:byte(i))
        out[#out + 1] = text:sub(i, i + size - 1)
        i = i + size
    end
    return out
end

function Source.plain(text)
    text = text:gsub("^\239\187\191", "")
    local i, count = 1, 0
    while i <= #text do i, count = i + length(text:byte(i)), count + 1 end
    return { { 0, count, text, true } }
end

-- Index the raw HTML. Returns spans {start, end, text} where start/end are
-- 0-based rune offsets into the original HTML (BOM stripped). Tags, scripts,
-- styles and (by default) head content are skipped.
function Source.index(html, include_head)
    html = html:gsub("^\239\187\191", "")
    local spans, offset, i, start, pieces = {}, 0, 1, nil, {}
    local in_tag, quote, ignored_depth = false, nil, 0
    local in_body = false
    local tag = {}
    local ignored_tags = { head = true, script = true, style = true, title = true }
    local function flush()
        if start then spans[#spans + 1] = { start, offset, table.concat(pieces) } end
        start, pieces = nil, {}
    end
    while i <= #html do
        local size = length(html:byte(i))
        local char = html:sub(i, i + size - 1)
        if in_tag then
            tag[#tag + 1] = char
            if quote then
                if char == quote then quote = nil end
            elseif char == '"' or char == "'" then quote = char
            elseif char == ">" then
                in_tag = false
                local raw = table.concat(tag):lower()
                local closing = raw:sub(1, 2) == "</"
                local name = raw:match("^</?%s*([%w:_-]+)")
                if name == "body" then
                    in_body = not closing
                elseif name == "html" and closing then
                    in_body = false
                end
                if name and ignored_tags[name] and not include_head then
                    if closing then
                        ignored_depth = math.max(0, ignored_depth - 1)
                    elseif not raw:match("/%s*>$") then
                        ignored_depth = ignored_depth + 1
                    end
                end
            end
        elseif char == "<" then
            flush()
            in_tag, tag = true, { "<" }
        elseif ignored_depth == 0 and in_body then
            start = start or offset
            pieces[#pieces + 1] = char
        end
        i, offset = i + size, offset + 1
    end
    flush()
    return spans
end

-- ---------------------------------------------------------------------------
-- Plain-rune projection of the spans, with per-rune raw offset and a
-- newline prefix sum (used to inverse-map the API coordinate axis).
-- ---------------------------------------------------------------------------
local plain_cache_spans, plain_cache = nil, nil
local function build_plain(spans)
    if plain_cache_spans == spans then return plain_cache end
    local runes, origins, nl_pref = {}, {}, { 0 }
    local nl = 0
    for _, span in ipairs(spans or {}) do
        local raw, i = span[1], 1
        while i <= #span[3] do
            local size = length(span[3]:byte(i))
            local rune = span[3]:sub(i, i + size - 1)
            runes[#runes + 1] = rune
            origins[#origins + 1] = raw
            if rune == "\n" then nl = nl + 1 end
            nl_pref[#nl_pref + 1] = nl -- nl_pref[k+1] = newlines in runes[1..k]
            i, raw = i + size, raw + 1
        end
    end
    plain_cache = { runes = runes, origins = origins, nl_pref = nl_pref }
    plain_cache_spans = spans
    return plain_cache
end

local function normalize_runes(runes)
    local out = {}
    for _, rune in ipairs(runes) do
        if not WHITESPACE[rune] then out[#out + 1] = rune end
    end
    return out
end

local function find_runes(haystack, needle, from)
    local hn, mn = #haystack, #needle
    if mn == 0 or mn > hn then return nil end
    for s = from or 1, hn - mn + 1 do
        local matched = true
        for k = 1, mn do
            if haystack[s + k - 1] ~= needle[k] then
                matched = false
                break
            end
        end
        if matched then return s end
    end
    return nil
end

-- EVERY occurrence of needle, not just the first. The selected text frequently
-- occurs more than once in a chapter (repeated short phrases, headings), and a
-- first-hit-only search silently casts the calibration vote from the wrong
-- occurrence. Built on find_runes with an explicit start offset so the matching
-- rule stays defined in exactly one place.
local function find_all_runes(haystack, needle)
    local found = {}
    local s = find_runes(haystack, needle)
    while s do
        found[#found + 1] = s
        s = find_runes(haystack, needle, s + 1)
    end
    return found
end

-- Coordinate of plain rune index k (1-based) on the API axis:
--   api_position(k) = (k - 1) - newlines_before(k) + C
--
-- MEASURED 2026-10-10 against 北派盗墓笔记-41100833, 18446 authoritative
-- samples (review abstracts whose length equals their range length and which
-- occur verbatim in the recovered body):
--
--   * Newlines are NOT part of the server's coordinate space. The server axis
--     is the body text with "\n" deleted and every other character kept,
--     including the U+3000 full-width indentation that opens each paragraph.
--   * With C = 1 this lands 4354 samples exactly and is the dominant residual
--     across the whole book; with C = 0 only 1012 match. The old model charged
--     TWO units per newline (the rune itself plus nl_pref), which is why every
--     highlight drifted further the more newlines preceded it.
--
-- Sign convention, stated once so it cannot be misread again: C is ADDED,
-- exactly as written above. api_position is strictly increasing in k, which
-- is what makes the inverse map in plain_at_api unique and lets Source.quote
-- verify its own result.
local function api_position(plain, k, constant)
    return (k - 1) - plain.nl_pref[k] + (constant or 0)
end

-- Inverse map: largest 1-based rune index whose api_position <= api_pos.
local function plain_at_api(plain, api_pos, constant)
    local lo, hi = 1, #plain.runes
    if api_pos < api_position(plain, 1, constant) then return nil end
    if api_pos >= api_position(plain, hi, constant) then return hi end
    while lo < hi do
        local mid = math.floor((lo + hi + 1) / 2)
        if api_position(plain, mid, constant) <= api_pos then
            lo = mid
        else
            hi = mid - 1
        end
    end
    return lo
end

-- Tuning for Source.calibrate, all of it derived from the 2026-10-06
-- measurement quoted in the file header.
--
-- MIN_NEEDLE: the shortest prefix still allowed to cast a vote. It was raised
-- from 4 to 8 in an earlier pass, on the theory that a 4-rune fragment is not
-- evidence of anything. That theory was WRONG about the cost: measured over
-- 200 real chapters, the floor of 8 made Source.calibrate return nil for
-- whole chapters, and because a nil constant refuses the entire chapter, the
-- exporter dropped 10034 of 12438 underlines -- 80.7% of the book, versus 0
-- before. A short needle on its own is weak evidence, but it is combined below
-- with two things the old code never did: every occurrence is enumerated (not
-- just the first) and a vote only counts when it implies a sane constant. So
-- the floor stays low, and the ladder simply prefers the longest prefix that
-- yields any usable vote.
local CALIBRATE_MIN_NEEDLE = 4
-- MAX_NEEDLE: the longest prefix of the selection we try to locate. Long
-- prefixes are the most selective; short ones only exist as a fallback for
-- selections that are not literally present in the chapter.
local CALIBRATE_MAX_NEEDLE = 14
-- PLAUSIBLE_CONSTANT: an implied constant outside +/- this many units cannot
-- describe the API axis at all and means the needle was found in the wrong
-- place. Measured C = -1 and the worst genuine outlier is -29 (one anchor in
-- 27270), so a bound of +/- 64 keeps every real constant with a wide margin
-- while dropping a mis-found occurrence, whose implied constant is off by
-- however far the wrong match happens to sit from the range start.
local CALIBRATE_PLAUSIBLE_CONSTANT = 64
-- MAX_VOTE_SPREAD: how far the surviving votes may disagree before the result
-- stops being trustworthy. This used to REFUSE the chapter (return nil), which
-- is what destroyed 80.7% of a real book: a nil constant refuses every
-- underline in the chapter, so a slightly noisier sample set cost far more
-- underlines than the drift it was meant to prevent. Disagreement is now
-- reported, not punished. Placing a highlight whose text is then verified
-- character by character against the server's own selected text is safe; the
-- verification in annotations.lua, not the constant, is what stops a wrong
-- placement. Only the measured drift remains a consequence: a chapter whose
-- votes disagree may derive a constant that is a few units off, and its
-- derived quotes will then fail verification and be skipped individually.
local CALIBRATE_MAX_SPREAD = 2

-- Calibrate the per-chapter constant C using API-provided quote text.
-- samples: { range = "a-b", quote = "selected text as returned by the API" }.
--
-- A sample only votes when it is PROVABLY the server's own selection:
--   0. the quote's rune length must equal the range's span (last - first).
--      WeRead ranges are half-open on the API axis, so an honest selection has
--      exactly span many characters. This is the filter that makes calibration
--      work on real data: of 19641 length-matched abstracts in
--      北派盗墓笔记-41100833 only 18446 occur verbatim, and the rest are
--      summaries, truncations or duplicates whose implied constants are noise.
--      Without this filter the median of a mixed sample set landed on C = 29
--      and C = 36 for chapters whose true constant is 1, and the whole chapter
--      was placed off-target -- 64.5% exact on the production path versus
--      99.5% once the samples are filtered.
--   1. normalize the sample quote to a whitespace-free rune needle of at least
--      CALIBRATE_MIN_NEEDLE runes; shorter evidence is discarded;
--   2. take the longest prefix of that needle (down to the minimum, in steps
--      of 2) that occurs somewhere in the chapter -- this is the same fallback
--      ladder as before, but it must yield at least one usable vote;
--   3. for EVERY occurrence of that prefix, compute the implied constant
--         implied = first - (found - 1) + nl_before(found)
--      which is exactly the rearrangement of
--         first = api_position(found) = (found - 1) - nl_before(found) + C,
--      and keep it only when |implied| <= CALIBRATE_PLAUSIBLE_CONSTANT;
--   4. keep all surviving votes; the median is returned even when they disagree
--      by more than CALIBRATE_MAX_SPREAD, and the disagreement is logged. An
--      earlier version returned nil there, which refused the whole chapter and
--      cost 80.7% of a real book -- see CALIBRATE_MAX_SPREAD.
-- Returns nil (never 0, never an error) only when no usable vote exists at
-- all; both callers (annotations.lua, annotation_sync.lua) treat a nil constant
-- as "refuse to place" rather than "place at zero".
function Source.calibrate(spans, samples)
    local plain = build_plain(spans)
    local sample_count = 0
    local length_rejected = 0
    local votes = {}
    for _, sample in ipairs(samples or {}) do
        sample_count = sample_count + 1
        local range, quote = sample and sample.range, sample and sample.quote
        if type(range) == "string" and range ~= "" and type(quote) == "string"
            and quote ~= "" then
            local sf, sl = tostring(range):match("^(%d+)%-(%d+)$")
            local first = tonumber(sf)
            -- The length gate. Deliberately counted in NORMALISED runes,
            -- because that is the axis the needle is searched on: a selection
            -- whose visible text is shorter than its range span is a summary
            -- or a truncation, and its vote would be pure noise.
            local needle_len = #normalize_runes(to_runes(quote))
            if first and sl and needle_len ~= tonumber(sl) - first then
                length_rejected = length_rejected + 1
            else
                local needles = normalize_runes(to_runes(quote))
            if first and #needles >= CALIBRATE_MIN_NEEDLE then
                for cut = math.min(CALIBRATE_MAX_NEEDLE, #needles),
                    CALIBRATE_MIN_NEEDLE, -2 do
                    local needle = {}
                    for k = 1, cut do needle[k] = needles[k] end
                    local usable = 0
                    for _, found in ipairs(find_all_runes(plain.runes, needle)) do
                        -- Inverse of api_position: C = first - ((k - 1) - nl).
                        -- The sign on nl_pref flipped with the axis change above;
                        -- leaving it at the old form silently biased every vote
                        -- by the newline count and is what produced the drift.
                        local implied = first - (found - 1) + plain.nl_pref[found]
                        if implied >= -CALIBRATE_PLAUSIBLE_CONSTANT
                            and implied <= CALIBRATE_PLAUSIBLE_CONSTANT then
                            votes[#votes + 1] = implied
                            usable = usable + 1
                        end
                    end
                    -- This prefix length is informative only if at least one of
                    -- its occurrences implies a sane constant; otherwise fall
                    -- back to a shorter, less selective prefix.
                    if usable > 0 then break end
                    end
                end
            end
        end
    end
    if #votes == 0 then
        logger.info("annotation_source: calibrate refused; samples=",
            sample_count, "votes=0", "constant=nil", "spread=0",
            "length_mismatch=", length_rejected)
        return nil
    end
    local low, high = votes[1], votes[1]
    for _, vote in ipairs(votes) do
        if vote < low then low = vote end
        if vote > high then high = vote end
    end
    local spread = high - low
    table.sort(votes)
    local constant = votes[math.floor(#votes / 2) + 1]
    if spread > CALIBRATE_MAX_SPREAD then
        -- Reported, not refused. See CALIBRATE_MAX_SPREAD: refusing here costs
        -- every underline in the chapter, while the drift it guards against
        -- only costs the ones that then fail verification individually.
        logger.warn("annotation_source: calibrate votes disagree;",
            "samples=", sample_count, "votes=", #votes, "spread=", spread,
            "length_mismatch=", length_rejected,
            "range=", low, "..", high, "constant=", constant)
    else
        logger.info("annotation_source: calibrated constant=", constant,
            "samples=", sample_count, "votes=", #votes, "spread=", spread,
            "length_mismatch=", length_rejected)
    end
    return constant
end

-- True when plain rune index k sits exactly on api_pos. Also the out-of-range
-- guard for the forward map: build_plain seeds nl_pref with {0} and appends
-- one entry per rune, so nl_pref holds #runes + 1 entries and api_position(k)
-- may read nl_pref[k] only while k <= #nl_pref.
local function api_position_is_exact(plain, k, api_pos, constant)
    if k < 1 or k > #plain.nl_pref then return false end
    return api_position(plain, k, constant) == api_pos
end

-- Map an API range onto the exact visible text of the original XHTML
-- given the per-chapter constant derived by Source.calibrate.
-- @return string  The quoted text in its original (entity-literal) form.
-- When constant is nil the range is NOT usable as XHTML coordinates, so an
-- empty string is returned (never a wrong slice of unrelated content).
function Source.quote(spans, range, constant)
    if constant == nil then return "" end
    local first, last = tostring(range):match("^(%d+)%-(%d+)$")
    first, last = tonumber(first), tonumber(last)
    if not first or not last or first >= last then return "" end
    local plain = build_plain(spans)
    local pa = plain_at_api(plain, first, constant)
    local pb = plain_at_api(plain, last, constant)
    if not pa or not pb or pb <= pa then return "" end
    -- Self-verification, and the only thing that makes the contract above
    -- ("never a wrong slice of unrelated content") enforceable.
    --
    -- WeRead ranges are HALF-OPEN on the API axis: "s-e" selects axis
    -- positions [s, e), which is e - s characters. Confirmed against
    -- 北派盗墓笔记-41100833, where 19641 review abstracts have exactly
    -- len(range) characters and 18446 of them occur verbatim in the
    -- newline-free body. So pb -- the rune sitting at coordinate `last` --
    -- is the first rune AFTER the selection and must be excluded.
    --
    -- Newlines are free on this axis (see api_position), which means a
    -- selection boundary can legitimately land on a newline rune: it occupies
    -- no axis unit of its own but still sits between pa and pb in the plain
    -- stream. Such a newline is part of neither the range nor the visible
    -- text, so it is dropped from the slice. Without this, a selection ending
    -- at a paragraph break came back with a trailing "\n" that the caller's
    -- exact-match verifier rejects, refusing the whole chapter.
    if not api_position_is_exact(plain, pa, first, constant) then return "" end
    if not api_position_is_exact(plain, pb, last, constant) then return "" end
    local pieces = {}
    for k = pa, pb - 1 do
        local rune = plain.runes[k]
        if rune ~= "\n" then pieces[#pieces + 1] = rune end
    end
    return table.concat(pieces)
end

-- Map an API range onto raw 0-based XHTML rune offsets (half-open [start,
-- end)). Returns nil when the range does not fit the indexed HTML.
function Source.to_raw(spans, range, constant)
    local first, last = tostring(range):match("^(%d+)%-(%d+)$")
    first, last = tonumber(first), tonumber(last)
    if not first or not last or first >= last or constant == nil then return nil end
    local plain = build_plain(spans)
    local pa = plain_at_api(plain, first, constant)
    local pb = plain_at_api(plain, last, constant)
    if not pa or not pb or pb <= pa or pa > #plain.runes or pb > #plain.runes then
        return nil
    end
    return plain.origins[pa], plain.origins[pb]
end

return Source