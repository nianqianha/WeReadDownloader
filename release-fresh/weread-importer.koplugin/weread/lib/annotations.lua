--[[--
微信读书划线标注处理

将微信读书的划线数据注入到章节 HTML 中。
划线 range 格式：如 "383-415"，表示 HTML 字符串的 rune 索引（包含所有标签）。
--]] --

local logger = require("weread.lib.logger")

local Annotations = {}

-- 下划线 CSS 样式
Annotations.UNDERLINE_CSS = [[
.wr-underline {
    border-bottom: 2px dashed #666666;
    padding-bottom: 2px;
}
.wr-thought-count {
    font-size: 0.8em;
    color: #000000;
    border: 1px solid #777777;
    padding: 0 2px;
    margin-left: 3px;
    vertical-align: super;
    line-height: 1;
    font-weight: bold;
}
]]

-- 想法链接保持正文原有样式；划线本身就是可点击区域。
Annotations.THOUGHT_LINK_CSS = [[
.wr-thought-link{text-decoration:none;color:inherit;}
]]

--- 去除字符串开头的 UTF-8 BOM（\ufeff）。
-- WeRead 的部分章节会携带 BOM，而下划线 range 索引通常不包含这个字符。
local function stripLeadingBOM(s)
    if type(s) ~= "string" then return s end
    -- UTF-8 BOM: EF BB BF
    if s:sub(1, 3) == "\xef\xbb\xbf" then
        return s:sub(4)
    end
    return s
end

--- 将 UTF-8 字符串转换为 rune 数组。
local function toRunes(str)
    local runes = {}
    local i = 1
    local len = #str
    while i <= len do
        local byte = string.byte(str, i)
        local rune_len
        if byte < 0x80 then
            rune_len = 1
        elseif byte < 0xE0 then
            rune_len = 2
        elseif byte < 0xF0 then
            rune_len = 3
        else
            rune_len = 4
        end
        runes[#runes + 1] = str:sub(i, i + rune_len - 1)
        i = i + rune_len
    end
    return runes
end

--- 解析 range 字符串（如 "383-415"）为起止位置。
-- 注意：微信读书 API 返回的 range 是 0 索引（JavaScript 惯例），
-- 但 Lua 使用 1 索引。需要加 1 转换。
local function parseRange(range_str)
    if type(range_str) ~= "string" or range_str == "" then
        return nil, nil
    end
    local start_str, end_str = range_str:match("^(%d+)%-(%d+)$")
    if not start_str or not end_str then
        return nil, nil
    end
    -- 加 1 转换为 Lua 1 索引
    local start = tonumber(start_str) + 1
    local end_pos = tonumber(end_str) + 1
    if not start or not end_pos or start >= end_pos then
        return nil, nil
    end
    return start, end_pos
end

--- snapEndToSafeBoundary 将 end 位置向前（回退）调整，使其不落在 HTML 标签或实体内部。
-- @return number position  调整后的 end（Lua 半开区间）
-- @return boolean moved  是否真的移动过坐标
-- 调用方必须把 moved 记进日志：一次静默的坐标移动就是把划线画偏的另一种形式，
-- 它和"落点错误"一样需要可计数，否则读者只会看到偏移却查不到原因。
local function snapEndToSafeBoundary(runes, start, end_pos)
    local n = #runes
    if end_pos <= start or end_pos > n then
        return end_pos, false
    end
    -- 检查是否在 HTML 标签内部：从 end-1 向前扫描
    for i = end_pos - 1, start, -1 do
        if runes[i] == '>' then
            break -- 遇到 >，说明不在标签内部
        end
        if runes[i] == '<' then
            return i, true -- 在标签内部，回退到 < 之前
        end
    end
    -- 检查是否在 HTML 实体内部：从 end-1 向前扫描（实体最长约 10 字符）
    for i = end_pos - 1, start, -1 do
        if i < end_pos - 12 then break end
        local r = runes[i]
        if r == ';' or r == '<' or r == '>' then
            break -- 遇到分隔符，说明不在实体内部
        end
        if r == '&' then
            return i, true -- 在实体内部，回退到 & 之前
        end
    end
    return end_pos, false
end

--- snapStartToSafeBoundary 将 start 位置向后（前进）调整，使其不落在 HTML 标签或实体内部。
-- @return number position  调整后的 start
-- @return boolean moved  是否真的移动过坐标（同样必须由调用方记录）
local function snapStartToSafeBoundary(runes, start, end_pos)
    local n = #runes
    if start < 0 or start >= end_pos or start >= n then
        return start, false
    end
    -- 检查是否在 HTML 标签内部：从 start-1 向前扫描
    for i = start - 1, 0, -1 do
        if i < start - 200 then break end
        if runes[i] == '>' then
            break -- 不在标签内部
        end
        if runes[i] == '<' then
            -- 在标签内部，向前找到闭合 >
            for j = start, n do
                if runes[j] == '>' then
                    return j + 1, true
                end
            end
            break
        end
    end
    -- 检查是否在 HTML 实体内部：从 start-1 向前扫描
    for i = start - 1, 0, -1 do
        if i < start - 12 then break end
        local r = runes[i]
        if r == ';' or r == '<' or r == '>' then
            break
        end
        if r == '&' then
            -- 在实体内部，向前找到闭合 ;
            for j = start, n do
                if j >= start + 12 then break end
                if runes[j] == ';' then
                    return j + 1, true
                end
            end
            break
        end
    end
    return start, false
end

--- wrapTextSegments 将 rune 切片中的每个文本段（非标签部分）分别用 <span> 包裹。
-- 遇到 HTML 标签时自动关闭/重开 span，确保不跨越标签边界。
local function wrapTextSegments(runes, className)
    local openTag = '<span class="' .. className .. '">'
    local closeTag = '</span>'

    local result = {}
    local inTag = false
    local textBuf = {}

    -- 包裹一个文本段
    local function wrapSegment(seg)
        if #seg == 0 then return end
        -- 检查是否纯空白
        local hasContent = false
        for _, r in ipairs(seg) do
            if not r:match("^%s$") then
                hasContent = true
                break
            end
        end
        if hasContent then
            result[#result + 1] = openTag
            for _, r in ipairs(seg) do
                result[#result + 1] = r
            end
            result[#result + 1] = closeTag
        else
            for _, r in ipairs(seg) do
                result[#result + 1] = r
            end
        end
    end

    -- 刷新文本缓冲区
    local function flushTextBuf()
        if #textBuf == 0 then return end
        wrapSegment(textBuf)
        textBuf = {}
    end

    for _, r in ipairs(runes) do
        if r == '<' then
            flushTextBuf()
            inTag = true
            result[#result + 1] = r
        elseif r == '>' then
            inTag = false
            result[#result + 1] = r
        elseif inTag then
            result[#result + 1] = r
        else
            -- 文本字符：缓冲到 textBuf
            textBuf[#textBuf + 1] = r
        end
    end
    flushTextBuf()

    return result
end

--- HTML 转义
local function htmlEscape(text)
    text = tostring(text or "")
    text = text:gsub("&", "&amp;")
    text = text:gsub("<", "&lt;")
    text = text:gsub(">", "&gt;")
    text = text:gsub('"', "&quot;")
    return text
end

-- The range returned by /book/underlines is not consistently expressed in
-- the decoded XHTML coordinate space. In particular, on some books it is
-- based on the rendered chapter text, while the downloader receives XHTML
-- containing tags and entities. Applying that range directly can therefore
-- put a thought on a nearby but unrelated sentence. Prefer the quote carried
-- by the underline/review and map it back to raw XHTML rune boundaries.
local function isMatchWhitespace(rune)
    return rune == " " or rune == "\t" or rune == "\r" or rune == "\n"
        or rune == "\v" or rune == "\f" or rune == "　"
        or rune == "\194\160"
end

local function decodeSimpleEntity(entity)
    local entities = {
        ["&amp;"] = "&", ["&lt;"] = "<", ["&gt;"] = ">",
        ["&quot;"] = '"', ["&apos;"] = "'", ["&nbsp;"] = "\194\160",
    }
    return entities[entity] or entity
end

--- scanVisible 扫描 runes[from..to] 并产出"规范可见文本"的 rune 数组。
-- 规范化规则（唯一权威定义，全文只有这一处实现）：
--   1. 跳过标签内部（< ... > 之间的所有字符），标签本身不是正文；
--   2. 解码 &amp; &lt; &gt; &quot; &apos; &nbsp; 六个简单实体，&nbsp; 解码成
--      U+00A0，而 U+00A0 又在第 3 步被丢弃 —— 与原 visibleTextIndex 行为一致；
--   3. 丢弃 isMatchWhitespace 命中的 rune（含 U+3000 与 U+00A0）。
-- 任何"定位"和"验证"都必须走这里：needle 与 haystack 只要有一侧换了归一化，
-- 相等判断就会假阴性，把本来正确的划线当成不匹配丢掉。
-- @param function|nil emit  可选回调 emit(start_pos, end_pos)，记录每个可见 rune
--   在原始 XHTML 中的半开区间；nil 时只产出文本（验证时不需要区间）。
local function scanVisible(runes, from, to, emit)
    local out = {}
    local i, in_tag = from, false
    while i <= to do
        local rune = runes[i]
        if rune == "<" then
            in_tag = true
            i = i + 1
        elseif in_tag then
            if rune == ">" then in_tag = false end
            i = i + 1
        elseif rune == "&" then
            local entity = {}
            local j = i
            while j <= to and j - i <= 12 do
                entity[#entity + 1] = runes[j]
                if runes[j] == ";" then break end
                j = j + 1
            end
            local text = table.concat(entity)
            if text:sub(-1) == ";" then
                text = decodeSimpleEntity(text)
                for _, decoded in ipairs(toRunes(text)) do
                    if not isMatchWhitespace(decoded) then
                        if emit then emit(i, j + 1) end
                        out[#out + 1] = decoded
                    end
                end
                i = j + 1
            else
                if not isMatchWhitespace(rune) then
                    if emit then emit(i, i + 1) end
                    out[#out + 1] = rune
                end
                i = i + 1
            end
        else
            if not isMatchWhitespace(rune) then
                if emit then emit(i, i + 1) end
                out[#out + 1] = rune
            end
            i = i + 1
        end
    end
    return out
end

--- normalizeVisible 把一段原始 XHTML rune（或一段纯文本）转成唯一可比较的规范形式。
-- needle（候选引文，通常是服务端返回的纯文本）与 haystack（XHTML 切片）
-- 必须走同一个函数，否则验证会产生假不匹配。
local function normalizeVisible(rune_list)
    if type(rune_list) == "string" then rune_list = toRunes(rune_list) end
    return table.concat(scanVisible(rune_list, 1, #rune_list, nil))
end

--- visibleTextOf 取 runes[start_pos .. end_pos-1]（Lua 半开区间，与注入时的
-- inner 切片方式一致）并归一化，得到"这段区间实际覆盖了什么正文"。
-- 验证阶段必须用它，而不是直接把引文当成答案 —— 只有它能证明落点上的文字
-- 真的就是那串引文，一个字符都不多、不少。
local function visibleTextOf(runes, start_pos, end_pos)
    if start_pos >= end_pos then return "" end
    local slice = {}
    for i = start_pos, end_pos - 1 do
        slice[#slice + 1] = runes[i]
    end
    return normalizeVisible(slice)
end

local function visibleTextIndex(html)
    local source = toRunes(stripLeadingBOM(html))
    local starts, ends = {}, {}
    local flat = scanVisible(source, 1, #source, function(from_pos, to_pos)
        starts[#starts + 1] = from_pos
        ends[#ends + 1] = to_pos
    end)
    return { flat = flat, starts = starts, ends = ends }
end

--- quoteCandidate 收集这条划线可用于"落点"的候选引文，按权威性分组返回。
-- 两组必须分开而不是合成一个列表：服务端已经告诉我们选中了哪段字，若那份
-- 原文在正文里找不到，就说明本地正文与服务端不一致 —— 此时再拿公式反推的
-- range_quote 顶上，等于用不可靠的猜测覆盖服务端的事实，正是「相差几个字符」
-- 的原始形态。所以 (b) 只在 (a) 根本不存在时才允许使用。
-- @return table authoritative  服务端选中文本（markText/bookmarkText/rangeText/text）
-- @return table derived        由 range 公式反推的文本，仅在没有 (a) 时使用
-- @return boolean has_context_window  是否存在 review 侧的 abstract/contextAbstract/
--   markText；它们是上下文窗口而非选区，只能用来产出拒绝理由，不能用来落点。
local function quoteCandidate(underline, review, range_quote)
    local authoritative, derived = {}, {}

    -- (a) 服务端返回的"选中文本"：markText / bookmarkText / rangeText / text。
    -- 这就是用户当时真正选中的那段字，权威性最高，而且根本不需要坐标公式 ——
    -- 章节常数 C 算错也不会影响它。
    if type(underline) == "table" then
        for _, key in ipairs({ "markText", "bookmarkText", "rangeText", "text" }) do
            local quote = underline[key]
            if type(quote) == "string" and quote ~= "" then
                authoritative[#authoritative + 1] = quote
            end
        end
    end

    -- (b) 由公式反推的 range_quote：它来自 Source.quote()，依赖每章常数 C，
    -- C 算错时整段会平移几个字符，因此和 (a) 一样要求全文相等。
    if type(range_quote) == "string" and range_quote ~= "" then
        derived[#derived + 1] = range_quote
    end

    -- (c) 想法侧的 abstract / contextAbstract / markText。
    --
    -- MEASURED 2026-10-10 on 北派盗墓笔记-41100833: the review abstract IS the
    -- user's selected text, not a window around it. Checked against the
    -- official WeRead client, whose underlines for chapter 1 are character
    -- identical to these abstracts on all 11 sampled ranges (the two that
    -- differ are only cut off by a page boundary in the screenshot). Across
    -- the book 94.7% of abstracts occur exactly once in the chapter body.
    --
    -- So abstract is promoted to a REAL candidate, ranked BELOW the underline
    -- row's own selected text but ABOVE the coordinate-derived quote. The old
    -- code refused to use it at all (changelog issue #14 worried the abstract
    -- was a context window), which meant every chapter without markText fell
    -- back to the coordinate formula and drifted.
    --
    -- Safety is unchanged by this promotion: resolveAnnotationRange still
    -- requires the candidate to match the landing text character for character
    -- (REASON.TEXT_MISMATCH otherwise). An abstract that really was a context
    -- window simply will not match and is refused with a countable reason --
    -- the same treatment it got before, minus the guaranteed failure.
    local page = review and type(review.pageReviews) == "table"
        and review.pageReviews[1] or nil
    local item = page and (page.review or page) or nil
    local has_context_window = false
    if type(item) == "table" then
        -- abstract first: it is the measured selection. markText on the review
        -- is the same claim from the other direction and is kept as a fallback.
        for _, key in ipairs({ "abstract", "markText" }) do
            local text = item[key]
            if type(text) == "string" and text ~= "" then
                authoritative[#authoritative + 1] = text
            end
        end
        -- contextAbstract stays a context window: it is never a selection.
        local context = item.contextAbstract
        if type(context) == "string" and context ~= "" then
            has_context_window = true
        end
    end

    return authoritative, derived, has_context_window
end

local function findQuoteRange(index, quote, minimum_start)
    -- needle 用与验证相同的 normalizeVisible：命中之后才能谈得上"全文相等"。
    -- 旧实现只丢空白，遇到 &amp; 之类的引文永远匹配不上，等于白丢一条划线。
    local needle = toRunes(normalizeVisible(quote))
    if #needle == 0 or #needle > #index.flat then return nil end
    for start = 1, #index.flat - #needle + 1 do
        local raw_start = index.starts[start]
        if not minimum_start or raw_start >= minimum_start then
            local matched = true
            for offset, rune in ipairs(needle) do
                if index.flat[start + offset - 1] ~= rune then
                    matched = false
                    break
                end
            end
            if matched then
                return raw_start, index.ends[start + #needle - 1]
            end
        end
    end
    return nil
end

--- 审计原因码。这些字符串是对外契约的一部分：Python 侧按字面量统计拒写数量，
-- 发布后只能新增、不能改写含义。
local REASON = {
    OK = "ok",                                     -- 已落点且全文相等
    NO_CANDIDATE = "no_candidate",                 -- 划线本身没带任何可验证文本
    QUOTE_NOT_FOUND = "quote_not_found",           -- 有候选，但本章正文里找不到
    TEXT_MISMATCH = "text_mismatch",               -- 找到了位置，但正文与引文不全等
    ABSTRACT_IS_CONTEXT_WINDOW = "abstract_is_context_window", -- 只有上下文窗口，无法验证
    OVERLAP = "overlap",                           -- 与前一条划线重叠，或区间塌缩
    OUT_OF_RANGE = "out_of_range",                 -- 落点超出 XHTML 边界或区间非法
    BAD_RANGE = "bad_range",                       -- range 字段本身无法解析
}

--- recordRangeAudit 记录一条审计结果。collect 为 nil 时整段空转，
-- 保证生产注入路径不为审计付出任何分配。
local function recordRangeAudit(collect, range_str, placed, reason, quote, marked)
    if not collect then return end
    collect[#collect + 1] = {
        range = range_str or "",
        placed = placed and true or false,
        reason = reason,
        quote = quote or "",
        marked = marked or "",
    }
end

--- refuse 拒写一条划线：写日志 + 记审计，二者必须成对，否则拒写会重新变成
-- 看不见的缺陷（丢一条划线可以接受，丢得没有痕迹不行）。
local function refuse(collect, range_str, reason, quote)
    logger.info("annotations: refused range", range_str or "", reason)
    recordRangeAudit(collect, range_str, false, reason, quote, "")
end

--- resolveAnnotationRange 决定一条划线在 XHTML 中的落点。
-- 唯一的放置规则：候选引文的全文必须与落点区间上的正文完全相等。
-- 任何"只匹配一部分"的落点都不接受 —— 那正是划线被画偏几个字符的根因。
-- @param table ctx  { index, runes, underline, review, range_quote, minimum_start }
-- @return number|nil start_pos, number|nil end_pos, string reason, string needle
--   needle 为参与决策的规范化引文（ok 时即被验证通过的那一条）。
local function resolveAnnotationRange(ctx)
    local authoritative, derived, has_context_window =
        quoteCandidate(ctx.underline, ctx.review, ctx.range_quote)
    local saw_mismatch = false
    local needle = ""

    -- 服务端原文优先，且它一旦存在就是唯一依据：找不到就拒画，不拿公式反推的
    -- 猜测去顶替（见 quoteCandidate 的说明）。
    local groups = { authoritative }
    if #authoritative == 0 then groups = { derived } end

    for _, candidates in ipairs(groups) do
        for _, quote in ipairs(candidates) do
            local start_pos, end_pos = findQuoteRange(ctx.index, quote, ctx.minimum_start)
            if start_pos and end_pos then
                needle = normalizeVisible(quote)
                if visibleTextOf(ctx.runes, start_pos, end_pos) == needle then
                    return start_pos, end_pos, REASON.OK, needle
                end
                saw_mismatch = true
            end
        end
        if #authoritative > 0 then break end
    end

    -- 只有上下文窗口：这是刻意拒写，而不是"再试一个更短的引文"。
    if has_context_window then
        return nil, nil, REASON.ABSTRACT_IS_CONTEXT_WINDOW, ""
    end
    if saw_mismatch then
        return nil, nil, REASON.TEXT_MISMATCH, needle
    end
    if #authoritative > 0 or #derived > 0 then
        return nil, nil, REASON.QUOTE_NOT_FOUND, ""
    end
    return nil, nil, REASON.NO_CANDIDATE, ""
end

--- 把 range 想法数据里的非安全字符替换为下划线，用于内部锚点 id。
local function idSafe(text)
    return tostring(text or ""):gsub("[^%w%._%-]", "_")
end

--- 生成想法内部锚点 id：wrthought-<book>-<chap>-<start>-<end>。
-- range_str 形如 "383-415"，其中 - 是 start/end 分隔符。
local function thoughtAnchorId(book_id, chapter_uid, range_str)
    return "wrthought-" .. idSafe(book_id) .. "-" .. idSafe(chapter_uid) .. "-" .. tostring(range_str or "")
end

Annotations.thoughtAnchorId = thoughtAnchorId

--- Convert one range review into the small, plain-text records used by the
-- native thought dialog. Keeping these fields separate in SQLite means a tap
-- never has to decode the chapter JSON or start an HTML renderer.
-- @table range_review  A range review containing .pageReviews
-- @return table  Ordered thought items
function Annotations.buildThoughtPopupItems(range_review)
    if type(range_review) ~= "table" or type(range_review.pageReviews) ~= "table"
        or #range_review.pageReviews == 0 then
        return {}
    end

    local items = {}
    for i, pr in ipairs(range_review.pageReviews) do
        local review = pr.review or {}
        local author = review.author or {}
        local abstract = nil
        if i == 1 then
            abstract = review.abstract or review.contextAbstract
            if type(abstract) ~= "string" or abstract == "" then
                abstract = nil
            end
        end
        items[#items + 1] = {
            abstract = abstract,
            author = tostring(author.nick or author.name or "匿名"),
            content = tostring(review.content or ""),
            likes_count = tonumber(pr.likesCount) or 0,
        }
    end
    return items
end

--- injectUnderlinesCore 在 HTML 中注入下划线标记 —— 落点决策的唯一实现。
-- @string html  完整的原始 HTML（包含 body 标签）
-- @table  underlines  划线列表
-- @table  thought_reviews  想法数据 map
-- @table|nil collect  审计接收表；nil 表示纯生产路径，不做任何审计记录。
--   注入与审计共用这一份实现，因此"审计说的"与"注入做的"不可能分叉。
-- @return processed_html
local function injectUnderlinesCore(html, underlines, thought_reviews, chapter_uid,
        book_id, collect)
    if type(html) ~= "string" or html == "" then
        return html
    end
    if type(underlines) ~= "table" or #underlines == 0 then
        return html
    end

    -- 单次注入不变式：源 HTML 必须保持原始（range 坐标空间），若已包含注入
    -- 标记则原样返回，避免二次注入导致坐标漂移（wr_diag2 缓存曾踩过此坑）。
    -- 注意：find 的 plain=true 模式下 % 是字面量，不能带转义。
    if html:find("wr-underline", 1, true) or html:find("wr-thought-link", 1, true)
        or html:find("wr-thought-count", 1, true) then
        logger.info("annotations: html already injected, skip re-injection")
        return html
    end

    -- 去除 BOM，避免下划线位置偏移
    local original_html = html
    html = stripLeadingBOM(html)
    if html ~= original_html then
        logger.info("annotations: stripped BOM")
    end

    -- 解析所有 range
    local ranges = {}
    for _, ul in ipairs(underlines) do
        local range_str = ul.range
        if range_str then
            local start_pos, end_pos = parseRange(range_str)
            if start_pos and end_pos and start_pos < end_pos then
                ranges[#ranges + 1] = {
                    range_str = range_str,
                    start = start_pos,
                    end_pos = end_pos,
                }
            else
                -- range 字段存在却无法解析，等于一条没有落点的划线。
                -- 旧实现直接丢弃，读者和统计都看不到；现在它是一条 bad_range。
                refuse(collect, "", REASON.BAD_RANGE)
            end
        end
    end

    if #ranges == 0 then
        return html
    end

    -- 按起始位置排序
    table.sort(ranges, function(a, b) return a.start < b.start end)

    -- 转换为 rune 数组
    local runes = toRunes(html)
    local n = #runes

    logger.info("annotations: html runes=", n, "underlines=", #ranges)

    -- 预计算所有替换片段。先把 API 的正文 range 映射回原始 XHTML
    -- 的可见文本，再在当前 XHTML 中定位；绝不把 range 直接当 HTML 坐标。
    local index = visibleTextIndex(html)
    local source_spans
    local ok_source, Source = pcall(require, "weread.lib.annotation_source")
    if ok_source and Source and type(Source.index) == "function" then
        local ok_index, value = pcall(Source.index, html)
        if ok_index then source_spans = value end
    end
    local underline_by_range = {}
    for _, candidate in ipairs(underlines) do
        if candidate.range then underline_by_range[candidate.range] = candidate end
    end

    -- 校准 API range 坐标空间：api_pos = (k - 1) + nl_before(k) + C，C 是【相加】的。
    -- 在 27370 条真实 anchor 上实测，C = -1 覆盖 99.613%（旧注释里的 C≈±4 是错的）。
    -- 用服务端提供的选中文本（markText / review abstract）作样本交给
    -- Source.calibrate：票必须彼此一致（离散度 <= 2）才返回 C，否则返回 nil 并
    -- 整章拒画。中位数式的投票会把「C 估错」变成稳定偏几个字符且无人察觉——
    -- 那正是本次要根除的失效模式，所以此处不再描述为中位数。
    local constant
    if source_spans and Source and type(Source.calibrate) == "function" then
        local samples = {}
        for _, candidate in ipairs(underlines) do
            local qtext
            if candidate and type(candidate.markText) == "string" and candidate.markText ~= "" then
                qtext = candidate.markText
            elseif candidate and candidate.range and thought_reviews then
                local review = thought_reviews[candidate.range]
                local page = review and type(review.pageReviews) == "table" and review.pageReviews[1] or nil
                local item = page and (page.review or page) or nil
                qtext = item and (item.abstract or item.contextAbstract or item.markText)
            end
            if candidate and candidate.range and type(qtext) == "string" and qtext ~= "" then
                samples[#samples + 1] = { range = tostring(candidate.range), quote = qtext }
            end
        end
        if #samples >= 1 then
            local ok_constant, value = pcall(Source.calibrate, source_spans, samples)
            if ok_constant and type(value) == "number" then
                constant = value
                logger.info("annotations: calibrated range constant=", constant)
            end
        end
    end
    if not constant then
        -- Without a sample there is no way to derive the API-to-XHTML offset for
        -- a mid-chapter range, so that case must still be refused. But a range
        -- anchored at 0 carries no offset to derive: after parseRange's 1-based
        -- conversion its start is exactly 1, so it can only begin at the first
        -- character, and its end is read straight from the range. Short chapters
        -- an author's note, a "see chapter N" line -- return no markText at all,
        -- so they could never be calibrated and lost every highlight for that
        -- reason alone.
        --
        -- parseRange has already shifted both ends by +1 into Lua's 1-based world,
        -- so a range anchored at 0 shows up as start == 1. Such a range has no
        -- offset to derive and fits the text by construction, and it is the only
        -- case that may proceed without calibration.
        local all_anchored_at_zero = #ranges > 0
        for _, candidate in ipairs(ranges) do
            if candidate.start ~= 1 or candidate.end_pos > n then
                all_anchored_at_zero = false
                break
            end
        end
        if not all_anchored_at_zero then
            -- CHANGED 2026-10-12 (超神机械师-19480992 ch1 measured): refusing
            -- the whole chapter here ALSO refused highlights whose placement
            -- never needed the coordinate space. quoteCandidate puts the
            -- server's own markText/abstract first, and findQuoteRange locates
            -- that text directly; the constant only feeds the derived quote.
            -- Chapter 1 of this book had votes=0 (both honest samples are under
            -- the 4-rune minimum), so constant=nil, and every authoritative
            -- highlight was refused with it -- that is why chapters 1-3 of the
            -- shipped EPUB carry the old 10-11 build's misplaced underlines
            -- while later chapters carry none at all.
            --
            -- Proceed with constant = nil. The coordinate path contributes
            -- nothing (Source.quote returns "" for nil), so each range either
            -- places via its authoritative text or is refused individually with
            -- a reason; nothing is painted that the text does not verify.
            logger.warn("annotations: cannot calibrate range space;",
                "authoritative quotes may still place, coordinate path disabled")
            constant = nil
        else
            logger.info("annotations: no calibration needed, all ranges anchored at 0")
            constant = 0
        end
    end

    local replacements = {}
    local prevEnd = 0

    -- Per-range constant retry.
    --
    -- WHY THIS EXISTS (measured on 超神机械师-19480992 chapter 1, 2026-10-12):
    -- calibrate() summarises all samples into ONE chapter constant by median.
    -- Two genuine server samples in that chapter vote C=3 and C=4; the median
    -- picks one, and every range governed by the other constant lands exactly
    -- one rune off — 亲吻 underlined as 吻金, 海拉 as 拉，. The mid-word
    -- underlines in the shipped EPUB were all this, not random drift.
    --
    -- The fix is not a better global summary: with two honest but disagreeing
    -- votes no single constant can serve both. Instead, when the derived quote
    -- fails verification under the chapter constant, the SAME range is retried
    -- with the neighbouring constants; each retry still has to pass the same
    -- character-exact verification as the primary path, so a wrong constant
    -- cannot paint an unverified location. Retries are counted and recorded.
    local RETRY_CONSTANTS = { 1, -1, 2, -2, 3, -3 }
    local per_range_retries = 0
    local per_range_retry_hits = 0

    for _, ul in ipairs(ranges) do
        local review = thought_reviews and thought_reviews[ul.range_str] or nil
        local range_quote
        if source_spans and Source and type(Source.quote) == "function" then
            local ok_quote, value = pcall(Source.quote, source_spans, ul.range_str, constant)
            if ok_quote then range_quote = value end
        end
        -- 唯一的落点来源。返回值自带稳定原因码：解析不通过就是拒写，
        -- 不存在"再退化到 raw range"的分支。
        local start_pos, end_pos, reason, needle = resolveAnnotationRange({
            index = index,
            runes = runes,
            underline = underline_by_range[ul.range_str],
            review = review,
            range_quote = range_quote,
            minimum_start = prevEnd + 1,
        })
        if (not start_pos or not end_pos)
            and source_spans and Source and type(Source.quote) == "function"
            and constant ~= nil
            and (reason == REASON.TEXT_MISMATCH or reason == REASON.QUOTE_NOT_FOUND) then
            -- The chapter constant may be wrong for THIS range even though it
            -- was right for the samples that voted. Retry the coordinate path
            -- with neighbouring constants; each candidate quote goes through the
            -- same exact-match verifier, so this cannot place a highlight that
            -- the chapter text does not literally contain.
            --
            -- `constant ~= nil` in the guard is load-bearing, not defensive
            -- noise: calibration can legitimately fail (a chapter whose only
            -- sample is below the 4-rune minimum), and constant + delta on a nil
            -- constant raises. That path still places via the authoritative
            -- quote, which needs no constant at all -- so skipping the retry
            -- there costs nothing. Found by
            -- tests/test_annotation_midword_fix.py.
            for _, delta in ipairs(RETRY_CONSTANTS) do
                local candidate = constant + delta
                local ok_retry, value = pcall(Source.quote, source_spans,
                    ul.range_str, candidate)
                if ok_retry and type(value) == "string" and value ~= ""
                    and value ~= range_quote then
                    per_range_retries = per_range_retries + 1
                    local s2, e2, r2, n2 = resolveAnnotationRange({
                        index = index,
                        runes = runes,
                        underline = underline_by_range[ul.range_str],
                        review = review,
                        range_quote = value,
                        minimum_start = prevEnd + 1,
                    })
                    if s2 and e2 then
                        start_pos, end_pos, reason, needle = s2, e2, r2, n2
                        per_range_retry_hits = per_range_retry_hits + 1
                        logger.info("annotations: per-range constant retry",
                            ul.range_str, "constant", constant, "->", candidate)
                        break
                    end
                end
            end
        end
        if not start_pos or not end_pos then
            -- Never paint an unrelated location when the server supplied
            -- annotation text but the local XHTML cannot explain it.
            -- Never use raw range as a final fallback: on affected books it
            -- can point to an unrelated paragraph in the XHTML.
            refuse(collect, ul.range_str, reason, needle)
            goto continue
        end

        -- 边界检查
        if start_pos < 0 or end_pos > n or start_pos >= end_pos then
            refuse(collect, ul.range_str, REASON.OUT_OF_RANGE, needle)
            goto continue
        end

        -- 校正边界：确保 start 和 end 不落在 HTML 标签或实体内部。
        -- 校正会改变坐标，必须留痕 —— 这里落下的每一处偏移都要能和原文对上。
        local snapped_end, end_moved = snapEndToSafeBoundary(runes, start_pos, end_pos)
        local snapped_start, start_moved =
            snapStartToSafeBoundary(runes, start_pos, snapped_end)
        if end_moved then
            logger.info("annotations: corrected boundary", ul.range_str, "end",
                end_pos, "->", snapped_end)
        end
        if start_moved then
            logger.info("annotations: corrected boundary", ul.range_str, "start",
                start_pos, "->", snapped_start)
        end
        end_pos, start_pos = snapped_end, snapped_start

        -- 确保不重叠
        if start_pos >= end_pos or start_pos < prevEnd then
            refuse(collect, ul.range_str, REASON.OVERLAP, needle)
            goto continue
        end

        -- 提取范围内的内容并包裹下划线标签
        local inner = {}
        for j = start_pos, end_pos - 1 do
            inner[#inner + 1] = runes[j]
        end

        -- 使用 wrapTextSegments 处理跨标签边界
        local wrapped = wrapTextSegments(inner, "wr-underline")

        -- 如果有想法数据，每个 wr-underline span 单独包裹 <a>（跨段可点击）
        if thought_reviews and thought_reviews[ul.range_str] then
            local underline_open = '<span class="wr-underline">'
            local underline_close = '</span>'

            -- 内部锚点（非 noteref）：去掉 epub:type="noteref" 避免 crengine 走专用
            -- 脚注弹窗路径（该路径无视 CSS pointer-events）。想法内容不再内嵌 EPUB，
            -- 点击时由 main.lua 从缓存 JSON 现取。锚点已编码 book_id+chapter_uid+range。
            local anchor_id = thoughtAnchorId(book_id, chapter_uid, ul.range_str)
            local href = "#" .. anchor_id
            local open_a = '<a class="wr-thought-link" href="' .. htmlEscape(href) .. '">'
            -- 只第一个 <a> 带 id：拦截失败时 KOReader 跟随锚点最多跳回划线起点（兜底）。
            local open_a_with_id = '<a id="' .. htmlEscape(anchor_id)
                .. '" class="wr-thought-link" href="' .. htmlEscape(href) .. '">'

            -- wrapTextSegments 为每个文本段生成独立的 underline span；
            -- 逐 span 包裹 <a> 可避免 </h1><p>、</p><p> 等块级边界导致 MuPDF 截断链接。
            -- 徽章：在第一个文本段后追加想法计数（如 "③"），仅显示一次。
            local with_links = {}
            local first_link = true
            local badge_added = false
            for _, item in ipairs(wrapped) do
                if item == underline_open then
                    with_links[#with_links + 1] = first_link and open_a_with_id or open_a
                    first_link = false
                    with_links[#with_links + 1] = item
                elseif item == underline_close then
                    with_links[#with_links + 1] = item
                    if not badge_added then
                        local _review_obj = thought_reviews[ul.range_str]
                        local _count = _review_obj and (
                            _review_obj.totalCount or
                            (type(_review_obj.pageReviews) == "table" and #_review_obj.pageReviews) or
                            0
                        ) or 0
                        if _count > 0 then
                            with_links[#with_links + 1] = '<span class="wr-thought-count">[' .. tostring(_count) .. ']</span>'
                        end
                        badge_added = true
                    end
                    with_links[#with_links + 1] = '</a>'
                else
                    with_links[#with_links + 1] = item
                end
            end
            wrapped = with_links
        end

        replacements[#replacements + 1] = {
            start = start_pos,
            end_pos = end_pos,
            content = wrapped,
        }
        -- ok 记录带 marked：它是落点区间上归一化后的实际正文，
        -- 消费方可以自行断言 marked == quote，而不必相信本文件的结论。
        recordRangeAudit(collect, ul.range_str, true, REASON.OK, needle,
            visibleTextOf(runes, start_pos, end_pos))
        prevEnd = end_pos

        ::continue::
    end

    if #replacements == 0 then
        return html
    end

    -- 单遍拼接：依次输出未修改段和替换片段
    local result = {}
    local prev = 1

    for _, rep in ipairs(replacements) do
        -- 输出未修改段
        for j = prev, rep.start - 1 do
            result[#result + 1] = runes[j]
        end
        -- 输出替换片段
        for _, r in ipairs(rep.content) do
            result[#result + 1] = r
        end
        prev = rep.end_pos
    end

    -- 输出剩余部分
    for j = prev, n do
        result[#result + 1] = runes[j]
    end

    return table.concat(result)
end

--- 在 HTML 中注入下划线标记。签名与返回值保持不变（其他调用方依赖）。
function Annotations.injectUnderlines(html, underlines, thought_reviews, chapter_uid, book_id)
    return injectUnderlinesCore(html, underlines, thought_reviews, chapter_uid, book_id, nil)
end

--- Annotations.audit 只做解析与统计，不注入、不改写 HTML。
-- 它与 injectUnderlines 共用同一份落点实现（injectUnderlinesCore），因此审计结论
-- 与实际注入结果必然一致；这里不做第二次落点计算，也就不会出现两套逻辑漂移。
-- @string html  同一份"原始"章节 HTML（已被注入过的 HTML 会命中单次注入
--   不变式并直接返回空数组 —— 在那种 HTML 上算出的坐标没有意义）
-- @table  underlines  划线列表（与 Annotations.injectUnderlines 同形）
-- @table  thought_reviews  想法数据 map（可选），keyed by range string
-- @return table records  每条带 range 的划线一条记录：
--   { range = "<start>-<end>"（无法解析时为 ""）, placed = bool,
--     reason = 稳定原因码, quote = 参与决策的规范化引文, marked = 落点上实际的正文 }
function Annotations.audit(html, underlines, thought_reviews, book_id)
    local records = {}
    injectUnderlinesCore(html, underlines, thought_reviews, nil, book_id, records)
    return records
end

--- 处理章节数据中的划线标注。
-- @string html  原始 HTML 内容
-- @table  chapter_underlines  章节划线数据（来自 API）
-- @table  thought_reviews  想法数据 map（可选），keyed by range string
-- @return processed_html, css  处理后的 HTML 和额外的 CSS
function Annotations.process(html, chapter_underlines, thought_reviews, book_id)
    if type(html) ~= "string" or html == "" then
        return html, ""
    end

    if type(chapter_underlines) ~= "table" then
        return html, ""
    end

    local underlines = chapter_underlines.underlines
    if type(underlines) ~= "table" or #underlines == 0 then
        return html, ""
    end

    -- 构建 thought range 快速查找表（存 review 对象以支持计数徽章）
    local thought_map = nil
    if type(thought_reviews) == "table" then
        thought_map = {}
        for _, rv in ipairs(thought_reviews) do
            if rv.range then
                thought_map[rv.range] = rv
            end
        end
        if not next(thought_map) then
            thought_map = nil
        end
    end

    logger.info("annotations: processing", #underlines, "underlines",
        thought_map and ("thoughts on " .. #underlines) or "")

    local processed = injectUnderlinesCore(html, underlines, thought_map,
        chapter_underlines.chapterUid, book_id, nil)

    -- 想法内容不再注入 EPUB（不生成 <aside> 脚注），改为点击时从缓存 JSON 现取。

    if processed ~= html then
        local css = Annotations.UNDERLINE_CSS
        if thought_map then
            css = css .. "\n" .. Annotations.THOUGHT_LINK_CSS
        end
        return processed, css
    end

    return html, ""
end

return Annotations
