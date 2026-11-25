-- reader: title メモリーズオフ (Memories Off)
-- reader: reads the message window's three rows and the speaker's name
-- reader: version SLPS-02296
-- reader: build e1db7cdd3b84deab40fe330d4d98451f52faf589
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- How the game keeps its text.
--
-- The window has three rows of Shift-JIS, 57 bytes apart (ROWS). They are written over in place and not ended:
-- LENGTHS holds, a byte a row, how many characters of each count. A page is whole there as soon as it starts
-- to type. A row that wraps ends in the game's padding of wide spaces, dropped here. SHOWN is nonzero while
-- the window is up.
--
-- The speaker's name is a string of its own (NAME) that keeps the last speaker through narration; NAMED is
-- nonzero while the name plate is up.
--
-- Choices weren't reached.

local ROWS, ROW_SIZE, ROW_COUNT = 0x800F6018, 57, 3
local LENGTHS = 0x800F7220 -- a byte for each row: its characters
local SHOWN = 0x80074BA2   -- byte: the window is up
local NAME, NAMED = 0x800F3750, 0x8007253D
local WIDE_SPACE = "\u{3000}"

-- Three calls in the game's code.
if mem.u32(0x800244F4) ~= 0x0C006A04 or mem.u32(0x8003B314) ~= 0x0C00FEB1 or mem.u32(0x8004AC04) ~= 0x0C012CB3 then
    error("this script is for another build of the game", 0)
end

--- The first `count` characters of a row's bytes; one cut off by the row's end is left out.
local function cut(row, count)
    local i = 1
    while count > 0 and i <= #row do
        local byte = row:byte(i)
        local size = ((byte >= 0x81 and byte <= 0x9F) or (byte >= 0xE0 and byte <= 0xFC)) and 2 or 1
        if i + size - 1 > #row then
            break
        end
        i, count = i + size, count - 1
    end
    return row:sub(1, i - 1)
end

local function without_padding(line)
    while line:sub(-3) == WIDE_SPACE do
        line = line:sub(1, -4)
    end
    return line
end

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    if (mem.u8(SHOWN) or 0) == 0 then
        reader.forget("page")
        return
    end
    local rows, raw = {}, {}
    for row = 0, ROW_COUNT - 1 do
        local bytes = cut(mem.read(ROWS + row * ROW_SIZE, ROW_SIZE), mem.u8(LENGTHS + row) or 0)
        raw[#raw + 1] = bytes
        rows[#rows + 1] = text.sjis(bytes)
    end
    while #rows > 0 and rows[#rows] == "" do
        rows[#rows] = nil
    end
    for i, row in ipairs(rows) do
        rows[i] = without_padding(row)
    end
    local page = table.concat(rows, "\n")
    local name = (mem.u8(NAMED) or 0) ~= 0 and text.sjis(mem.cstr(NAME, 16)) or ""
    if page ~= "" and reader.changed("page", name .. "\n" .. table.concat(raw, "\n")) then
        reader.emit{ text = page, speaker = name, id = text.hash(table.concat(raw, "\n")) }
    end
end)
