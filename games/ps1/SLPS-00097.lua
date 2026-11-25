-- reader: title 幻想水滸伝 (Gensou Suikoden)
-- reader: reads the message window's four rows; a portrait speaker's name is the first row
-- reader: version SLPS-00097
-- reader: build f01bf2b47a59a4cda2efdbe2c001af518f01f2ae
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- How the game keeps its text.
--
-- WINDOW points at the window last used (0 before the first). In it, the byte at +0x08 is 1 while the window
-- is open, and the word at +0x18 points at its text: four rows of twenty 16-bit cells, each cell a glyph's
-- place in the game's font (the table beside this script), filled in as the line is typed. The text stays
-- after the window closes.
--
-- A line is emitted when it is whole: when the game draws its wait mark (MARK), or when the rows have stood
-- still for half a second, for a line the game moves on from by itself.
--
-- Answers to a question come as the message, one a row; no flag for them was found. Talking to townspeople,
-- shops, battles, the war and duel screens weren't reached. Cursor marks, wait marks and item icons aren't in
-- the table and read as U+FFFD.

local tbl = require("lib.tbl")

local WINDOW = 0x8017DBC0 -- word: the window last used
local OPEN, TEXT = 0x08, 0x18
local ROW_SIZE, ROW_COUNT = 40, 4
local MARK, WAITING = 0x801BA436, 24 -- byte: the wait mark's entry in the draw list
local STILL = 30                     -- frames

-- The game runs one program for its title and name entry and another for the game itself, so no code is the
-- same throughout. Three words of the tables it keeps in memory from power-on are.
if mem.u32(0x8009A000) ~= 0x0B2D0B6F or mem.u32(0x8009C000) ~= 0xF4D3F491 or mem.u32(0x80095000) ~= 0x040107FF then
    error("this script is for another build of the game", 0)
end

local source = reader.data("tbl")
if not source then
    error(reader.game_id .. ".tbl is missing: the game's character table", 0)
end
local glyphs = tbl.parse(source)

local last, since = nil, 0

reader.on_state_loaded(function()
    reader.forget()
    last, since = nil, 0
end)

reader.on_frame(function()
    local window = mem.u32(WINDOW)
    local rows_at = window and window ~= 0 and mem.u8(window + OPEN) == 1 and mem.u32(window + TEXT)
    if not rows_at then
        reader.forget("line")
        last, since = nil, 0
        return
    end
    local raw = mem.read(rows_at, ROW_SIZE * ROW_COUNT)
    if raw ~= last then
        last, since = raw, 0
    else
        since = since + 1
    end
    if mem.u8(MARK) ~= WAITING and since < STILL then
        return
    end
    local rows = {}
    for row = 0, ROW_COUNT - 1 do
        rows[#rows + 1] = tbl.trim_end(tbl.decode(raw:sub(row * ROW_SIZE + 1, (row + 1) * ROW_SIZE), glyphs, 2))
    end
    while #rows > 0 and rows[#rows] == "" do
        rows[#rows] = nil
    end
    if #rows > 0 and reader.changed("line", raw) then
        reader.emit{ text = table.concat(rows, "\n"), id = text.hash(raw) }
    end
end)
