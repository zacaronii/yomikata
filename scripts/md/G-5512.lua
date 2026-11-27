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
__module["lib.cells"] = function()
-- Glyph cells out of a picture a game draws its text into: tiles of 1, 2 or 4 bits a pixel (as a Super Famicom or
-- a Game Boy keeps them, or packed rows of a bit a pixel), laid out some tiles a row. For a game that keeps no code
-- of what it shows, only the pixels: each cell is cut out and known by its picture, in the dictionary that
-- lib/glyphs.lua reads (`<id>.glyphs` beside the script, made from the game's own font with `yomikata rom-font
-- --glyphs`, or read off what was logged with `yomikata unread`).
--
-- A cell's picture is what lib/glyphs.lua hashes: its rows from the top, a bit a pixel (ink or not), each row a
-- whole number of bytes, the leftmost pixel in a byte's high bits. Cells are cut on a fixed grid, or, for a
-- proportional font, one after another along a row: from the first column with ink, the widest picture of the
-- dictionary's widths that it has is taken; where none fits, the cell runs to the next blank column and is logged.

local cells = {}

-- Bytes a tile, and how its planes lie: two bits a pixel as a pair of bytes a row (Super Famicom, Game Boy); four
-- as two such halves, planes 0-1 then 2-3; a bit a pixel as a byte a row.
local FORMS = {
    snes2 = { size = 16, bits = 2 }, gb2 = { size = 16, bits = 2 },
    snes4 = { size = 32, bits = 4 }, bits1 = { size = 8, bits = 1 },
}

--- A picture over `bytes`: tiles of `form` (snes2, snes4, gb2 or bits1), `across` tiles a row. Gives an object with
--- width, height and value(x, y), the pixel's value (0 to 3, or 15); nil where `bytes` ends.
function cells.picture(bytes, form, across)
    local f = FORMS[form]
    if not f then
        error("cells.picture: form is snes2, snes4, gb2 or bits1", 2)
    end
    local tiles = #bytes // f.size
    local pic = { width = across * 8, height = (tiles + across - 1) // across * 8 }

    function pic.value(x, y)
        if x < 0 or y < 0 or x >= pic.width or y >= pic.height then
            return nil
        end
        local tile = (y // 8) * across + x // 8
        if tile >= tiles then
            return nil
        end
        local at, row, bit = tile * f.size, y % 8, 7 - x % 8
        if f.bits == 1 then
            return (bytes:byte(at + row + 1) >> bit) & 1
        end
        local value = ((bytes:byte(at + 2 * row + 1) >> bit) & 1) | (((bytes:byte(at + 2 * row + 2) >> bit) & 1) << 1)
        if f.bits == 4 then
            value = value | (((bytes:byte(at + 16 + 2 * row + 1) >> bit) & 1) << 2)
                          | (((bytes:byte(at + 16 + 2 * row + 2) >> bit) & 1) << 3)
        end
        return value
    end

    --- The 8 pixels of tile column `tx` on row `y` as a byte, the leftmost in the high bit: set where any plane in
    --- `planes` (a bit for each, 1 the first) is set. 0 where `bytes` ends.
    function pic.byte(tx, y, planes)
        local tile = (y // 8) * across + tx
        if y < 0 or tx < 0 or tx >= across or tile >= tiles then
            return 0
        end
        local at, row, out = tile * f.size, y % 8, 0
        if f.bits == 1 then
            return planes & 1 ~= 0 and bytes:byte(at + row + 1) or 0
        end
        if planes & 1 ~= 0 then out = out | bytes:byte(at + 2 * row + 1) end
        if planes & 2 ~= 0 then out = out | bytes:byte(at + 2 * row + 2) end
        if f.bits == 4 then
            if planes & 4 ~= 0 then out = out | bytes:byte(at + 16 + 2 * row + 1) end
            if planes & 8 ~= 0 then out = out | bytes:byte(at + 16 + 2 * row + 2) end
        end
        return out
    end

    return pic
end

--- Ink, by default: any value but 0. A game's own (its window's colour, a shadow) is given as a function of the
--- value, e.g. function(v) return v >= 2 end, or, fastest, as the planes that make ink: a number with a bit for each
--- (2 for "values 2 and 3" at 2 bits a pixel).
local function default_ink(value)
    return value ~= nil and value ~= 0
end

--- The ink of columns x0 to x1 - 1 over rows y to y + h - 1 (h at most 62): for each column, a number with bit r set
--- where row y + r has ink. Read once and kept, so that cutting a row costs a pass over its pixels.
function cells.columns(pic, y, h, x0, x1, ink)
    local cols = {}
    for x = x0, x1 - 1 do
        cols[x] = 0
    end
    if type(ink) == "number" then
        for r = 0, h - 1 do
            for tx = x0 // 8, (x1 - 1) // 8 do
                local b = pic.byte(tx, y + r, ink)
                if b ~= 0 then
                    for px = 0, 7 do
                        local x = tx * 8 + px
                        if x >= x0 and x < x1 and (b >> (7 - px)) & 1 ~= 0 then
                            cols[x] = cols[x] | (1 << r)
                        end
                    end
                end
            end
        end
    else
        ink = ink or default_ink
        for x = x0, x1 - 1 do
            local m = 0
            for r = 0, h - 1 do
                if ink(pic.value(x, y + r)) then
                    m = m | (1 << r)
                end
            end
            cols[x] = m
        end
    end
    return cols
end

--- A cell's picture from column masks (cells.columns): w columns from x, h rows; and whether it is blank.
local function packed(cols, x, w, h)
    local rows, blank = {}, true
    for r = 0, h - 1 do
        local bytes = {}
        for from = 0, w - 1, 8 do
            local byte = 0
            for px = 0, 7 do
                local c = from + px < w and cols[x + from + px] or 0
                byte = byte << 1 | ((c >> r) & 1)
            end
            blank = blank and byte == 0
            bytes[#bytes + 1] = string.char(byte)
        end
        rows[#rows + 1] = table.concat(bytes)
    end
    return table.concat(rows), blank
end

--- Whether column `x` has ink in rows y to y + h - 1.
function cells.inked(pic, x, y, h, ink)
    return cells.columns(pic, y, h, x, x + 1, ink)[x] ~= 0
end

--- The picture of the cell at x, y of w by h pixels, as lib/glyphs.lua hashes one (a bit a pixel), and whether it
--- is blank.
function cells.cell(pic, x, y, w, h, ink)
    return packed(cells.columns(pic, y, h, x, x + w, ink), x, w, h)
end

--- `count` cells of w by h on a fixed grid from x, y, one after another along the row: each { picture, blank }.
function cells.fixed(pic, x, y, w, h, count, ink)
    local out = {}
    for k = 0, count - 1 do
        local picture, blank = cells.cell(pic, x + k * w, y, w, h, ink)
        out[#out + 1] = { picture = picture, blank = blank, x = x + k * w, w = w }
    end
    return out
end

--- The widths a dictionary's pictures have, from its `# widths 12 11 7 ...` line, widest first.
function cells.widths(source)
    local out = {}
    local line = (source or ""):match("#%s*widths%s+([%d%s]+)")
    for n in (line or ""):gmatch("%d+") do
        out[#out + 1] = tonumber(n)
    end
    table.sort(out, function(a, b) return a > b end)
    return out
end

--- The characters of one row of a proportional font: the strip of `h` rows from `y`, columns x0 to x1 - 1.
--- `dictionary` is lib/glyphs.lua's (its `known` hashes and `read`, which logs a picture it hasn't got), `widths`
--- the widths to try, widest first. A gap of at least `space` blank columns (default: none are counted) is a
--- wide space. Gives the row's text (a picture the dictionary hasn't got as U+FFFD) and its cells, each
--- { x, w, hash, character }.
function cells.proportional(pic, y, h, x0, x1, dictionary, widths, space, ink)
    local cols = cells.columns(pic, y, h, x0, x1, ink)
    local out, found, x, gap = {}, {}, x0, 0
    while x < x1 do
        if cols[x] == 0 then
            x, gap = x + 1, gap + 1
        else
            if space and #found > 0 and gap >= space then
                for _ = 1, gap // space do
                    out[#out + 1] = "\u{3000}"
                end
            end
            gap = 0
            local taken
            for _, w in ipairs(widths) do
                -- A dictionary's pictures end in a column with ink: a slice that ends in a blank one is the
                -- narrower glyph's picture padded out to the same bytes, and is left for that width.
                if x + w <= x1 and cols[x + w - 1] ~= 0 then
                    local picture = packed(cols, x, w, h)
                    local hash = text.hash(picture):upper()
                    local character = dictionary.known[hash]
                    if character then
                        taken = { x = x, w = w, hash = hash, character = character }
                        break
                    end
                end
            end
            if not taken then
                local w = 1
                while x + w < x1 and cols[x + w] ~= 0 do
                    w = w + 1
                end
                local character, hash = dictionary:read(packed(cols, x, w, h), w, h, 1)
                taken = { x = x, w = w, hash = hash, character = character }
            end
            found[#found + 1] = taken
            out[#out + 1] = taken.character or "\u{FFFD}"
            x = x + taken.w
        end
    end
    return table.concat(out), found
end

return cells
end
__module["lib.glyphs"] = function()
-- A dictionary of glyph pictures (docs/script-api.md, "Files"): for a game that brings its characters as
-- pictures and numbers them anew in every scene, so that a code says nothing. A picture is known by
-- text.hash of its bytes, and the file `<id>.glyphs` beside the script says which character each hash is.
--
-- A picture the file hasn't got is logged once, as
--     glyph <HASH> <width>x<height>x<bits> <the picture's bytes in hex>
-- which is all `yomikata unread` needs to draw it for reading (docs/harness.md, "A game's own font"). For
-- that the bytes hashed are the picture's rows from the top, each a whole number of bytes, a pixel `bits`
-- bits, the leftmost in a byte's high bits. A game that keeps its pictures otherwise has them repacked by
-- its script first.
--
-- A glyph is read surest in its sentence. So a script may also say which pictures made up a row of text
-- that had one without a reading, and that is logged once too, as
--     glyph-line <HASH> <HASH> _ =<characters> <HASH> …
-- a hash for each picture in order, "_" for a space, and "=" before characters the script knows by code:
-- `yomikata unread` then draws the row whole.

local glyphs = {}

--- Reads a dictionary's text (the file a script asks reader.data for): a picture's hash -> its character.
function glyphs.parse(source)
    local known = {}
    for line in (source or ""):gmatch("[^\r\n]+") do
        local hash, character = line:match("^(%x+)=(.+)$")
        if hash then
            known[hash:upper()] = character
        end
    end
    return known
end

--- A dictionary to ask: `dictionary:read(picture, width, height, bits)` gives the picture's character, or nil
--- (and logs the picture, the first time it is asked for), and its hash.
function glyphs.open(source)
    local dictionary = { known = glyphs.parse(source), logged = {} }

    function dictionary:read(picture, width, height, bits)
        local hash = text.hash(picture):upper()
        local character = self.known[hash]
        if not character and not self.logged[hash] then
            self.logged[hash] = true
            reader.log(string.format("glyph %s %dx%dx%d %s", hash, width, height, bits,
                (picture:gsub(".", function(c)
                    return string.format("%02x", c:byte())
                end))))
        end
        return character, hash
    end

    --- A row of text the script has read, as the hashes `read` gave for its pictures in order; false for a
    --- space, and for what the script knows by code, not by picture, the characters themselves (without
    --- spaces). Logged once, if the dictionary lacks one of the pictures.
    function dictionary:line(hashes)
        local marks, lacking = {}, false
        for i, hash in ipairs(hashes) do
            if hash and not hash:find("^%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x$") then
                marks[i] = "=" .. hash
            else
                marks[i] = hash or "_"
                lacking = lacking or (hash and not self.known[hash])
            end
        end
        local row = table.concat(marks, " ")
        if lacking and not self.logged[row] then
            self.logged[row] = true
            reader.log("glyph-line " .. row)
        end
    end

    return dictionary
end

return glyphs
end
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
