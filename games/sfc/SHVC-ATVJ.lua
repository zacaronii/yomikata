-- reader: title テイルズ オブ ファンタジア (Tales of Phantasia, Super Famicom)
-- reader: reads the message window's rows off video memory once the page stands, each character by its picture
-- reader: version SHVC-ATVJ
-- reader: build 8fc7e4e3
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The script lies packed in the cartridge, and no page of it stands anywhere in memory as codes: the game draws
-- each character's picture into tiles of video memory (TILES on, 2 bits a pixel) and lays those tiles out in a
-- tile map it keeps in work RAM (MAP on, a 16-bit word a cell, 32 cells a row, the tile's number in its low 10
-- bits). The window holds ROWS rows of text, the speaker's name on the first where there is one; each row is a
-- map row of top tiles over one of bottom tiles, COLUMNS tiles from the map's third column, the tiles numbered
-- down each column. A row's picture is 16 pixels high; the font is fixed, CELLS characters a row, each CELL pixels
-- wide. Its ink is the pictures' first plane (values 1 and 3; 2 is the outline drawn around each stroke). Narration
-- shown without a window is drawn the same way.
--
-- So each cell is cut out of the row's picture (lib/cells.lua) and known by its picture, in the dictionary beside
-- this script (a hash to a character): read in play, each page's cells beside its picture, since the font in the
-- cartridge is packed too. A picture it hasn't got comes out as U+FFFD and is logged for `yomikata unread`.
--
-- SHOWN is 5 while text is shown (a window, or narration), 0 while none is: the map and the tiles keep the last
-- page after it closes. A page is said once its rows have stood the same for STILL frames while text is shown,
-- rows with nothing in them left out; each page once, until another comes. A page that types out in two parts
-- (the game waits for the button halfway) is said at each part.
--
-- It reads video memory (the region `vram`): where the host doesn't give it, it reads nothing.
--
-- Not covered: menus, fights' messages, the screens before the story. A place's name is centred on the grid by
-- blank cells, which are kept; the double rule drawn under it is left out.

local MAP, MAP_ROW = 0x7E59C0, 0x40
local ROWS, COLUMNS, FIRST_COLUMN = 3, 28, 2
local TILES, TILE, COUNT, BLANK = 0xE000, 16, 0x200, 0x2540   -- the tiles to the end of video memory
local CELL, CELLS, HEIGHT = 14, 16, 16
local SHOWN, ON = 0x7E20A0, 5
local INK = 1   -- the first plane: values 1 and 3
-- Pictures drawn on the grid that are no character: the double rule under a place's name.
local DECORATION = { ["3DAAF875D91178F5"] = true }
local STILL = 20

-- The build: the cartridge's title, in its first bank.
if mem.read(0xC0FFC0, 18) ~= "TALES OF PHANTASIA" then
    error("this script is for another build of the game", 0)
end

local cells = require("lib.cells")
local glyphs = require("lib.glyphs")
local source = reader.data("glyphs")
if not source then
    error("SHVC-ATVJ.glyphs is missing: the pictures of the game's font", 0)
end
local dictionary = glyphs.open(source)
local vram = mem.region("vram")

--- The tiles' bytes of one row of text: its top tiles, then its bottom tiles, as cells.picture takes them.
local function row_bytes(map, tiles, row)
    local out = {}
    for half = 0, 1 do
        local at = (2 * row + half) * MAP_ROW + 2 * FIRST_COLUMN
        for column = 0, COLUMNS - 1 do
            local word = map:byte(at + 2 * column + 1) | (map:byte(at + 2 * column + 2) << 8)
            if word == BLANK then
                out[#out + 1] = string.rep("\0", TILE)
            else
                local from = (word & 0x3FF) * TILE
                local tile = tiles:sub(from + 1, from + TILE)
                out[#out + 1] = #tile == TILE and tile or string.rep("\0", TILE)
            end
        end
    end
    return table.concat(out)
end

local function page_text(map, tiles)
    local lines = {}
    for row = 0, ROWS - 1 do
        local pic = cells.picture(row_bytes(map, tiles, row), "snes2", COLUMNS)
        local out, marks = {}, {}
        for _, c in ipairs(cells.fixed(pic, 0, 0, CELL, HEIGHT, CELLS, INK)) do
            if c.blank or DECORATION[text.hash(c.picture):upper()] then
                out[#out + 1], marks[#marks + 1] = "\u{3000}", false
            else
                local character, hash = dictionary:read(c.picture, CELL, HEIGHT, 1)
                out[#out + 1] = character or "\u{FFFD}"
                marks[#marks + 1] = character or hash
            end
        end
        while #out > 0 and out[#out] == "\u{3000}" do       -- the blank cells after the row's last character
            out[#out], marks[#marks] = nil, nil
        end
        local line = table.concat(out)
        if line ~= "" then
            dictionary:line(marks)     -- the row, logged once where a picture in it has no reading
            lines[#lines + 1] = line
        end
    end
    return table.concat(lines, "\n")
end

local said, last, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
end)

reader.on_frame(function()
    if not vram then
        return
    end
    if mem.u8(SHOWN) ~= ON then
        last, stood = nil, 0
        return
    end
    local map = mem.read(MAP, 2 * ROWS * MAP_ROW)
    local tiles = vram.read(TILES, COUNT * TILE)
    if not map or #map < 2 * ROWS * MAP_ROW or not tiles then
        return
    end
    local now = map .. tiles
    stood = now == last and stood + 1 or 0
    last = now
    if stood ~= STILL then
        return
    end
    local page = page_text(map, tiles)
    if page ~= "" and page ~= said then
        said = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end)
