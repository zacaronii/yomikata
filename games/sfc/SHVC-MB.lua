-- reader: title MOTHER2 ギーグの逆襲 (Mother 2: Gyiyg no Gyakushuu)
-- reader: reads the message window's rows as they are typed and scroll
-- reader: version SHVC-MB
-- reader: build b343f13a
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- Text is kept as the tiles on screen, not as character codes. The game has eight window slots, each with a
-- buffer of 16-bit cells: a tile's number (0x40-0x1FF) and its colour bits (0x0400 the cursor's colour, 0x1800
-- a highlighted line). The table beside this script says which character each is, in all three colours.
-- SLOT_OF + 2 x a window's number is the slot that window is open in, 0xFFFF when it isn't.
--
-- Only window 1 is read, the message window: three rows of 17 cells, each row of characters followed by a row
-- of their lower halves, which isn't read. It types and scrolls, and no mark for "waiting" is known: what it
-- has gained is said once its rows have stood still.
--
-- Not read: menus, names, shops, the battle window.
--
-- The build: a cartridge's program isn't in the memory a script sees. What is checked is a table of six
-- addresses the game writes into work RAM as it starts, there from the first second on.

local tiles = require("lib.tiles")
local tbl = require("lib.tbl")

local SLOT_OF, MESSAGE_WINDOW = 0x7E8C26, 1 -- u16 each
local BUFFERS, BUFFER_SIZE, SLOTS = 0x7E61F6, 0x3F0, 8
local ROWS, CELLS, ROW_STEP = 3, 17, 68 -- bytes from a row of characters to the next
local STILL = 20 -- frames

if mem.read(0x7E514E, 12) ~= "\x7F\x9C\xDD\x9C\x3B\x9D\x99\x9D\xF7\x9D\x55\x9E" then
    error("this script is for another build of the game", 0)
end

local source = reader.data("tbl")
if not source then
    error("SHVC-MB.tbl is missing: the game's table of tiles", 0)
end
local parsed = tbl.parse(source)

local last, since, said = "", 0, {}

reader.on_state_loaded(function()
    reader.forget()
    last, since, said = "", 0, {}
end)

reader.on_frame(function()
    local slot = mem.u16(SLOT_OF + 2 * MESSAGE_WINDOW)
    local lines = {}
    if slot and slot < SLOTS then
        local buffer = BUFFERS + slot * BUFFER_SIZE
        lines = tiles.rows(function(row)
            return mem.read(buffer + row * ROW_STEP, 2 * CELLS)
        end, ROWS, parsed, 2)
    end
    local shown = table.concat(lines, "\n")
    if shown ~= last then
        last, since = shown, 0
        return
    end
    since = since + 1
    if shown == "" then
        said = {}
    elseif since == STILL then
        local new = table.concat(tiles.gained(said, lines), "\n")
        said = lines
        if new:find("%S") then
            reader.emit{ text = new, id = text.hash(new) }
        end
    end
end)
