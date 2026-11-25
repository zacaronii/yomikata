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
---- cursor: the chapter's cells }), rest, spaces (whether an answer's colon may have wide spaces before it) and
--- tight (whether a line break before a row's colon is left out: a line starts at its colon).
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
                page = table.concat(pieces, "\n")
                if game.tight then
                    page = page:gsub("^\n" .. COLON, COLON):gsub("\n\n" .. COLON, "\n" .. COLON)
                end
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
