-- reader: title ポリスノーツ (Policenauts)
-- reader: reads the window under the picture a page at a time as the game starts to show it, with its furigana
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
-- A message may have several pages, parted by #W. NEXT says how many bytes into the message the page after
-- the one on screen starts, and 0 on the last: so each page is said when it comes up. NEXT is a word the
-- game also uses for other things between messages; it is only looked at against a message's own pages.
--
-- Not covered: who speaks (the name in the box left of the window isn't in the message, and where it is kept
-- wasn't found); the commands of the picture (見る and the rest) and their answers; the films' lines under
-- the picture at the start and the title's messages about the memory card, which aren't messages of this
-- kind. Checked in the first room on disc 1: sixteen pictures, ten pages of nine messages.

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
local tbl = require("lib.tbl")

local CURSOR = 0x80080B8C -- word: the zero byte that ends the message on screen
local NEXT = 0x80080C6C   -- word: how far into the message the next page starts; 0 on the last page
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
                i = at + 1 -- a command: a letter or a digit
            end
        else
            out:add(character(code))
            i = i + 1
        end
    end
    return out
end

--- A message's pages: the bytes of each, and how far into the message the next one starts.
local function pages_of(bytes)
    local pages, first = {}, 1
    for i = 1, #bytes - 3, 2 do
        if string.unpack(">I2", bytes, i) & 0x1FFF == HASH and string.unpack(">I2", bytes, i + 2) & 0x1FFF == LETTER_W then
            pages[#pages + 1] = { bytes = bytes:sub(first, i - 1), next = i + 3 }
            first = i + 4
        end
    end
    if first <= #bytes then
        pages[#pages + 1] = { bytes = bytes:sub(first) }
    end
    return pages
end

local message -- the message the cursor is at: { cursor, pages, said }

reader.on_state_loaded(function()
    reader.forget()
    message = nil
end)

reader.on_frame(function()
    local cursor = mem.u32(CURSOR)
    if not cursor or cursor < RAM + MOST or cursor >= RAM_END or mem.u8(cursor) ~= 0 then
        return
    end
    if not message or message.cursor ~= cursor then
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
        message = { cursor = cursor, pages = pages_of(bytes), said = 0 }
    end
    -- Which page is up: the one before the place NEXT gives; the last when it gives none of them.
    local next = mem.u32(NEXT)
    local page = #message.pages
    for i, one in ipairs(message.pages) do
        if one.next and one.next == next then
            page = i
            break
        end
    end
    if page > message.said then -- pages only go forward
        message.said = page
        local built = decode(message.pages[page].bytes)
        local line = built:text()
        if line ~= "" then
            reader.emit{ text = line, ruby = built:ruby(), channel = "dialogue",
                         id = text.hash(message.pages[page].bytes),
                         meta = { page = page, pages = #message.pages } }
        end
    end
end)
