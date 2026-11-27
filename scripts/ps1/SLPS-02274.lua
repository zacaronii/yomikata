-- reader: title 夕闇通り探検隊 (Yuuyami Doori Tankentai)
-- reader: reads the subtitle on screen: the page from the scene's script, each character by its picture in video memory
-- reader: version SLPS-02274
-- reader: build 1b2eee02e9ae542620be83a142532dc39c468886
-- reader: no hooks
-- reader: no layout where the subtitle is drawn isn't looked into

-- How the game keeps what it shows.
--
-- A scene's script lies in work RAM whole: a character a 4-byte entry, a code of 16 bits and a word that is
-- 0x0200 (0x0000 for a row's last); an entry of 0xFFFF and 0x0100 ends a page, one of 0xFFFF and another word
-- starts a row. The codes are numbers of the scene's own: the same character has another in the next scene, so a
-- code says nothing by itself. The script is where a scene's table of sections says (the sixth word): a talk's
-- table is where the word at TALK points, plus 0x18; with none, the scene's table is at SCENE_TABLE. SHOWN is 1
-- while a subtitle is up (2 for the talk walking brings). The page shown is counted: in a talk, from 0, at
-- TALK_PAGE in the block the talk's record's second word points to; in a scene, from 1, at PAGE.
--
-- The game draws the page's characters, in their order (a space too), into a cache in video memory: cells of 16
-- by 16 pixels, 4 bits a pixel (read as a bit a pixel: ink or the background), TEN a row from halfword x CACHE_X, ROW_LINES apart, the last page's leftovers
-- after them. So the page shown is the counted one where its codes repeat as the cells' pictures do (where it
-- doesn't, the longest run of one or two rows of a page that fits: the window shows two rows at a time, and a
-- longer page in turns), its rows come from the script, and each character is known by the
-- picture in its cell: in the dictionary beside this script (a hash to a character), read in play. A picture it
-- hasn't got comes out as U+FFFD and is logged for `yomikata unread`.
--
-- A page is said once the cache has stood the same for STILL frames while a subtitle is up; each page once,
-- until another comes. It reads video memory (the region `vram`): where the host doesn't give it, it reads
-- nothing.
--
-- Not covered: menus (the notebook's captions among them), the screens before the story.

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
local SCENE_TABLE, TALK, PAGE, SHOWN = 0x80094014, 0x8009516C, 0x80167A20, 0x8008191C
local TALK_PAGE = 0x8C
local CACHE_X, TEN, ROW_LINES, CELL = 832, 10, 24, 16
local MOST = 60          -- characters a page holds at most
-- A space's cell: its left half filled (with 0, not the background), its right half empty, row after row.
local SPACE_CELL = string.rep("\xFF\0", 16)
local STILL = 20

if mem.u32(0x80011194) ~= 0x0C0076C8 or mem.u32(0x8003E424) ~= 0x0C00FAA5 then
    error("this script is for another build of the game", 0)
end

local glyphs = require("lib.glyphs")
local source = reader.data("glyphs")
if not source then
    error("SLPS-02274.glyphs is missing: the pictures of the game's characters", 0)
end
local dictionary = glyphs.open(source)
local vram = mem.region("vram")

local function in_ram(address)
    return address and address >= 0x80000000 and address < 0x80200000
end

--- Where the script lies now (the talk's, or the scene's), and the page shown, from 1.
local function script_start()
    local talk = mem.u32(TALK)
    local page, table_at
    if in_ram(talk) then
        local state = mem.u32(talk + 4)
        page = in_ram(state) and (mem.u32(state + TALK_PAGE) or -1) + 1 or 0
        table_at = talk + 0x18
    else
        page, table_at = mem.u8(PAGE) or 0, SCENE_TABLE
    end
    local at = mem.u32(table_at + 0x14)
    return in_ram(at) and at or nil, page
end

local parsed_at, parsed = nil, nil

--- The script's pages from `at`, each a list of rows, each a list of codes.
local function pages_of(at)
    local pages, rows, row = {}, {}, {}
    for _ = 1, 40000 do
        local code, word = mem.u16(at), mem.u16(at + 2)
        if not code or not word then
            break
        end
        at = at + 4
        if code == 0xFFFF and word == 0x0100 then
            rows[#rows + 1] = row
            pages[#pages + 1] = rows
            rows, row = {}, {}
        elseif code == 0xFFFF then
            rows[#rows + 1] = row
            row = {}
        elseif (word == 0x0200 or word == 0x0000) and code < 0x400 then
            row[#row + 1] = code
        else
            break
        end
    end
    return pages
end

--- The same, kept while the script stays where it is.
local function cached_pages(at)
    if at ~= parsed_at then
        parsed = pages_of(at)
        parsed_at = at
    end
    return parsed
end

--- Whether a page's codes repeat where the cells' pictures do, over its length.
local function fits(page, hashes)
    local codes = {}
    for _, row in ipairs(page) do
        for _, code in ipairs(row) do
            codes[#codes + 1] = code
        end
    end
    if #codes == 0 or #codes > MOST then
        return false
    end
    for i = 1, #codes do
        for j = i + 1, #codes do
            if (codes[i] == codes[j]) ~= (hashes[i] == hashes[j]) then
                return false
            end
        end
    end
    return true, #codes
end

--- Cache cell i (from 0) as lib/glyphs.lua hashes a picture: 16 rows of 2 bytes, a bit a pixel, the leftmost in
--- a byte's high bit; ink wherever a pixel isn't the background (15). The shades a character is drawn in differ from
--- one scene to another (a talk's by a step or so); its shape doesn't.
local function cell(i)
    local x, y = CACHE_X + 6 * (i % TEN), ROW_LINES * (i // TEN)
    local out = {}
    for r = 0, CELL - 1 do
        local bytes = vram.read(((y + r) * 1024 + x) * 2, 8)
        if not bytes or #bytes < 8 then
            return nil
        end
        local bits = 0
        for k = 1, 8 do
            local b = bytes:byte(k)
            bits = (bits << 2) | (((b & 0x0F) ~= 0x0F) and 2 or 0) | (((b >> 4) ~= 0x0F) and 1 or 0)
        end
        out[#out + 1] = string.char(bits >> 8, bits & 0xFF)
    end
    return table.concat(out)
end

local said, last, stood = nil, nil, 0
-- The talk walking brings (SHOWN 2) comes up while the cache still holds a line from before it, which no row of
-- the script then tells from the one coming: the cache as it stood when such a talk came up isn't read.
local held, was = nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood, held, was = nil, nil, 0, nil, 0
end)

reader.on_frame(function()
    local shown = mem.u8(SHOWN) or 0
    if not vram or shown == 0 then
        last, stood, held, was = nil, 0, nil, 0
        return
    end
    local cells = {}
    for i = 0, MOST - 1 do
        cells[#cells + 1] = cell(i)
        if not cells[#cells] then
            return
        end
    end
    local now = table.concat(cells)
    if shown == 2 and was ~= 2 then
        held = now
    end
    was = shown
    stood = now == last and stood + 1 or 0
    last = now
    if stood ~= STILL or now == held then
        return
    end
    local at, page = script_start()
    if not at then
        return
    end
    local pages = cached_pages(at)
    local hashes = {}
    for i, c in ipairs(cells) do
        hashes[i] = text.hash(c)
    end
    local chosen, size = nil, 0
    local counted = pages[page]
    local ok, n = counted and fits(counted, hashes)
    if ok then
        chosen, size = counted, n
    else
        -- Not counted (the talk walking brings keeps no count): any one or two rows running on in a page, as
        -- the window shows them, the longest that fits.
        for _, page in ipairs(pages) do
            for first = 1, #page do
                for last = first, math.min(first + 1, #page) do
                    local part = { table.unpack(page, first, last) }
                    ok, n = fits(part, hashes)
                    if ok and n > size then
                        chosen, size = part, n
                    end
                end
            end
        end
    end
    if not chosen then
        return
    end
    local lines, i = {}, 0
    for _, row in ipairs(chosen) do
        local out, marks = {}, {}
        for _ = 1, #row do
            i = i + 1
            local character, hash
            if cells[i] == SPACE_CELL then
                character = "\u{3000}"          -- a space
            else
                character, hash = dictionary:read(cells[i], CELL, CELL, 1)
            end
            out[#out + 1] = character or "\u{FFFD}"
            marks[#marks + 1] = character or hash
        end
        if #out > 0 then
            dictionary:line(marks)
            lines[#lines + 1] = table.concat(out)
        end
    end
    local page_text = table.concat(lines, "\n")
    if page_text ~= "" and page_text ~= said then
        said = page_text
        reader.emit{ text = page_text, channel = "dialogue", id = text.hash(page_text) }
    end
end)
