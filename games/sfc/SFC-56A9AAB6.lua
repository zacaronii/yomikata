-- reader: title 真・女神転生 (Shin Megami Tensei)
-- reader: reads text a row at a time as it is put on screen
-- reader: version SFC-56A9AAB6
-- reader: build 56a9aab6
-- reader: no hooks
-- reader: no layout where the window is drawn isn't looked into

-- How the game keeps what it shows.
--
-- Text is put on screen a row at a time, through a list of writes to video memory that the game keeps at
-- LIST: for each write, where on the screen's tile map it goes (a 16-bit word address), how many cells, and
-- the cells, 16 bits each with a character's number in the low ten bits. A row of text is two writes: the
-- row itself, then a write of the same length one tile row above it (0x20 words less), which holds the marks
-- of voiced kana over the cells they belong to. The list keeps the last row written until the next takes
-- its place, so each row is said once, when it has been written.
--
-- The numbers are the font's own. Hiragana stand in the order of the fifty sounds from 0x25 on (あ to ん),
-- katakana likewise from 0x5D, the digits from 0x01 and capitals from 0x0B; the rest are in the table beside this script, read in play. A voiced kana is the plain one with its mark
-- (0x9E, or 0x9F for the half-voiced) in the cell over it. 0xCF is a space, 0xAB the mark that waits for a
-- button, and 0x97 the mark a speech begins with.
--
-- Not covered: who speaks; a window's rows as one text (each row is a text of its own, a question's answers
-- too); menus, fights, the map's names.

local LIST = 0x7E0102 -- the list of writes: where (u16), cells (u16), the cells; then the next
local MOST = 32 -- cells of a row at most
local SPACE, WAIT, PROMPT = 0xCF, 0xAB, 0x97
local VOICED, HALF_VOICED = 0x9E, 0x9F
local DRAWN = 0x98 -- from here on: cursors, gauges and frames, which aren't text

-- The build: this game writes nothing into work RAM that is its own from the first second on, so the
-- cartridge's own header is checked, in its first bank (its title's start, and its checksum with its
-- complement). A host that gives a script no cartridge can't run this script.
if mem.read(0x00FFC0, 8) ~= "DIGITAL " or mem.read(0x00FFDC, 4) ~= "\x8B\x52\x74\xAD" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("SFC-56A9AAB6.tbl is missing: the game's table of characters", 0)
end
local characters = {}
for code, character in pairs(tbl.parse(source).chars) do
    characters[string.unpack(">I2", code)] = character
end

local GOJUON = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"

local function UNKNOWN(code)
    return "\u{FFFD}"
end

--- The character of one cell's number.
local function character(code)
    if characters[code] then
        return characters[code]
    elseif code == SPACE then
        return "\u{3000}"
    elseif code >= 0x25 and code <= 0x52 then
        local from = utf8.offset(GOJUON, code - 0x25 + 1)
        return GOJUON:sub(from, utf8.offset(GOJUON, code - 0x25 + 2) - 1)
    elseif code >= 0x5D and code <= 0x8A then -- katakana, in the same order
        local from = utf8.offset(GOJUON, code - 0x5D + 1)
        return utf8.char(utf8.codepoint(GOJUON, from) + 0x60)
    elseif code >= 0x01 and code <= 0x0A then
        return utf8.char(0xFF10 + code - 1)
    elseif code >= 0x0B and code <= 0x24 then
        return utf8.char(0xFF21 + code - 0x0B)
    elseif code >= DRAWN then
        return ""
    end
    return UNKNOWN(code)
end

local waiting -- the write of the tick before
local said = {} -- the row last said at each place on screen

reader.on_state_loaded(function()
    reader.forget()
    waiting, said = nil, {}
end)

reader.on_frame(function()
    local where, cells = mem.u16(LIST), mem.u16(LIST + 2)
    if not where or not cells or cells == 0 or cells > MOST then
        return
    end
    local bytes = mem.read(LIST + 4, 2 * cells)
    local over = mem.read(LIST + 4 + 2 * cells, 4 + 2 * cells)
    if #bytes < 2 * cells or #over < 4 + 2 * cells then
        return
    end
    -- The write after it is the marks' row when it goes one tile row above, to as many cells.
    local marks = nil
    if string.unpack("<I2", over, 1) == where - 0x20 and string.unpack("<I2", over, 3) == cells then
        marks = over:sub(5)
    end
    local key = where .. ":" .. bytes .. (marks or "")
    if key ~= waiting then -- taken on the second tick it is there, so that a row half put down isn't said
        waiting = key
        return
    end
    local out = {}
    for i = 1, #bytes, 2 do
        local code = string.unpack("<I2", bytes, i) & 0x3FF
        if code ~= PROMPT and code ~= WAIT and code ~= 0 then
            local ch = character(code)
            local mark = marks and string.unpack("<I2", marks, i) & 0x3FF
            if (mark == VOICED or mark == HALF_VOICED) and #ch == 3 then
                ch = utf8.char(utf8.codepoint(ch) + (mark == VOICED and 1 or 2))
            end
            out[#out + 1] = ch
        end
    end
    local row = table.concat(out)
    while row:sub(-3) == "\u{3000}" do
        row = row:sub(1, -4)
    end
    while row:sub(1, 3) == "\u{3000}" do
        row = row:sub(4)
    end
    -- A row is said when it is put where another stood, or where none did: the mark that waits for a button
    -- blinks in its cell, which writes the row again and again.
    if row ~= "" and said[where] ~= row then
        said[where] = row
        reader.emit{ text = row, channel = "dialogue", id = text.hash(where .. row) }
    end
end)
