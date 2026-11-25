-- reader: title ムーンライトシンドローム (Moonlight Syndrome)
-- reader: reads the message window as it fills, a part at a time, and a choice's answers with its cursor
-- reader: version SLPS-01001 (disc 1) and SLPS-01002 (disc 2): the same program
-- reader: build a8b3924464bf4b2977f03ac14ea8a7e0c09739d7
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- How the game keeps its text.
--
-- The game reads the chapter's script in place (lib/inplace.lua): SCRIPT points at it and OFFSET is how far it
-- has read, so the message is what has been read so far of the page it is on, and grows while a line types.
-- A text starts after the code 0x8000 and ends at 0xFFFF; 0xFFFD is a new line, 0xFFFC a new page, 0xFFF9 and
-- 0xFFFA take a frame count after them. Codes are 16 bits, high byte first, and a text can start at an odd
-- address.
--
-- Codes below 0x00F0 are the same throughout (the kana table beside this script); kanji, from 0x0100, are
-- numbered anew in each of the twelve chapters, and each has its table. LABELS, the count of labels in the
-- chapter's script, differs between them all and says which chapter is loaded.
--
-- STATE: 1 typing, 2 waiting, 6 a pause in a line, 3 a timed wait, 0 between operations, 4 a choice up,
-- 5 answered. UP is nonzero while the window is up. A choice is one operation holding each answer after the
-- bytes 00 00 01; while it is up OFFSET stands at its end, and CURSOR is the answer the cursor is on.
--
-- What is new of the page is emitted when the window waits, or has stood still for half a second.
--
-- Not read: the speaker's name beside some lines (a picture). Run in the prologue only; the other chapters
-- are checked from the discs' scripts.

local __loaded, __module = {}, {}
local function require(name)
    local value = __loaded[name]
    if value == nil then
        local make = __module[name] or error("module " .. name .. " isn't in this script", 2)
        value = make()
        if value == nil then value = true end
        __loaded[name] = value
    end
    return value
end
__module["lib.inplace"] = function()
-- A script the game reads in place: it keeps no copy of the text it shows, only how far into its script it
-- has read, with instructions between the texts. What is on screen is the page that position is on, as far
-- as it has been read (docs/format.md says the same of a layout's `script`).
--
-- A text starts after a mark and stops at an end code. A mark right after an end code that has no page break
-- before it carries the page on. A page break inside a text starts the page anew, unless only end codes
-- follow it: the page it ended is still up.

local inplace = {}

-- Bytes i (from 0) up to j, as Python slices them: nothing before the start, short at the end.
local function cut(s, i, j)
    if i < 0 then
        return ""
    end
    return s:sub(i + 1, j)
end

--- What `data` holds from `start` to its end, less the pages that are off the screen: one piece, or with
--- `rows` a piece for each text.
local function pages(data, start, game, mark, rows)
    local unit = game.unit
    local tail = #data
    while tail - unit >= start and game.ends[cut(data, tail - unit, tail)] do
        tail = tail - unit
    end
    local out, piece, i = {}, {}, start
    while i + unit <= #data do
        local code = cut(data, i, i + unit)
        local size = game.sizes[code]
        if not size and mark and game.ends[code] and cut(data, i + unit, i + unit + #mark) == mark then
            i = i + unit + #mark -- where two texts of the page join
            if rows then
                out[#out + 1] = table.concat(piece)
                piece = {}
            end
        elseif not size and game.breaks[code] and i + unit < tail then
            out, piece, i = {}, {}, i + unit -- a new page: what came before is off the screen
        else
            piece[#piece + 1] = cut(data, i, i + (size or unit))
            i = i + (size or unit)
        end
    end
    out[#out + 1] = table.concat(piece)
    return out
end

--- The page the reading position is on. `both` is `size` bytes of script up to the position, then as many
--- bytes after it as `mark` has. `game` has unit, ends, breaks (sets of codes) and sizes (codes with
--- arguments -> their bytes). Gives a list of pieces; an empty list when the position isn't in a text, or a
--- text is about to start a new page.
function inplace.page(both, size, game, mark, rows)
    local unit = game.unit
    local data, ahead = both:sub(1, size), both:sub(size + 1)
    local function code(i)
        return cut(data, i, i + unit)
    end
    local function joins(q) -- a mark at q carries the page on
        return game.ends[code(q - unit)] and not game.breaks[code(q - 2 * unit)]
    end
    local p, start = #data, nil
    if ahead == mark and not joins(p) then
        return {}
    end
    if game.ends[code(p - unit)] then -- the text has just ended
        p = p - unit
    end
    while true do
        while p >= #mark and cut(data, p - #mark, p) ~= mark and not game.ends[code(p - unit)] do
            p = p - unit
        end
        if p < #mark or cut(data, p - #mark, p) ~= mark then
            break -- out of what was read, or a second end code: not a text
        end
        start = p
        local q = p - #mark
        if not joins(q) then
            break
        end
        p = q - unit -- on through the text before
    end
    if not start then
        return {}
    end
    return pages(data, start, game, mark, rows)
end

--- A piece as text: an end code, a page break or a clear is a blank line, `game.newlines` a line break,
--- `game.silent` nothing, a code with arguments nothing, a code the table hasn't got U+FFFD. Line breaks at
--- the end are dropped. `characters` is code -> character.
function inplace.decode(piece, game, characters)
    local out, i = {}, 1
    while i + game.unit - 1 <= #piece do
        local code = piece:sub(i, i + game.unit - 1)
        local size = game.sizes[code]
        if size then
            i = i + size
        else
            if game.ends[code] or game.breaks[code] then
                out[#out + 1] = "\n\n"
            elseif game.newlines[code] then
                out[#out + 1] = "\n"
            elseif not game.silent[code] then
                out[#out + 1] = characters[code] or "\u{FFFD}"
            end
            i = i + game.unit
        end
    end
    return (table.concat(out):gsub("\n+$", ""))
end

return inplace
end
__module["lib.tbl"] = function()
-- A character table in the Thingy form the catalog keeps (docs/format.md): "0100=学" for a code and its
-- character, the code as hex in the order its bytes lie in memory; "/FFFF" for a code that ends a text and
-- "*FFFE" for one that breaks a line; "#" or ";" for a comment.

local tbl = {}

local function bytes(hex)
    return (hex:gsub("%x%x", function(pair)
        return string.char(tonumber(pair, 16))
    end))
end

--- Reads a table's text (reader.data("tbl")). Gives { chars = code -> character, ends = code -> true,
--- newlines = code -> true }, each code a string of its bytes.
function tbl.parse(source)
    local out = { chars = {}, ends = {}, newlines = {} }
    for line in source:gmatch("[^\r\n]+") do
        local first = line:sub(1, 1)
        if first == "/" or first == "*" then
            local code = line:match("^.(%x+)")
            if code and #code % 2 == 0 then
                out[first == "/" and "ends" or "newlines"][bytes(code)] = true
            end
        elseif first ~= "#" and first ~= ";" then
            local code, character = line:match("^(%x+)=(.*)$")
            if code and #code % 2 == 0 and character ~= "" then
                out.chars[bytes(code)] = character
            end
        end
    end
    return out
end

--- Text from `bytes`, `unit` bytes a code, with a parsed table: a code the table ends a text with is a blank
--- line, one it breaks a line with a line break, and one it hasn't got is U+FFFD.
function tbl.decode(bytes, parsed, unit)
    local out = {}
    for i = 1, #bytes - unit + 1, unit do
        local code = bytes:sub(i, i + unit - 1)
        if parsed.ends[code] then
            out[#out + 1] = "\n\n"
        elseif parsed.newlines[code] then
            out[#out + 1] = "\n"
        else
            out[#out + 1] = parsed.chars[code] or "\u{FFFD}"
        end
    end
    return table.concat(out)
end

--- A line without the wide spaces at its end, as a game pads a window's rows.
function tbl.trim_end(line)
    while line:sub(-3) == "\u{3000}" do
        line = line:sub(1, -4)
    end
    return line
end

--- A line without the wide spaces at its start and its end, as a game pads a window's rows.
function tbl.trim_wide(line)
    local space = "\u{3000}"
    local first, last = 1, #line
    while line:sub(first, first + 2) == space do
        first = first + 3
    end
    while last - 2 >= first and line:sub(last - 2, last) == space do
        last = last - 3
    end
    return line:sub(first, last)
end

return tbl
end
local inplace = require("lib.inplace")
local tbl = require("lib.tbl")

local SCRIPT, OFFSET, LABELS = 0x8008BEF0, 0x8008BEF4, 0x8008BEF8
local STATE, UP, CURSOR = 0x8008BF00, 0x8008F0E4, 0x8008E33C
local WAITING, CHOICE = 2, 4
local TEXT_MARK, ANSWER_MARK = "\x80\x00", "\x00\x00\x01"
local MOST, MOST_ANSWERS = 600, 128
local STILL = 30 -- frames

-- Three calls in the game's code.
if mem.u32(0x80017278) ~= 0x0C005CF9 or mem.u32(0x80021C74) ~= 0x0C009385 or mem.u32(0x8002FD3C) ~= 0x0C00BE00 then
    error("this script is for another build of the game", 0)
end

local game = {
    unit = 2,
    ends = { ["\xFF\xFF"] = true },
    breaks = { ["\xFF\xFC"] = true },
    newlines = { ["\xFF\xFD"] = true },
    silent = { ["\xFF\xF9"] = true, ["\xFF\xFA"] = true },
    sizes = { ["\xFF\xF9"] = 4, ["\xFF\xFA"] = 4 },
}

-- Each chapter's table, by its script's count of labels.
local CHAPTERS = {
    [36] = function() return reader.data("prologue") end,
    [94] = function() return reader.data("henlin") end,
    [88] = function() return reader.data("henshits") end,
    [123] = function() return reader.data("mowdei") end,
    [128] = function() return reader.data("sowguw") end,
    [210] = function() return reader.data("denpow") end,
    [167] = function() return reader.data("dowaku") end,
    [74] = function() return reader.data("epilogue") end,
    [265] = function() return reader.data("fuyou") end,
    [104] = function() return reader.data("hamon") end,
    [53] = function() return reader.data("kaibyo") end,
    [132] = function() return reader.data("shitsow") end,
}

local kana = reader.data("kana")
if not kana then
    error(reader.game_id .. ".kana is missing: the game's kana table", 0)
end
kana = tbl.parse(kana).chars

local loaded, characters = nil, nil

--- The characters of the chapter whose script has `labels` labels, or nil when it is none of the twelve.
local function chapter(labels)
    if labels ~= loaded then
        loaded, characters = labels, nil
        local source = CHAPTERS[labels] and CHAPTERS[labels]()
        if source then
            characters = setmetatable(tbl.parse(source).chars, { __index = kana })
        end
    end
    return characters
end

--- The pieces of the page the game is reading, texts opened by `mark`, looking back `most` bytes.
local function reading(mark, most, rows)
    local script, at = mem.u32(SCRIPT), mem.u32(OFFSET)
    if not script or not at or at <= 0 then
        return {}
    end
    local size = math.min(most, at)
    local both = mem.read(script + at - size, size + #mark)
    if #both < size then
        return {}
    end
    return inplace.page(both, size, game, mark, rows)
end

local function text_of(pieces, glyphs)
    local out = {}
    for i, piece in ipairs(pieces) do
        out[i] = inplace.decode(piece, game, glyphs)
    end
    return table.concat(out, "\n")
end

local last, since, said = nil, 0, ""

local function nothing()
    reader.forget()
    reader.cursor(nil)
    last, since, said = nil, 0, ""
end

reader.on_state_loaded(nothing)

reader.on_frame(function()
    local glyphs = chapter(mem.u32(LABELS))
    if not glyphs then
        nothing()
        return
    end
    local state = mem.s32(STATE)

    if state == CHOICE then
        local answers = text_of(reading(ANSWER_MARK, MOST_ANSWERS, true), glyphs)
        if answers ~= "" and reader.changed("answers", answers) then
            reader.emit{ text = answers, channel = "choice" }
        end
        reader.cursor(mem.u8(CURSOR) or -1)
    else
        reader.forget("answers")
        reader.cursor(nil)
    end

    local shown = (mem.u8(UP) or 0) ~= 0 and text_of(reading(TEXT_MARK, MOST, false), glyphs) or ""
    if shown ~= last then
        last, since = shown, 0
    else
        since = since + 1
    end
    if shown == "" then
        said = ""
        return
    end
    if state ~= WAITING and since < STILL then
        return
    end
    if shown == said then
        return
    end
    -- What the page has gained since it was last said; the whole of it when it isn't the same page going on.
    local new = shown
    if said ~= "" and shown:sub(1, #said) == said then
        new = shown:sub(#said + 1):gsub("^\n+", "")
    end
    said = shown
    if new ~= "" then
        reader.emit{ text = new, id = text.hash(new) }
    end
end)
