-- reader: title 幻想水滸伝II (Gensou Suikoden II)
-- reader: reads the dialogue window's four rows and the speaker's name; a question's answers with their cursor
-- reader: version SLPM-86168
-- reader: build 9a8d96efa3ccbfe51510274f33bca6e5a2bc4997
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- How the game keeps its text.
--
-- WINDOW points at the dialogue window, or is 0 without one (and holds something that is no address on the
-- title screen); the window's own address moves. In the window: the word at +0x64 points at its text, four
-- rows of twenty-three 16-bit cells, each a glyph's place in the game's font (the table beside this script),
-- filled in as the line is typed; the word at +0x80 points at the speaker's name, cells up to a zero one.
-- The byte at +0x21 is 0 while typing, 8 in a timed pause, 1 waiting for a press inside a speech, 2 at its
-- last page.
--
-- A question's answers are typed into the same rows: the byte at +0x0C is nonzero then, and the byte at +0x9F
-- is the row the cursor is on. They are emitted as a choice, not as a line.
--
-- A line is emitted when it is whole: when the window waits for a press, or when the rows have stood still
-- for half a second.
--
-- Notices in their own window aren't read. Towns, shops, battles, the war and duel screens weren't reached.
-- Window frame pieces, cursor marks and item icons aren't in the table and read as U+FFFD.

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

local WINDOW = 0x8002590C -- word: the dialogue window
local TEXT, NAME, STATE, CHOOSING, CURSOR = 0x64, 0x80, 0x21, 0x0C, 0x9F
local WAITING = 3         -- the bits of STATE that say the window waits for a press
local ROW_SIZE, ROW_COUNT = 46, 4
local RAM, RAM_END = 0x80000000, 0x80200000
local STILL = 30          -- frames

-- Three calls in the game's code.
if mem.u32(0x8007F764) ~= 0x0C023835 or mem.u32(0x8008FE00) ~= 0x0C024047 or mem.u32(0x800BBE5C) ~= 0x0C01D278 then
    error("this script is for another build of the game", 0)
end

local source = reader.data("tbl")
if not source then
    error(reader.game_id .. ".tbl is missing: the game's character table", 0)
end
local glyphs = tbl.parse(source)

local function pointer(address)
    local value = mem.u32(address)
    if value and value >= RAM and value < RAM_END then
        return value
    end
end

local last, since = nil, 0

local function nothing()
    reader.forget()
    reader.cursor(nil)
    last, since = nil, 0
end

reader.on_state_loaded(nothing)

reader.on_frame(function()
    local window = pointer(WINDOW)
    local rows_at = window and pointer(window + TEXT)
    if not rows_at then
        nothing()
        return
    end
    local raw = mem.read(rows_at, ROW_SIZE * ROW_COUNT)
    if raw ~= last then
        last, since = raw, 0
    else
        since = since + 1
    end
    local choosing = (mem.u8(window + CHOOSING) or 0) ~= 0
    if not choosing then
        reader.cursor(nil)
    end
    if (mem.u8(window + STATE) or 0) & WAITING == 0 and not choosing and since < STILL then
        return
    end
    local rows = {}
    for row = 0, ROW_COUNT - 1 do
        rows[#rows + 1] = tbl.trim_end(tbl.decode(raw:sub(row * ROW_SIZE + 1, (row + 1) * ROW_SIZE), glyphs, 2))
    end
    while #rows > 0 and rows[#rows] == "" do
        rows[#rows] = nil
    end
    if #rows == 0 then
        return
    end
    if choosing then
        if reader.changed("answers", raw) then
            reader.emit{ text = table.concat(rows, "\n"), channel = "choice", id = text.hash(raw) }
        end
        reader.cursor(mem.u8(window + CURSOR) or -1)
        return
    end
    local name_at = pointer(window + NAME)
    local name = name_at and tbl.trim_end(tbl.decode(mem.wstr(name_at, 44), glyphs, 2)) or ""
    if reader.changed("line", name .. "\n" .. raw) then
        reader.emit{ text = table.concat(rows, "\n"), speaker = name, id = text.hash(raw) }
    end
end)
