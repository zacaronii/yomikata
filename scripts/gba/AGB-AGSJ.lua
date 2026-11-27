-- reader: title 黄金の太陽 開かれし封印 (Ougon no Taiyou: Hirakareshi Fuuin, Game Boy Advance)
-- reader: reads the message window's rows off the sprites that draw them once the page stands, each by its picture
-- reader: version AGB-AGSJ
-- reader: build 81fab74a
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The script lies packed in the cartridge, and no page of it stands in memory as codes (page_buffer found none):
-- the game draws each character into a sprite of its own, 16 by 16 pixels, its tiles in the sprites' video memory
-- (TILES on, 4 bits a pixel, a sprite's four tiles one after another), and places the sprites in the window by
-- the sprite table (OAM: 8 bytes a sprite, its y, x, shape, size and first tile). The characters' sprites are the
-- square 16-by-16 ones of palette 0 and priority 0; the rain, the portraits and the cursor are other shapes,
-- palettes or priorities. The font is proportional: a character's sprite stands as far from the last as its
-- width, and a wider gap is a space.
--
-- So the rows are the characters' sprites by their y, each row by x, and each sprite is known by its picture in
-- the dictionary beside this script (a hash to a character): read in play, since the font in the cartridge is
-- packed too. A picture it hasn't got comes out as U+FFFD and is logged for `yomikata unread`.
--
-- A page is said once its sprites and their pictures have stood the same for STILL frames, with some in place;
-- each page once, until another comes. It reads the sprite table and video memory at the console's own
-- addresses: where a host doesn't give them, it reads nothing.
--
-- Not covered: menus, fights' messages, the screens before the story.

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
local OAM, SPRITES = 0x07000000, 128
local TILES, TILE = 0x06010000, 32
local CELL, CELL_BYTES = 16, 4 * 32
local SPACE = 16          -- a gap from one character's x to the next's of this much or more holds a space
local STILL = 20

-- The build: the start of the program the game copies into the CPU's memory (CODE, the same from the opening's
-- first frames to its end and in every snap taken in play), and the cartridge's header (its title and code)
-- where the host gives the cartridge to read. At power-on the copy isn't made yet (the region reads as nothing at
-- frame 0, while work RAM is there, then as zeros): then nothing is decided, and nothing read, until it is there or
-- WAIT frames have passed. A refusal after load is logged once, and the script reads nothing. Memory that can't be
-- read at all is refused at once.
local CODE, CODE_BYTES = 0x03000000, "\x01\x33\xA0\xE3\x00\x22\xB3\xE5\xB8\x10\xD3\xE1\x00\x00\x4F\xE1"
local WAIT = 120
local header = mem.read(0x080000A0, 16)
local code = mem.read(CODE, 16)
local work = mem.read(0x02000000, 16)
local unread = not code or #code < 16
if (header and #header == 16 and header ~= "OugonTaiyo_AAGSJ") or (unread and not (work and #work == 16))
    or (not unread and code ~= string.rep("\0", 16) and code ~= CODE_BYTES) then
    error("this script is for another build of the game", 0)
end
-- nil: not decided yet; true: this build; false: another, and nothing is read.
local build = (header and #header == 16) or code == CODE_BYTES or nil
local waited = 0

--- Whether to read: decides the build once the copy is there or WAIT frames have passed.
local function this_build()
    if build ~= nil then
        return build
    end
    waited = waited + 1
    local now = mem.read(CODE, 16)
    if now == CODE_BYTES then
        build = true
    elseif (now and #now == 16 and now ~= string.rep("\0", 16)) or waited >= WAIT then
        build = false
        reader.warn("this script is for another build of the game: it reads nothing")
    end
    return build == true
end

local glyphs = require("lib.glyphs")
local source = reader.data("glyphs")
if not source then
    error("AGB-AGSJ.glyphs is missing: the pictures of the game's font", 0)
end
local dictionary = glyphs.open(source)

--- The characters' sprites: { x, y, tile } of each square 16-by-16 sprite of palette 0 and priority 0 shown.
local function text_sprites(table_bytes)
    local out = {}
    for i = 0, SPRITES - 1 do
        local a0 = table_bytes:byte(8 * i + 1) | (table_bytes:byte(8 * i + 2) << 8)
        local a1 = table_bytes:byte(8 * i + 3) | (table_bytes:byte(8 * i + 4) << 8)
        local a2 = table_bytes:byte(8 * i + 5) | (table_bytes:byte(8 * i + 6) << 8)
        local hidden = (a0 >> 8) & 3 == 2
        local y, x = a0 & 0xFF, a1 & 0x1FF
        if not hidden and a0 >> 14 == 0 and a1 >> 14 == 1 and a2 >> 12 == 0 and (a2 >> 10) & 3 == 0 and y < 160 then
            out[#out + 1] = { x = x >= 256 and x - 512 or x, y = y, tile = a2 & 0x3FF }
        end
    end
    table.sort(out, function(a, b) return a.y < b.y or (a.y == b.y and a.x < b.x) end)
    return out
end

--- A sprite's picture as lib/glyphs.lua hashes one: 16 rows of 16 pixels, 4 bits a pixel, the leftmost in a
--- byte's high bits (the tiles keep the leftmost in the low bits, a tile's rows 4 bytes each).
local function picture(cell)
    local rows = {}
    for y = 0, CELL - 1 do
        local row = {}
        for half = 0, 1 do
            local tile = (y // 8) * 2 + half
            local at = tile * TILE + (y % 8) * 4
            for k = 1, 4 do
                local b = cell:byte(at + k)
                row[#row + 1] = string.char(((b & 0x0F) << 4) | (b >> 4))
            end
        end
        rows[#rows + 1] = table.concat(row)
    end
    return table.concat(rows)
end

local function page_text(sprites, cells)
    local lines, row, marks, last = {}, {}, {}, nil
    local function finish()
        if #row > 0 then
            dictionary:line(marks)     -- the row, logged once where a picture in it has no reading
            lines[#lines + 1] = table.concat(row)
        end
        row, marks = {}, {}
    end
    for i, s in ipairs(sprites) do
        if last and s.y ~= last.y then
            finish()
            last = nil
        end
        if last and s.x - last.x >= SPACE then
            row[#row + 1], marks[#marks + 1] = "\u{3000}", false
        end
        local character, hash = dictionary:read(cells[i], CELL, CELL, 4)
        row[#row + 1] = character or "\u{FFFD}"
        marks[#marks + 1] = character or hash
        last = s
    end
    finish()
    return table.concat(lines, "\n")
end

local said, last, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
end)

reader.on_frame(function()
    if not this_build() then
        return
    end
    local table_bytes = mem.read(OAM, 8 * SPRITES)
    if not table_bytes or #table_bytes < 8 * SPRITES then
        return
    end
    local sprites = text_sprites(table_bytes)
    if #sprites == 0 then
        last, stood = nil, 0
        return
    end
    local cells, key = {}, {}
    for i, s in ipairs(sprites) do
        local bytes = mem.read(TILES + s.tile * TILE, CELL_BYTES)
        if not bytes or #bytes < CELL_BYTES then
            return
        end
        cells[i] = picture(bytes)
        key[#key + 1] = string.format("%d,%d;", s.x, s.y) .. bytes
    end
    local now = table.concat(key)
    stood = now == last and stood + 1 or 0
    last = now
    if stood ~= STILL then
        return
    end
    local page = page_text(sprites, cells)
    if page ~= "" and page ~= said then
        said = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end)
