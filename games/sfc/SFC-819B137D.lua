-- reader: title 真・女神転生II (Shin Megami Tensei II)
-- reader: reads a page of the message window as the game types it, a character at a time
-- reader: version SFC-819B137D
-- reader: build 819b137d
-- reader: no hooks
-- reader: no layout where the window is drawn isn't looked into

-- How the game keeps what it shows.
--
-- The script lies packed in the cartridge, and the game draws each character into the window's tiles as it unpacks
-- it: no page stands anywhere. What does stand, for the character just drawn: CODE, its number in the font (the low
-- ten bits; 0x2000 voices a kana, 0x1000 half-voices it, 0 is a blank), AT, where in the window it was drawn (it
-- moves on by the character's width, and by a row's worth at a new row; 0x8000 in CODE colours a name), and ROW,
-- the window's row it is on (1, 3, 5, ...). So this script follows the typing: each new character is added to its
-- row; the page is said once nothing has been added to it for STILL frames (the page waits for the button), and a
-- character drawn at a row or place before the last one begins a new page. A page whose start came before the
-- script began isn't said, nor one said already.
--
-- The numbers are those of Shin Megami Tensei's font (games/sfc/SFC-56A9AAB6.lua), whose rules this script keeps:
-- hiragana in the order of the fifty sounds from 0x25, katakana likewise from 0x5C (0x5D there), the digits from
-- 0x01 and capitals from 0x0B; the rest (signs, kanji) are in the table beside this script, read in play.
--
-- Only the message window is read: its characters are drawn at places from FIRST to LAST, a step of 0x10 apart;
-- menus and status boxes draw elsewhere.
--
-- Not covered: menus, choices' answers, fights' messages, the screens before the story. A character the table
-- hasn't got comes out as U+FFFD.

local CODE, AT, ROW = 0x7E0504, 0x7E0808, 0x7E0818
local STILL = 30   -- frames with nothing added before the page is said
local SPACE = 0x00
local FIRST, LAST = 0x180, 0x900 -- the message window's places; menus and status boxes draw elsewhere
local VOICES, HALF = 0x2000, 0x1000

-- The build: the cartridge's own header, in its first bank (its title's start, and its checksum with its
-- complement). A host that gives a script no cartridge can't run this script.
if mem.read(0x00FFC0, 8) ~= "DIGITAL " or mem.read(0x00FFDC, 4) ~= "\xA7\x9D\x58\x62" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("SFC-819B137D.tbl is missing: the game's table of characters", 0)
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
    elseif code >= 0x5C and code <= 0x89 then -- katakana, in the same order (one before Shin Megami Tensei's)
        local from = utf8.offset(GOJUON, code - 0x5C + 1)
        return utf8.char(utf8.codepoint(GOJUON, from) + 0x60)
    elseif code >= 0x01 and code <= 0x0A then
        return utf8.char(0xFF10 + code - 1)
    elseif code >= 0x0B and code <= 0x24 then
        return utf8.char(0xFF21 + code - 0x0B)
    end
    return UNKNOWN(code)
end

--- A character with its voicing marks, as the number's flags say.
local function marked(value)
    local ch = character(value & 0x3FF)
    if ch == "ウ" and value & VOICES ~= 0 then
        return "ヴ"
    elseif #ch == 3 and value & VOICES ~= 0 then
        return utf8.char(utf8.codepoint(ch) + 1)
    elseif #ch == 3 and value & HALF ~= 0 then
        return utf8.char(utf8.codepoint(ch) + 2)
    end
    return ch
end

local rows, last_at, last_row, last_value, stood, said = {}, nil, nil, nil, 0, true
local begun, last_page = false, nil -- whether this page's start was seen; the page said last
local pending = 0                    -- characters drawn whose move of AT is still to come

reader.on_state_loaded(function()
    reader.forget()
    rows, last_at, last_row, last_value, stood, said = {}, nil, nil, nil, 0, true
    begun, last_page, pending = false, nil, 0
end)

local function say()
    local keys = {}
    for row in pairs(rows) do
        keys[#keys + 1] = row
    end
    table.sort(keys)
    local out = {}
    for _, row in ipairs(keys) do
        local line = table.concat(rows[row]):gsub("\u{3000}+$", "")
        if line ~= "" then
            out[#out + 1] = line
        end
    end
    said = true
    local page = table.concat(out, "\n")
    -- A page begun before the script was (a state loaded in the middle of one) isn't whole: not said. Nor
    -- one said already (a blank drawn after it).
    if begun and page:find("%S") and page ~= last_page then
        last_page = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end

reader.on_frame(function()
    local at, row, value = mem.u16(AT), mem.u16(ROW), mem.u16(CODE)
    if not at or not row or not value then
        return
    end
    if last_at == nil then
        last_at, last_row, last_value = at, row, value -- where things stand when the script begins
        return
    end
    -- A character is drawn when CODE changes. AT usually moves with it, but may follow a frame later, or not at
    -- all for the character after some (it is drawn where AT stands): such a character's move is pending, and
    -- AT moving with CODE the same is that pending move, or else the same character drawn again (いい).
    local new = false
    if value ~= last_value then
        new = true
        pending = at == last_at and pending + 1 or 0
    elseif at ~= last_at then
        if pending > 0 then
            pending = pending - 1
        else
            new = true
        end
    end
    last_value = value
    -- Frames that add nothing to the page (a menu or a status box drawn meanwhile included) count as standing.
    stood = stood + 1
    if not new then
        last_at = at
    end
    if not new or at < FIRST or at >= LAST or at & 0xF ~= 0 or value & 0x4000 ~= 0 or value & 0x3FF == 0xFF then
        -- No character, or a menu's or a status box's (drawn elsewhere, or a status figure): not the message
        -- window's, and not a place the window's next character is held against.
        if stood >= STILL and not said then
            say()
        end
        return
    end
    -- A page's first character is drawn at its first place; one drawn before the last character begins a new
    -- page too.
    if (row == 1 and at == FIRST) or row < last_row or (row == last_row and at < last_at) then
        if not said then
            say()
        end
        rows, begun = {}, true
    end
    rows[row] = rows[row] or {}
    table.insert(rows[row], marked(value & 0x3FFF))
    said = false
    last_at, last_row, stood = at, row, 0
end)
