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
