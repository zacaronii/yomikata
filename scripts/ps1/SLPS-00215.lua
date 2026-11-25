-- reader: title ポリスノーツ (Policenauts)
-- reader: reads the window under the picture a page at a time, and the lines of spoken passages, with their furigana
-- reader: version SLPS-00215 and SLPS-00216 (the two discs share one program)
-- reader: build 87b1ceca7e7c410855e2a63f847a8c2e801d8db2
-- reader: no hooks
-- reader: no layout where the text is drawn isn't looked into

-- How the game keeps what it shows.
--
-- A scene is loaded as one block: its program, then its messages one after another, each ended by a zero
-- byte. When a message starts to show, the game copies it to a place of its own (WORK), each code turned
-- round (0x10100 less the code), with a zero byte after it. That copy is what is read here.
--
-- A character is two bytes, the high one first. In a message's own code the low 13 bits say which character;
-- the three above them differ for one character from place to place (how it is set, it seems):
--   kana    0x4420 less the JIS code (hiragana 0x1Fxx, katakana 0x1Exx)
--   ASCII   0x100 less the character: letters, digits and signs, drawn full width
--   signs, kanji    the table beside this script. The kanji are numbered as the game's font has them, and the
--           font is in main memory, so the table was read off its glyphs.
-- The game's own marks are written with those letters: "#" and a letter is a command, "#ー" and "#-" a long
-- dash, "#{" or "#R{" … "}#" a word with a reading over it, the two parted by "、".
--
-- A message may have several pages, parted by #W (wait for a press). The routine that shows a message keeps,
-- on its stack, a word that says how many bytes into the message the page after the one on screen starts,
-- and 0 on the last. The stack isn't at one place: it is found by two words 0x24 bytes apart that both point
-- at the end of the copy, and the page word is 0xDC bytes after the first. A message is said only while
-- they are there: the copy stays after the window has gone, and holds other things between messages.
--
-- A spoken passage shows its lines under the picture without the window, each for a time of its own. Those
-- are a list the game makes of the passage (the same codes turned round, with a plain space where the message
-- has "、" and "|" for a new row), and LINE points at the one on screen, and holds no address between them.
--
-- Not covered: who speaks (the name in the box left of the window isn't kept as text); the commands of the
-- picture (見る and the rest); the title's messages about the memory card; the opening film's lines. Checked in the
-- first room on disc 1.

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

local WORK = 0x80086164 -- the game's copy of the message being shown
local MOST = 1024 -- bytes of one message
local STACK, STACK_SIZE = 0x80080800, 0x800 -- where the routine's stack has been seen
local TWIN, NEXT = 0x24, 0xDC -- from the first word that points at the copy's end: its twin, and the page word
local LINE = 0x8006CD74 -- word: the line shown under the picture without the window; not an address when none is
local LINES, LINES_END = 0x80100000, 0x80200000 -- where such lines have been seen
local MOST_LINE = 256

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
local BAR, SPACE = 0x100 - 0x7C, 0x10FF -- | starts a new row in a line under the picture; the wide space

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

--- Codes (their low 13 bits) as text and furigana.
local function decode(codes)
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
        elseif code == BAR then
            out:add("\n")
            i = i + 1
        else
            out:add(character(code))
            i = i + 1
        end
    end
    return out
end

--- A message's pages: the codes of each, and how many bytes into the message the next one starts.
local function pages_of(codes)
    local pages, page = {}, {}
    local i = 1
    while i <= #codes do
        if codes[i] == HASH and codes[i + 1] == LETTER_W then
            page.next = 2 * (i + 1)
            pages[#pages + 1], page = page, {}
            i = i + 2
        else
            page[#page + 1] = codes[i]
            i = i + 1
        end
    end
    if #page > 0 then
        pages[#pages + 1] = page
    end
    return pages
end

--- Where on the stack the two words that point at `finish` are, or nil.
local function frame_for(finish)
    local stack = mem.read(STACK, STACK_SIZE)
    for at = 1, #stack - TWIN - 3, 4 do
        if string.unpack("<I4", stack, at) == finish and string.unpack("<I4", stack, at + TWIN) == finish then
            return STACK + at - 1
        end
    end
end

--- The line LINE points at: codes as in the copy, and single bytes below 0x80 for ASCII as it is.
local function line_at(address)
    local bytes = mem.read(address, MOST_LINE)
    local codes, i = {}, 1
    while i <= #bytes do
        local first = bytes:byte(i)
        if first == 0 then
            return codes
        elseif first < 0x80 then
            codes[#codes + 1] = first == 0x20 and SPACE or 0x100 - first
            i = i + 1
        elseif i < #bytes then
            codes[#codes + 1] = (0x100 - string.unpack(">I2", bytes, i)) & 0x1FFF
            i = i + 2
        else
            break
        end
    end
end

local message -- the message in the copy: { key, finish, pages, said, frame }

reader.on_state_loaded(function()
    reader.forget()
    message = nil
end)

reader.on_frame(function()
    -- A line under the picture, without the window.
    local line = mem.u32(LINE)
    if line and line >= LINES and line < LINES_END then
        if reader.changed("line", line) then
            local codes = line_at(line)
            local built = codes and #codes > 0 and decode(codes)
            if built and built:text() ~= "" then
                reader.emit{ text = built:text(), ruby = built:ruby(), channel = "dialogue",
                             id = text.hash("line" .. built:text()), meta = { kind = "line" } }
            end
        end
    else
        reader.forget("line")
    end

    local bytes = mem.read(WORK, MOST)
    local codes = {}
    for i = 1, #bytes - 1, 2 do
        local work = string.unpack(">I2", bytes, i)
        if work < 0x100 then -- the zero byte that ends it; what follows is left from longer messages
            break
        end
        codes[#codes + 1] = (0x100 - work) & 0x1FFF
    end
    if #codes == 0 then
        return
    end
    local key = bytes:sub(1, 2 * #codes)
    if not message or message.key ~= key then
        message = { key = key, finish = WORK + 2 * #codes, pages = pages_of(codes), said = 0 }
    end
    if message.said >= #message.pages then
        return
    end
    -- The routine that shows it must be at it: the copy is also there after, and holds other things between.
    local frame = message.frame
    if not frame or mem.u32(frame) ~= message.finish or mem.u32(frame + TWIN) ~= message.finish then
        frame = frame_for(message.finish)
        message.frame = frame
    end
    if not frame then
        return
    end
    -- Which page is up: the one before the place the page word gives; the last when it gives none of them.
    local page = #message.pages
    local next = mem.u32(frame + NEXT)
    for i, one in ipairs(message.pages) do
        if one.next and one.next == next then
            page = i
            break
        end
    end
    while message.said < page do -- pages only go forward
        message.said = message.said + 1
        local built = decode(message.pages[message.said])
        local line = built:text()
        if line ~= "" then
            reader.emit{ text = line, ruby = built:ruby(), channel = "dialogue",
                         id = text.hash(key .. message.said),
                         meta = { page = message.said, pages = #message.pages } }
        end
    end
end)
