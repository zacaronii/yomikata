-- reader: title ドラゴンクエストI・II (Dragon Quest I & II, Game Boy Color)
-- reader: reads a message window's text from the cartridge, a page for each wait
-- reader: version CGB-AEDJ
-- reader: build 4fa1dc98
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows. As in Dragon Quest III on the same console (games/gbc/CGB-BD3J.lua), whose
-- way of reading this is, with this game's places in memory and some codes of its own.
--
-- The game's text lies in the cartridge as it is and is drawn from there: nothing is copied into work RAM. A
-- byte there says the cartridge's bank (BANK) and a word where in it (AT, 0x4000-0x7FFF, little-endian). When a
-- page has typed, AT stands just past the code that ends it. So a page is the cartridge's bytes from the code
-- that ended the page before up to AT, said once AT has stood a moment just past an end or a wait. A core shows
-- a script only the banks switched in now, so the cartridge is read whole where the host gives it (0x01000000).
--
-- The codes are the game's own, a byte each but for kanji (0xE5 and a byte: the table beside this script):
--   0x24-0x51 hiragana, あ to ん in the order of the fifty sounds; 0x5B-0x88 katakana the same
--   0x92 after a kana voices it (゛), 0x93 half-voices it (゜)
--   0xEA a message's start, 0xF7 its end, 0xFA a wait, 0xEF a new row (0xEE after it is stepped over), 0xB0 a
--   space; 0xF9 n a name kept in work RAM (0x00 the hero's), 0xF6 the hero's name alone; 0xFC n a pause of n frames:
--   a long one (0x80 or more) ends a page that goes on by itself (the narration's two rows, or a talk's page
--   that turns without the button), and 0xF0 after it clears the window; a short one is within the page
--   the signs and kanji by the table (read off this game's screens; Dragon Quest III numbers its kanji otherwise)
--
-- Not covered: who speaks but where the text says it, the yes/no box, menus, fights' messages (not looked at),
-- names other than the hero's; a sign or kanji the table hasn't got comes out as U+FFFD.

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
local BANK, AT = 0xC806, 0xC80F
local CART = 0x01000000                 -- the cartridge, whole, where the host gives it
local STILL = 4                         -- frames AT stands before a page is said
local MOST = 400                        -- bytes of a page looked back through
local END, WAIT, START, ROW, SPACE, KANJI, NAME, VOICE, HALF = 0xF7, 0xFA, 0xEA, 0xEF, 0xB0, 0xE5, 0xF9, 0x92, 0x93
local PAUSE, LONG, PAGE = 0xFC, 0x80, 0xE1
local ENDS = { [END] = true, [WAIT] = true }
local HERO = 0xF6
local NAMES = { [0x00] = { 0xC200, 4 } } -- a name by its number: where it is, how long at most (it ends at 0xF0)
local HIRAGANA = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"

-- The build: the cartridge's header, read from the whole cartridge where the host gives it (from power-on), or
-- else where the console has it (which holds it only once the game runs).
local header = mem.read(CART + 0x0134, 16) or mem.read(0x0134, 16)
if header ~= "DQ1&2\0\0\0\0\0\0AEDJ\x80" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("CGB-AEDJ.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

local function nth(list, n) -- the nth character (from 0) of a UTF-8 string, or nil
    local from = utf8.offset(list, n + 1)
    local to = from and utf8.offset(list, n + 2)
    return to and list:sub(from, to - 1) or nil
end

--- A one-byte code's character, by the table or by the order of the kana; nil for one neither has.
local function character(code)
    local ch = characters[string.char(code)]
    if ch then
        return ch
    elseif code >= 0x24 and code <= 0x51 then
        return nth(HIRAGANA, code - 0x24)
    elseif code >= 0x5B and code <= 0x88 then
        local h = nth(HIRAGANA, code - 0x5B)
        return h and utf8.char(utf8.codepoint(h) + 0x60)
    end
    return nil
end

-- A kana and its mark (゛ or ゜) as one character: the next code point for ゛ (か→が), two on for ゜ (は→ぱ).
local function marked(ch, mark)
    if not ch then
        return nil
    end
    local c = utf8.codepoint(ch)
    local base = c >= 0x30A0 and c - 0x60 or c
    local voiceable = (base >= 0x304B and base <= 0x3062) or (base >= 0x3064 and base <= 0x3069)
        or (base >= 0x306F and base <= 0x307D)
    if mark == VOICE and voiceable then
        return utf8.char(c + 1)
    elseif mark == HALF and base >= 0x306F and base <= 0x307D then
        return utf8.char(c + 2)
    elseif mark == VOICE and c == 0x30A6 then
        return "ヴ"
    end
    return ch
end

local function name(bytes)
    local out = {}
    for i = 1, #bytes do
        local b = bytes:byte(i)
        if b == 0 or b == 0xF0 then
            break
        end
        out[#out + 1] = character(b) or "\u{FFFD}"
    end
    return table.concat(out)
end

--- The text of the codes in `bytes`: commands stepped over, rows kept.
local function decode(bytes)
    local out, i = {}, 1
    while i <= #bytes do
        local b = bytes:byte(i)
        if b == KANJI and i < #bytes then
            out[#out + 1] = characters[bytes:sub(i, i + 1)] or "\u{FFFD}"
            i = i + 1
        elseif b == HERO then
            out[#out + 1] = name(mem.read(NAMES[0x00][1], NAMES[0x00][2]) or "")
        elseif b == 0xFC and i < #bytes then
            i = i + 1 -- a pause and its length
        elseif b == NAME and i < #bytes then
            local where = NAMES[bytes:byte(i + 1)]
            out[#out + 1] = where and name(mem.read(where[1], where[2]) or "") or "\u{FFFD}"
            i = i + 1
        elseif b == ROW then
            if #out > 0 then
                out[#out + 1] = "\n"
            end
        elseif b == SPACE then
            out[#out + 1] = "\u{3000}"
        elseif (b == VOICE or b == HALF) and #out > 0 then
            out[#out] = marked(out[#out], b)
        elseif b < 0xE0 then
            out[#out + 1] = character(b) or "\u{FFFD}"
        end
        i = i + 1
    end
    local shown = table.concat(out):gsub("\n+$", "")
    return shown
end

local before, stood, said, warned = nil, 0, nil, false

reader.on_state_loaded(function()
    reader.forget()
    before, stood, said = nil, 0, nil
end)

reader.on_frame(function()
    local bank, at = mem.u8(BANK), mem.u16(AT)
    if not bank or not at or at < 0x4000 or at > 0x7FFF or bank == 0 then
        return
    end
    local place = bank * 0x4000 + (at - 0x4000)
    stood = place == before and stood + 1 or 0
    before = place
    if stood < STILL or said == place then
        return
    end
    local back = math.min(MOST, place)
    local bytes = mem.read(CART + place - back, back)
    if not bytes or #bytes < back then
        if not warned then
            reader.log("the cartridge can't be read here (it is wanted at 0x01000000): nothing is read")
            warned = true
        end
        return
    end
    -- From AT back over the codes that end this page, then back to the code that ended the page before.
    -- A page ends at an end or a wait, or (one that goes on by itself) just past a long pause.
    local function long_pause(k) -- the pause whose length is at k
        return k >= 2 and bytes:byte(k - 1) == PAUSE and bytes:byte(k) >= LONG
            and (k < 3 or bytes:byte(k - 2) ~= KANJI)
    end
    local last = back
    if ENDS[bytes:byte(last)] then
        while last > 0 and ENDS[bytes:byte(last)] do
            last = last - 1
        end
    elseif long_pause(last) then
        last = last - 2
    else
        return -- AT stands at a row's end or a pause within the page: the page isn't whole
    end
    -- Back to where the page began: past an end, a wait, a long pause, or a message's or a page's start.
    local first = last
    while first > 1 do
        local b = bytes:byte(first - 1)
        if ENDS[b] or b == START or b == PAGE or long_pause(first - 1) then
            break
        end
        first = first - 1
    end
    if first <= 1 then
        return -- no page's start within reach
    end
    local shown = decode(bytes:sub(first, last))
    said = place
    if shown:find("%S") then
        reader.emit{ text = shown, channel = "dialogue", id = text.hash(string.format("%X", place) .. shown) }
    end
end)
