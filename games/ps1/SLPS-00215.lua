-- reader: title ポリスノーツ (Policenauts)
-- reader: reads each message of the window under the picture as the game starts to show it, with its furigana
-- reader: version SLPS-00215 and SLPS-00216 (the two discs share one program)
-- reader: build 87b1ceca7e7c410855e2a63f847a8c2e801d8db2
-- reader: no hooks
-- reader: no layout where the text is drawn isn't looked into

-- How the game keeps what it shows.
--
-- A scene is loaded as one block: its program, then its messages one after another, each ended by a zero
-- byte. CURSOR points at the zero that ends the message on screen: the game sets it when the message starts
-- to show, before it types. So a message is read back from there to the zero before it.
--
-- A character is two bytes, the high one first, and no code has a zero byte. Its low 13 bits say which
-- character; the three above them differ for one character from place to place (how it is set, it seems):
--   kana    0x4420 less the JIS code (hiragana 0x1Fxx, katakana 0x1Exx)
--   ASCII   0x100 less the character: letters, digits and signs, drawn full width
--   signs, kanji    the table beside this script. The kanji are numbered as the game's font has them, and the
--           font is in main memory, so the table was read off its glyphs.
-- The game's own marks are written with those letters: "#" and a letter is a command (#W waits for a press
-- and starts a new page), "#ー" and "#-" a long dash, "#{" or "#R{" … "}#" a word with a reading over it, the two
-- parted by "、".
--
-- A message of several pages is said whole when its first page shows, a line for each page: what says which
-- page is up wasn't found.
--
-- Not covered: who speaks (the name in the box left of the window isn't in the message, and where it is kept
-- wasn't found); the commands of the picture (見る and the rest) and their answers; the films' lines under
-- the picture at the start and the title's messages about the memory card, which aren't messages of this
-- kind. Checked in the first room on disc 1: sixteen pictures, nine messages.

local tbl = require("lib.tbl")

local CURSOR = 0x80080B8C -- word: the zero byte that ends the message on screen
local RAM, RAM_END = 0x80010000, 0x80200000
local MOST = 1024 -- bytes of one message

if mem.u32(0x80012840) ~= 0x0C00A0B5 or mem.u32(0x800452C8) ~= 0x0C00885D then
    error("this script is for another build of the game", 0)
end

local source = reader.data("tbl")
if not source then
    error("SLPS-00215.tbl is missing: the game's table of characters", 0)
end
-- By a code's low 13 bits: the three above them are how the character is set, not which it is.
local characters = {}
for code, character in pairs(tbl.parse(source).chars) do
    characters[string.unpack(">I2", code) & 0x1FFF] = character
end

local HASH, OPEN, CLOSE, MINUS, LETTER_W = 0x100 - 0x23, 0x100 - 0x7B, 0x100 - 0x7D, 0x100 - 0x2D, 0x100 - 0x57
local COMMA, DASH = 0x10FE, 0x10FA -- 、 ー

--- The character of one code's low 13 bits.
local function character(code)
    local known = characters[code]
    if known then
        return known
    end
    if code >= 0x1E00 then
        local jis = 0x4420 - code
        if jis >= 0x2421 and jis <= 0x2576 then
            local out = text.eucjp(string.char((jis >> 8) | 0x80, (jis & 0xFF) | 0x80))
            if out ~= "" then
                return out
            end
        end
    elseif code >= 0x82 and code <= 0xDF then
        return utf8.char(0xFEE0 + 0x100 - code) -- the full-width form of an ASCII character
    end
    return "\u{FFFD}"
end

--- A message's codes as text and furigana.
local function decode(bytes)
    local codes = {}
    for i = 1, #bytes - 1, 2 do
        codes[#codes + 1] = string.unpack(">I2", bytes, i) & 0x1FFF
    end
    local out = reader.builder()
    local i = 1
    while i <= #codes do
        local code = codes[i]
        if code == HASH then
            local at = i + 1
            if codes[at] ~= OPEN and codes[at + 1] == OPEN then
                at = at + 1 -- a letter before the bracket: the kind of group
            end
            if codes[at] == OPEN then
                local base, reading, into = {}, {}, nil
                at = at + 1
                while at <= #codes and codes[at] ~= CLOSE do
                    if codes[at] == COMMA and not into then
                        into = reading
                    else
                        local part = into or base
                        part[#part + 1] = character(codes[at])
                    end
                    at = at + 1
                end
                if into then
                    out:add_ruby(table.concat(base), table.concat(reading))
                else
                    out:add(table.concat(base))
                end
                i = at + (codes[at + 1] == HASH and 2 or 1)
            elseif codes[at] == DASH or codes[at] == MINUS then
                out:add("――")
                i = at + 1
            else
                if codes[at] == LETTER_W and at < #codes then
                    out:add("\n") -- the game waits for a press here and starts a new page
                end
                i = at + 1 -- a command: a letter or a digit
            end
        else
            out:add(character(code))
            i = i + 1
        end
    end
    return out
end

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    local cursor = mem.u32(CURSOR)
    if not cursor or cursor < RAM + MOST or cursor >= RAM_END or mem.u8(cursor) ~= 0 then
        return
    end
    if not reader.changed("message", cursor) then
        return
    end
    local before = mem.read(cursor - MOST, MOST)
    if #before < MOST then
        return
    end
    local start = MOST
    while start > 0 and before:byte(start) ~= 0 do
        start = start - 1
    end
    local bytes = before:sub(start + 1)
    if #bytes < 2 or #bytes % 2 ~= 0 then
        return
    end
    local built = decode(bytes)
    local line = built:text()
    if line ~= "" then
        reader.emit{ text = line, ruby = built:ruby(), channel = "dialogue", id = text.hash(bytes) }
    end
end)
