-- reader: title マザー (Mother, Famicom)
-- reader: reads the message window's rows off the screen's tiles once they have typed out and stand
-- reader: version FC-20CC079D
-- reader: build 20cc079d
-- reader: no hooks
-- reader: no layout the window is found on the screen's tiles each frame, not given as a layout

-- How the game keeps what it shows.
--
-- The game draws its text as tiles into the nametables (the screen's tiles, two tables of 32 by 30 side by side,
-- read at NAMETABLES), a byte a cell in the game's own codes (the table beside this script). A kana's voicing
-- mark is a tile of its own on the row above it: 0xFE is ゛ and 0xFF ゜. On a text row 0xFF is 。.
--
-- The message window is a frame of tiles: its top row a corner (0x80), 20 of 0x81 and a corner (0x82), its sides
-- 0xBE and 0xBF, its bottom row beginning 0x83 under the top's corner. Inside it the text is on every other row from
-- the 2nd below the top until the bottom (four rows in the field, three in a fight), each with its marks on the
-- row above, in the columns inside the frame from the second (the first is a margin). The window is drawn where
-- the screen is scrolled to, so it is looked for: a top row of that width, from any cell of the two tables (a
-- row going on from the left table into the right one, and round), with its bottom below it; one being drawn,
-- its bottom not there yet, isn't read.

-- The text types a few characters each few frames. The window's rows are said once they have stood the same for
-- STILL frames, rows of nothing but spaces left out, and rows with nothing the table has (a fight draws a word as
-- a picture in tiles of its own, 0x67-0x6F, into its window); each page once, until another comes. A fight's window
-- scrolls up a row at a time, so each of its pages holds the rows before it and one more; one that has only
-- lost its top row, nothing after, isn't said again.
--
-- Not covered: who speaks (the game doesn't show it), the opening's lines (not in a window), menus and the other
-- windows (commands, money, the party's status, a fight's commands), the naming screen.

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
local NAMETABLES = 0x80002000      -- the two nametables, 0x400 bytes each (32 x 30 tiles, then attributes)
local COLUMNS, ROWS = 64, 30       -- the two tables side by side
local WIDTH = 20                   -- the window's columns inside its frame
local FIRST = 2                    -- the text's first column inside it (the first is a margin)
local TOP, ACROSS, RIGHT = 0x80, 0x81, 0x82
local BOTTOM, DEEPEST = 0x83, 12   -- the frame's bottom-left corner, at most so many rows below the top
local DAKUTEN, HANDAKUTEN = 0xFE, 0xFF
local STILL = 20                   -- frames the rows stand before they are said

-- The build: the last 32 bytes of the program, in its last bank (the one fixed at 0xE000-0xFFFF). fceumm's map
-- has no program ROM, so it is read where the host gives the cartridge whole (0x01000000, after the iNES header).
local CART, PROGRAM = 0x01000000, 0x40000
local tail = mem.read(CART + PROGRAM - 32, 32)
if not tail or tail == "" then
    error("this script is for another build of the game, or the cartridge can't be read here (wanted at 0x01000000)", 0)
end
if tail ~= "MOTHER-1.00     'H\xBD\xA2\x03\x00\x01\x0F\x01\x8D\x92\xF8@\xFF{\xFE" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("FC-20CC079D.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

local function pairs_of(from, to)
    local out, a, b = {}, {}, {}
    for ch in from:gmatch(utf8.charpattern) do a[#a + 1] = ch end
    for ch in to:gmatch(utf8.charpattern) do b[#b + 1] = ch end
    for k = 1, #a do out[a[k]] = b[k] end
    return out
end
local VOICED = pairs_of("かきくけこさしすせそたちつてとはひふへほカキクケコサシスセソタチツテトハヒフヘホウ",
                        "がぎぐげござじずぜぞだぢづでどばびぶべぼガギグゲゴザジズゼゾダヂヅデドバビブベボヴ")
local SEMI = pairs_of("はひふへほハヒフヘホ", "ぱぴぷぺぽパピプペポ")

--- The two tables' tiles, as one string of rows of 64.
local function screen()
    local left, right = mem.read(NAMETABLES, 0x3C0), mem.read(NAMETABLES + 0x400, 0x3C0)
    if not left or #left < 0x3C0 or not right or #right < 0x3C0 then
        return nil
    end
    local rows = {}
    for row = 0, ROWS - 1 do
        rows[#rows + 1] = left:sub(row * 32 + 1, row * 32 + 32) .. right:sub(row * 32 + 1, row * 32 + 32)
    end
    return table.concat(rows)
end

local function cell(tiles, row, col)
    row, col = row % ROWS, col % COLUMNS
    return tiles:byte(row * COLUMNS + col + 1)
end

--- The window: its top row, its left column and its bottom row. The first cell with the corner, 20 across and
--- the other corner after it, and the bottom corner below.
local function find_window(tiles)
    for row = 0, ROWS - 1 do
        for col = 0, COLUMNS - 1 do
            if cell(tiles, row, col) == TOP and cell(tiles, row, col + WIDTH + 1) == RIGHT then
                local whole = true
                for k = 1, WIDTH do
                    if cell(tiles, row, col + k) ~= ACROSS then
                        whole = false
                        break
                    end
                end
                if whole then
                    for down = 3, DEEPEST do
                        if cell(tiles, row + down, col) == BOTTOM then
                            return row, col, row + down
                        end
                    end
                end
            end
        end
    end
    return nil
end

local function row_text(tiles, row, col)
    local cells, known = {}, false
    for k = FIRST, WIDTH do
        local code = cell(tiles, row, col + k)
        local mark = cell(tiles, row - 1, col + k)
        local ch = characters[string.char(code)]
        if ch and mark == DAKUTEN then
            ch = VOICED[ch] or ch
        elseif ch and mark == HANDAKUTEN then
            ch = SEMI[ch] or ch
        end
        cells[#cells + 1] = ch or "\u{FFFD}"
        known = known or (ch ~= nil and ch ~= "　")
    end
    if not known then
        return "" -- nothing the table has: blank, or a word drawn as a picture in tiles (0x67-0x6F in a fight)
    end
    while #cells > 0 and cells[#cells] == "　" do
        cells[#cells] = nil
    end
    return table.concat(cells)
end

local function window_text(tiles)
    local top, left, bottom = find_window(tiles)
    if not top then
        return nil
    end
    local rows = {}
    for row = top + 2, bottom - 1, 2 do
        local line = row_text(tiles, row, left)
        if line ~= "" then
            rows[#rows + 1] = line
        end
    end
    return table.concat(rows, "\n")
end

local said, last, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
end)

reader.on_frame(function()
    local tiles = screen()
    local page = tiles and window_text(tiles) or nil
    stood = page == last and stood + 1 or 0
    last = page
    if not page or page == "" or stood < STILL or page == said then
        return
    end
    local before = said
    said = page
    -- A fight's window scrolled up with no row after: its rows were all said with the page before.
    if before and #page < #before and before:sub(-#page) == page and before:sub(-#page - 1, -#page - 1) == "\n" then
        return
    end
    reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
end)
