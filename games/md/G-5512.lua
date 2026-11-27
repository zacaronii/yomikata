-- reader: title シャイニング・フォース 神々の遺産 (Shining Force: Kamigami no Isan, Mega Drive)
-- reader: reads the text window's rows off video memory once the page stands, each glyph by its picture
-- reader: version G-5512
-- reader: build G-5512-00
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- No page stands anywhere in work RAM as codes: the game draws each character straight into tiles of the VDP's
-- video memory, TEXT_FIRST on, 4 bits a pixel (the leftmost pixel in a byte's high half); the window's colour is
-- BACK (0 where nothing is drawn), and anything else is ink. A row of text is ROW_HEIGHT pixels. The tiles are
-- shown one of two ways:
-- - In the talk window, on the plane whose map is at PLANE (a 16-bit word a cell, ACROSS cells a row: the tile's
--   number in its low 11 bits, then its flips): the cells whose tiles are text tiles make the window's inside.
-- - In the opening's narration, as sprites (the sprite table at SAT: 8 bytes a sprite, its y, its size and link,
--   its first tile with palette and priority, its x): sprites TILES_HIGH tiles high of palette PALETTE, each a block
--   of tiles numbered down each column, with the window's border drawn by narrower ones around them.
--
-- The font is proportional, and two glyphs may touch. So each row is read off the window's picture (lib/cells.lua):
-- from each glyph's first column with ink, the widest picture the dictionary beside this script has (a hash to a
-- character, read in play: each page's glyphs beside its picture). A glyph it hasn't got comes out as U+FFFD and is
-- logged for `yomikata unread`. A gap of SPACE blank columns or more is a space.
--
-- A page is said once the window's cells (or the text sprites) and their tiles have stood the same for STILL
-- frames, with some text in them, and no row cut by the window's scroll (one with ink in fewer than GLYPH_ROWS
-- pixel rows); each page once, until another comes. It reads video memory (the region `vram`,
-- from a core that gives it: genesis_plus_gx built with tools/cores/genesis_plus_gx-video-ram.patch): where the
-- host doesn't give it, it reads nothing.
--
-- Not covered: menus, fights' messages and numbers, the screens before the story but the opening's narration.
-- The font draws カ and 力 alike, so both read as カ; a row of nothing but short signs (・, ー) is taken for one
-- cut by the scroll.

local TEXT_FIRST, TEXT_TILES = 1664, 2048 - 1664   -- to the end of video memory
local PLANE, ACROSS, DOWN = 0xC000, 64, 32
local SAT, SPRITES = 0xF000, 80
local TILES_HIGH, PALETTE = 4, 2
local BACK = 13
local ROW_HEIGHT, GLYPH_ROWS = 16, 9
local SCREEN_HEIGHT = 224
local STILL = 20
local SPACE = 10

-- The build: the cartridge's header (its serial and version, and its checksum), where the host gives the
-- cartridge at 0x01000000 on.
if mem.read(0x01000180, 14) ~= "GM G-5512   00" or mem.read(0x0100018E, 2) ~= "\x53\x75" then
    error("this script is for another build of the game", 0)
end

local cells = require("lib.cells")
local glyphs = require("lib.glyphs")
local source = reader.data("glyphs")
if not source then
    error("G-5512.glyphs is missing: the pictures of the game's font", 0)
end
local dictionary = glyphs.open(source)
local widths = cells.widths(source)
local vram = mem.region("vram")

local function word(bytes, at)
    return (bytes:byte(at + 1) << 8) | bytes:byte(at + 2)
end

--- The text sprites shown, in the table's order: { x, y, across, tile }, and the box they make.
local function text_sprites()
    local table_bytes = vram.read(SAT, 8 * SPRITES)
    if not table_bytes or #table_bytes < 8 * SPRITES then
        return nil
    end
    local out, seen, i = {}, {}, 0
    repeat
        seen[i] = true
        local y, size, attr, x = word(table_bytes, 8 * i), word(table_bytes, 8 * i + 2),
            word(table_bytes, 8 * i + 4), word(table_bytes, 8 * i + 6)
        local across, high = ((size >> 10) & 3) + 1, ((size >> 8) & 3) + 1
        local top = (y & 0x3FF) - 128
        if high == TILES_HIGH and across >= 2 and (attr >> 13) & 3 == PALETTE and top >= 0 and top < SCREEN_HEIGHT then
            out[#out + 1] = { x = (x & 0x1FF) - 128, y = top, across = across, tile = attr & 0x7FF }
        end
        i = size & 0x7F
    until i == 0 or seen[i]
    if #out == 0 then
        return nil
    end
    local box = { x0 = math.huge, y0 = math.huge, x1 = -math.huge, y1 = -math.huge }
    for _, s in ipairs(out) do
        box.x0, box.y0 = math.min(box.x0, s.x), math.min(box.y0, s.y)
        box.x1, box.y1 = math.max(box.x1, s.x + 8 * s.across), math.max(box.y1, s.y + 8 * TILES_HIGH)
    end
    return out, box
end

--- The picture the text sprites make, as lib/cells.lua takes one: value(x, y) from the box's corner; and the
--- bytes that must stand.
local function sprite_picture(sprites, box)
    local tiles = {}
    for _, s in ipairs(sprites) do
        local bytes = vram.read(32 * s.tile, 32 * s.across * TILES_HIGH)
        if not bytes or #bytes < 32 * s.across * TILES_HIGH then
            return nil
        end
        s.bytes = bytes
        tiles[#tiles + 1] = string.format("%d,%d,%d;", s.x, s.y, s.tile) .. bytes
    end
    local pic = { width = box.x1 - box.x0, height = box.y1 - box.y0 }
    function pic.value(x, y)
        local sx, sy = x + box.x0, y + box.y0
        for _, s in ipairs(sprites) do
            if sx >= s.x and sx < s.x + 8 * s.across and sy >= s.y and sy < s.y + 8 * TILES_HIGH then
                local column, row = (sx - s.x) // 8, (sy - s.y) // 8
                local at = 32 * (column * TILES_HIGH + row) + 4 * ((sy - s.y) % 8) + ((sx - s.x) % 8) // 2
                local b = s.bytes:byte(at + 1)
                return (sx - s.x) % 2 == 0 and b >> 4 or b & 0x0F
            end
        end
        return nil
    end
    return pic, table.concat(tiles)
end

--- The talk window's picture off the plane: the box of cells whose tiles are text tiles, each cell drawn as the
--- screen draws it (its tile, flipped as its word says); and the bytes that must stand. nil where no cell is one.
local function plane_picture()
    local map = vram.read(PLANE, 2 * ACROSS * DOWN)
    if not map or #map < 2 * ACROSS * DOWN then
        return nil
    end
    local r0, c0, r1, c1 = math.huge, math.huge, -1, -1
    for k = 0, ACROSS * DOWN - 1 do
        local tile = word(map, 2 * k) & 0x7FF
        if tile >= TEXT_FIRST and tile < TEXT_FIRST + TEXT_TILES then
            local r, c = k // ACROSS, k % ACROSS
            r0, c0, r1, c1 = math.min(r0, r), math.min(c0, c), math.max(r1, r), math.max(c1, c)
        end
    end
    if r1 < 0 then
        return nil
    end
    local cell_tiles, key = {}, {}
    for r = r0, r1 do
        for c = c0, c1 do
            local w = word(map, 2 * (r * ACROSS + c))
            local bytes = vram.read(32 * (w & 0x7FF), 32)
            if not bytes or #bytes < 32 then
                return nil
            end
            cell_tiles[(r - r0) * (c1 - c0 + 1) + (c - c0)] = { bytes = bytes, h = (w >> 11) & 1, v = (w >> 12) & 1 }
            key[#key + 1] = string.format("%04X", w) .. bytes
        end
    end
    local across = c1 - c0 + 1
    local pic = { width = 8 * across, height = 8 * (r1 - r0 + 1) }
    function pic.value(x, y)
        local cell = cell_tiles[(y // 8) * across + x // 8]
        if not cell then
            return nil
        end
        local px, py = x % 8, y % 8
        if cell.h == 1 then px = 7 - px end
        if cell.v == 1 then py = 7 - py end
        local b = cell.bytes:byte(4 * py + px // 2 + 1)
        return px % 2 == 0 and b >> 4 or b & 0x0F
    end
    return pic, table.concat(key)
end

local function ink(value)
    return value ~= nil and value ~= BACK and value ~= 0
end

local function row_text(found)
    local out, marks, last_end = {}, {}, nil
    for _, c in ipairs(found) do
        if last_end and c.x - last_end >= SPACE then
            out[#out + 1] = string.rep("\u{3000}", (c.x - last_end) // SPACE)
            marks[#marks + 1] = false
        end
        out[#out + 1] = c.character or "\u{FFFD}"
        marks[#marks + 1] = c.character or c.hash
        last_end = c.x + c.w
    end
    if #out > 0 then
        dictionary:line(marks)     -- the row, logged once where a picture in it has no reading (for yomikata unread)
    end
    return table.concat(out)
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
    local pic, now = plane_picture()
    if not pic then
        local sprites, box = text_sprites()
        if sprites then
            pic, now = sprite_picture(sprites, box)
        end
    end
    if not pic then
        last, stood = nil, 0
        return
    end
    stood = now == last and stood + 1 or 0
    last = now
    if stood ~= STILL then
        return
    end
    local lines = {}
    for top = 0, pic.height - 1, ROW_HEIGHT do      -- a last row may stand on fewer tile rows
        -- A row whose ink spans fewer than GLYPH_ROWS pixel rows is glyphs cut by the window's scroll: the page is
        -- between two, and nothing is said.
        local rows = 0
        for _, mask in pairs(cells.columns(pic, top, ROW_HEIGHT, 0, pic.width, ink)) do
            rows = rows | mask
        end
        if rows ~= 0 then
            local low, high = 0, ROW_HEIGHT - 1
            while (rows >> low) & 1 == 0 do low = low + 1 end
            while (rows >> high) & 1 == 0 do high = high - 1 end
            if high - low + 1 < GLYPH_ROWS then
                return
            end
        end
        local _, found = cells.proportional(pic, top, ROW_HEIGHT, 0, pic.width, dictionary, widths, nil, ink)
        local line = row_text(found)
        if line ~= "" then
            lines[#lines + 1] = line
        end
    end
    local page = table.concat(lines, "\n")
    if page ~= "" and page ~= said then
        said = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end)
