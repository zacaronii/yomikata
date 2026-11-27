-- reader: title MOTHER3
-- reader: reads the message window's text as it is typed, a stretch for each time it stops
-- reader: version AGB-A3UJ
-- reader: build b44abf0c
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- A talk's whole text is put into one buffer in the board's memory before it is shown: 16-bit words, a
-- character's number in the game's own font or a command (0xFF00 and up), ended by 0xFFFF. A word beside it
-- (AT) points at the next character to be typed. The window has two rows and scrolls; when it is full, or
-- at a command that waits, the typing stops until a button.
--
-- So what the window gains is the words from where AT last stood to where it stands now, and it is said
-- each time AT has stood still for a moment, while the window is up (a byte says so: AT stays where it was
-- after the window closes). Commands that matter:
--   0xFF01      a new row
--   0xFF06 n    a question: the row that follows has its n answers
--   0xFFFF      the text's end
-- the others (0xFF00, 0xFF03 and more: waits, the window's clearing) say nothing.
--
-- Characters: the game's font (in the cartridge from 0xCE39FC, 11 rows of 16 bits a character) is in JIS X 0208's
-- order, a number for each cell: rows 1-8, the NEC row 13 at 752, the kanji's rows 16-84 from 846. Hiragana and
-- katakana fall in Unicode's order (hiragana from 0x011A, katakana from 0x0178) and are taken by rule; the signs,
-- letters and kanji are in the table beside this script, made from that order and read through in sentences in
-- the cartridge's own texts.
--
-- Not covered: who speaks (the window's tab takes a name from a list by a number not found yet); menus, the
-- fights' windows, signs and chapter titles drawn over the picture; a number the table hasn't got (an empty
-- JIS cell) comes out as U+FFFD.

local AT = 0x020196B8 -- word: the next character to be typed
local TEXT, SIZE = 0x02019ABC, 0x1000 -- the buffer a talk's text is put into
local WINDOW = 0x02005C00 -- byte: 1 while the message window is up
local ROW, CHOICE, END = 0xFF01, 0xFF06, 0xFFFF
-- Words below the characters that draw nothing: met before and after a letter in another colour, and at a
-- pause before a row.
local MARKS = { [0x0000] = true, [0x0003] = true, [0x0010] = true }
local STILL = 12 -- frames AT stands before what was typed is said

-- The build: the cartridge's header (its title and code), where a host gives the cartridge to read.
local header = mem.read(0x080000A0, 16)
if not header or #header < 16 or header ~= "MOTHER3\0\0\0\0\0A3UJ" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("AGB-A3UJ.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

--- What stands for a number the table hasn't got.
local function unknown(code)
    return "\u{FFFD}"
end

--- Text from the words in `bytes`: kana by their order, the rest by the table; a new row for 0xFF01, nothing
--- for the other commands. Stops at the text's end.
local function decode(bytes)
    local out, skip = {}, false
    for i = 1, #bytes - 1, 2 do
        local word = bytes:byte(i) + 256 * bytes:byte(i + 1)
        if skip then
            skip = false -- a command's own number
        elseif word == END then
            break
        elseif word == ROW then
            out[#out + 1] = "\n"
        elseif word == CHOICE then
            skip = true
        elseif word >= 0xFF00 or MARKS[word] then -- another command, or a mark that draws nothing
        elseif word >= 0x011A and word <= 0x016F then
            out[#out + 1] = utf8.char(word + 0x2F27)
        elseif word >= 0x0178 and word <= 0x01CF then
            out[#out + 1] = utf8.char(word + 0x2F29)
        else
            out[#out + 1] = characters[bytes:sub(i, i + 1)] or unknown(bytes:sub(i, i + 1))
        end
    end
    return table.concat(out)
end

local said, before, stood, fresh = TEXT, nil, 0, true

reader.on_state_loaded(function()
    reader.forget()
    said, before, stood, fresh = TEXT, nil, 0, true
end)

--- Where the window's two rows begin, for a text found already on its way (the script begins, or a state is
--- loaded, in the middle of a talk): after the last row break but one before `at`.
local function rows_from(at)
    local bytes = mem.read(TEXT, at - TEXT) or ""
    local breaks = {}
    for i = 1, #bytes - 1, 2 do
        if bytes:byte(i) + 256 * bytes:byte(i + 1) == ROW then
            breaks[#breaks + 1] = TEXT + i + 1
        end
    end
    -- AT stands after a break when the window is full: that one is the end of what is shown, not a row's start.
    if breaks[#breaks] == at then
        breaks[#breaks] = nil
    end
    return breaks[#breaks - 1] or TEXT
end

reader.on_frame(function()
    local at = mem.u32(AT)
    if not at or at < TEXT or at >= TEXT + SIZE or mem.u8(WINDOW) ~= 1 then
        said, before, fresh = TEXT, nil, false
        return
    end
    if fresh then
        said, fresh = rows_from(at), false
    end
    if at < said then
        said = TEXT -- another text has been put into the buffer
    end
    stood = at == before and stood + 1 or 0
    before = at
    if stood ~= STILL or at <= said then
        return
    end
    local bytes = mem.read(said, at - said)
    local from = said
    said = at
    local shown = bytes and decode(bytes):gsub("^\n+", ""):gsub("\n+$", "") or ""
    if shown:find("%S") then
        reader.emit{ text = shown, channel = "dialogue",
                     id = text.hash(string.format("%08X ", from) .. shown) }
    end
end)
