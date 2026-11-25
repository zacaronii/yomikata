-- reader: title トワイライトシンドローム 探索編 (Twilight Syndrome: Tansaku-hen)
-- reader: reads the message window as it fills, a part at a time, and a choice's answers with its cursor
-- reader: version SLPS-00102
-- reader: build 23eb26992df8396bac6e65ac0a61897cbac1c715
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- lib/twilight.lua says how the game keeps its text; here are this game's six chapters. Each is a program of
-- its own (CAP0.EXE and so on), told apart by a word of its code at WORD, with its own cells and its own
-- table of kanji: codes up to 0x0108 are the same throughout (the table beside this script), kanji from
-- 0x010C are numbered anew by each chapter. The game stands up to four words past a text's end code while it
-- draws a choice's letters.
--
-- Checked in the first chapter only. The other chapters' cells are from the discs' programs.

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
__module["lib.twilight"] = function()
-- The message window of Twilight Syndrome (Tansaku-hen and Kyuumei-hen share the way of it, with other codes
-- and other addresses).
--
-- Each chapter is a program of its own, with its cells at other addresses and its kanji numbered anew: a word
-- of the loaded program's code says which chapter it is. The script is read in place, one record at a time
-- (lib/inplace.lua), from a buffer at a fixed address; a text starts after the code 0x8900. The message is the
-- page so far. A choice's answers are its last rows, each after a colon, and the game may stand a few words past
-- the end code while it draws their letters and waits.

local inplace = require("lib.inplace")
local tbl = require("lib.tbl")

local twilight = {}

local TEXT, MOST = 0x801FE800, 2048
local MARK = "\x00\x89"
local WAITING, CHOOSING = 0xFFFE, 0xFFFF
local STILL = 30 -- frames
local COLON = "：" -- U+FF1A

--- Reads the codes file: what each control is ("0281 newline") and the bytes of those with arguments
--- ("00C8 4"). Gives what lib/inplace.lua wants, with the unit and `rest`.
function twilight.codes(source, rest)
    local game = { unit = 2, rest = rest, ends = {}, breaks = {}, newlines = {}, silent = {}, sizes = {} }
    for hex, what in source:gmatch("%f[^\n%z](%x%x%x%x) (%w+)") do
        local code = string.char(tonumber(hex:sub(1, 2), 16), tonumber(hex:sub(3, 4), 16))
        if tonumber(what) then
            game.sizes[code] = tonumber(what)
        elseif what == "end" then
            game.ends[code] = true
        elseif what == "page" or what == "clear" then
            game.breaks[code] = true
        elseif what == "newline" then
            game.newlines[code] = true
        else
            game.silent[code] = true
        end
    end
    return game
end

local function lines_of(s)
    local out = {}
    for line in (s .. "\n"):gmatch("(.-)\n") do
        out[#out + 1] = line
    end
    return out
end

--- A row without the colon an answer starts with (and the wide spaces before it, where `spaces`); nil when
--- it doesn't start with one.
local function answer(row, spaces)
    local at = 1
    while spaces and row:sub(at, at + 2) == "\u{3000}" do
        at = at + 3
    end
    if row:sub(at, at + #COLON - 1) == COLON then
        return row:sub(at + #COLON)
    end
end

--- Reads the window each frame. `game` has: word (the address of the word that tells the chapters apart),
--- chapters (that word's value -> { table = a function giving the chapter's table, offset, shown, state,
--- cursor: the chapter's cells }), rest, and spaces (whether an answer's colon may have wide spaces before it).
function twilight.read(game)
    local codes = reader.data("codes")
    local shared = reader.data("tbl")
    if not codes or not shared then
        error(reader.game_id .. ".codes or .tbl is missing: the game's codes and its character table", 0)
    end
    local rules = twilight.codes(codes, game.rest)
    local parsed = tbl.parse(shared)
    for code in pairs(parsed.ends) do
        rules.ends[code] = true
    end
    for code in pairs(parsed.newlines) do
        rules.newlines[code] = true
    end

    local loaded, characters = nil, nil
    local last, since, said = nil, 0, ""

    local function nothing()
        reader.forget()
        reader.cursor(nil)
        last, since, said = nil, 0, ""
    end

    reader.on_state_loaded(nothing)

    reader.on_frame(function()
        local word = mem.u32(game.word)
        local chapter = word and game.chapters[word]
        if not chapter then
            nothing()
            return
        end
        if word ~= loaded then
            loaded = word
            local source = chapter.table()
            characters = source and setmetatable(tbl.parse(source).chars, { __index = parsed.chars })
        end
        if not characters then
            nothing()
            return
        end

        -- The page so far.
        local page = ""
        local at = (mem.u16(chapter.offset) or 0) * rules.unit
        local size = math.min(MOST, at)
        if size > 0 then
            local both = mem.read(TEXT + at - size, size + #MARK)
            if #both >= size then
                local pieces = inplace.page(both, size, rules, MARK, false)
                for i, piece in ipairs(pieces) do
                    pieces[i] = inplace.decode(piece, rules, characters)
                end
                -- A row that is only a colon's own line break goes: a line starts at its colon.
                page = table.concat(pieces, "\n"):gsub("^\n" .. COLON, COLON):gsub("\n\n" .. COLON, "\n" .. COLON)
            end
        end
        local state = mem.u16(chapter.state)

        -- A choice: its answers are the rows from the first that starts with a colon.
        local rows = lines_of(page)
        local answers = {}
        if state == CHOOSING then
            local first = 1
            while first <= #rows and not answer(rows[first], game.spaces) do
                first = first + 1
            end
            for _ = first, #rows do
                local row = table.remove(rows, first)
                if row ~= "" then
                    answers[#answers + 1] = answer(row, game.spaces) or row
                end
            end
        end

        -- The rows above them, or the page: what it has gained since it was last said (the whole of it when
        -- it isn't the same page), once the window waits or has stood still.
        local shown = (mem.u16(chapter.shown) or 0) ~= 0 and table.concat(rows, "\n"):gsub("\n+$", "") or ""
        if shown ~= last then
            last, since = shown, 0
        else
            since = since + 1
        end
        if shown == "" then
            said = ""
        elseif shown ~= said and (state == WAITING or state == CHOOSING or since >= STILL) then
            local new = shown
            if said ~= "" and shown:sub(1, #said) == said then
                new = shown:sub(#said + 1):gsub("^\n+", "")
            end
            said = shown
            if new ~= "" then
                reader.emit{ text = new, id = text.hash(new) }
            end
        end

        if #answers > 0 then
            if reader.changed("answers", table.concat(answers, "\n")) then
                reader.emit{ text = table.concat(answers, "\n"), channel = "choice" }
            end
            reader.cursor(mem.u16(chapter.cursor) or -1)
        else
            reader.forget("answers")
            reader.cursor(nil)
        end
    end)
end

return twilight
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
--- arguments -> their bytes), and may have rest. Gives a list of pieces; an empty list when the position
--- isn't in a text, or a text is about to start a new page.
function inplace.page(both, size, game, mark, rows)
    local unit = game.unit
    -- A game may stop up to `game.rest` codes past a text's end code while the text stays up: the position
    -- is then read as just past that code.
    for k = 0, game.rest or -1 do
        if size >= (k + 1) * unit and game.ends[cut(both, size - (k + 1) * unit, size - k * unit)] then
            both, size = both:sub(1, size - k * unit + #mark), size - k * unit
            break
        end
    end
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

--- Reads a table's text (the file a script asks reader.data for). Gives { chars = code -> character,
--- ends = code -> true, newlines = code -> true }, each code a string of its bytes.
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
local twilight = require("lib.twilight")

-- Three words of the small program that stays in memory under every chapter.
if mem.u32(0x80011A9C) ~= 0x0C004333 or mem.u32(0x80011A0C) ~= 0x34020004 or mem.u32(0x80011934) ~= 0x34020005 then
    error("this script is for another build of the game", 0)
end

twilight.read{
    word = 0x8004AA1C,
    rest = 4,
    spaces = false,
    chapters = {
        [0xAFBF0020] = { table = function() return reader.data("ch0") end,
                         offset = 0x800A4CEA, shown = 0x800A4CEE, state = 0x800A4CE4, cursor = 0x80094D28 },
        [0xAFB20028] = { table = function() return reader.data("ch1") end,
                         offset = 0x800A39EE, shown = 0x800A39F2, state = 0x800A39E8, cursor = 0x800926EC },
        [0x30C21000] = { table = function() return reader.data("ch2") end,
                         offset = 0x8009F8E6, shown = 0x8009F8EA, state = 0x8009F8E0, cursor = 0x8008E3FC },
        [0xA4820032] = { table = function() return reader.data("ch3") end,
                         offset = 0x800A47FE, shown = 0x800A4802, state = 0x800A47F8, cursor = 0x80093878 },
        [0xAC2239F4] = { table = function() return reader.data("ch4") end,
                         offset = 0x800A4796, shown = 0x800A479A, state = 0x800A4790, cursor = 0x80092758 },
        [0xA062000F] = { table = function() return reader.data("chx") end,
                         offset = 0x8009444E, shown = 0x80094452, state = 0x80094448, cursor = 0x80084DCC },
    },
}
