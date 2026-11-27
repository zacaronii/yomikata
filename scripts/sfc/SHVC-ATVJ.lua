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
--
-- Some pictures a game draws where its glyphs go aren't text: a menu's cursor, a prompt's arrow, a decoration.
-- The file names those on lines of their own,
--     # not text: <HASH> <HASH> …
-- (a comment to anything else that reads the file), and `dictionary:text(picture)` says false for them, so a
-- script leaves them out of a page; a page of nothing else is no page.

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

--- The hashes the file says aren't text (`# not text:` lines), as a set.
function glyphs.not_text(source)
    local out = {}
    for line in (source or ""):gmatch("[^\r\n]+") do
        local list = line:match("^#%s*not text:%s*(.+)$")
        for word in (list or ""):gmatch("%S+") do
            if word:find("^%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x$") then      -- a hash; anything after it is a note
                out[word:upper()] = true
            end
        end
    end
    return out
end

--- A dictionary to ask: `dictionary:read(picture, width, height, bits)` gives the picture's character, or nil
--- (and logs the picture, the first time it is asked for), and its hash; `dictionary:text(picture)` whether the
--- picture may be text at all (false for one the file says isn't).
function glyphs.open(source)
    local dictionary = { known = glyphs.parse(source), not_text = glyphs.not_text(source), logged = {} }

    function dictionary:text(picture)
        return not self.not_text[text.hash(picture):upper()]
    end

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
