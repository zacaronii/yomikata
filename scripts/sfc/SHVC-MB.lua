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
__module["lib.tiles"] = function()
-- Text a game keeps as the tiles of a window: a buffer of cells, each a tile's number (with its colour bits),
-- row after row, read through a table of cell to character (lib/tbl.lua).

local tbl = require("lib.tbl")

local tiles = {}

--- The rows of a window as lines of text. `read` gives the bytes of one row (its number from 0); a row is
--- `cells` cells of `unit` bytes. Wide spaces at a row's end are left out, and empty rows at the window's end.
--- A cell the table hasn't got is U+FFFD.
function tiles.rows(read, count, parsed, unit)
    local lines = {}
    for row = 0, count - 1 do
        local bytes = read(row)
        lines[#lines + 1] = bytes and tbl.trim_end(tbl.decode(bytes, parsed, unit)) or ""
    end
    while #lines > 0 and lines[#lines] == "" do
        lines[#lines] = nil
    end
    return lines
end

--- What a window that types and scrolls has gained: `now` and `before` are its lines at two moments. The
--- lines of `now` after those it shares with the end of `before`, the last shared line carried on from where
--- it stood; all of `now` when they share nothing.
function tiles.gained(before, now)
    for skip = 0, #before - 1 do
        local kept = #before - skip
        local fits = kept <= #now
        for i = 1, fits and kept or 0 do
            local old, new = before[skip + i], now[i]
            if i < kept and old ~= new or i == kept and new:sub(1, #old) ~= old then
                fits = false
                break
            end
        end
        if fits then
            local out = {}
            local rest = now[kept]:sub(#before[#before] + 1)
            if rest ~= "" then
                out[1] = rest
            end
            for i = kept + 1, #now do
                out[#out + 1] = now[i]
            end
            return out
        end
    end
    return now
end

return tiles
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
