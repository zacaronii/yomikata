-- reader: title ヴァルキリープロファイル (Valkyrie Profile, disc 1)
-- reader: reads the talk window's rows once a page has typed out, each character by its picture
-- reader: version SLPM-86379
-- reader: build 33619ac1f19a4679b798a0f0a000a341a58eb748
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The script lies packed in the disc's one archive; a scene's lies unpacked in work RAM while it plays, in codes
-- of the scene's own (numbered in the order the scene first uses its characters, so a code says nothing by
-- itself). A byte under 0x80 is a character; one from 0x80 is a character with the byte after it (1 to 0x7F), or,
-- with 0x80 after it, an order with OPS[it] bytes of its own: 0x80 ends a row, 0x83 waits, 0x91 turns the page
-- after a time. A page ends at a zero byte (the window then waits for the button, or closes once 0x91's time is
-- up) or at the order 0x81 (the next page follows in the same window).
--
-- SHOWN points to the window shown (0 while none is). In it, at ROWS, its rows, ROW_BYTES each: where the row's
-- script starts and where it is typed to, where its first character's cell lies (in a sheet of 16 cells a row: the
-- sheet's row in the high half-word, 16 times the column in the low), and in the top byte of the fifth word the
-- row's place on its page (0 on a page's first, the speaker's name where there is one); at LAST_ROW, the row being
-- typed (from 0). A window that turns pages keeps them all, one after another.
-- The window's characters are drawn, in order, into cells at CELLS: 16 by 14 pixels, 4 bits a pixel (read as a
-- bit a pixel: ink or the background, 0), one after another; the last window's leftovers lie after them.
--
-- So the page shown is the rows from the last that starts a page, each its first cell and as many cells as its
-- script has characters, and each character is known by its picture: in the dictionary beside this script (a hash
-- to a character), read in play. A picture it hasn't got comes out as U+FFFD and is logged for `yomikata unread`.
--
-- A page is said once it has typed to its end (or to the 0x91 that turns it) and its rows have stood the same for
-- STILL frames; each page once, until another comes. The next page in a window begins with nothing typed for a
-- while, the last still shown.
--
-- Not covered: menus, fights' messages, the screens before the story. An order this script doesn't know (in OPS)
-- leaves the page unsaid.

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
local SHOWN, LAST_ROW, ROWS, ROW_BYTES, MOST_ROWS = 0x8007ECD8, 0x338, 0x360, 20, 16
local CELLS, CELL_W, CELL_H, CELL_BYTES = 0x800AD7A0, 16, 14, 112
local OPS = { [0x80] = 0, [0x81] = 0, [0x83] = 1, [0x8C] = 2, [0x91] = 2, [0x92] = 0, [0x93] = 2 }
local TURN, NEXT_PAGE = 0x91, 0x81
local STILL = 6

if mem.u32(0x8001074C) ~= 0x0C008EB4 or mem.u32(0x80016534) ~= 0x0C005475 then
    error("this script is for another build of the game", 0)
end

local glyphs = require("lib.glyphs")
local source = reader.data("glyphs")
if not source then
    error("SLPM-86379.glyphs is missing: the pictures of the game's characters", 0)
end
local dictionary = glyphs.open(source)

local function in_ram(address)
    return address and address >= 0x80000000 and address < 0x80200000
end

--- How many characters a row's script holds from `from` to `to`, and whether the page ends in it; nil where it
--- holds an order this script doesn't know.
local function characters(from, to)
    local n, at = 0, from
    while at < to do
        local b = mem.u8(at)
        if not b then
            return nil
        elseif b == 0 then
            return n, true
        elseif b < 0x80 then
            n, at = n + 1, at + 1
        else
            local next = mem.u8(at + 1)
            if next == 0x80 then
                local args = OPS[b]
                if not args then
                    return nil
                elseif b == NEXT_PAGE then
                    return n, true
                end
                at = at + 2 + args
            elseif next and next >= 1 and next < 0x80 then
                n, at = n + 1, at + 2
            else
                return nil
            end
        end
    end
    return n, false
end

--- Cell i (from 0) as lib/glyphs.lua hashes a picture: 14 rows of 2 bytes, a bit a pixel, the leftmost in a
--- byte's high bit (the cells keep the leftmost pixel in a byte's low half).
local function cell(i)
    local bytes = mem.read(CELLS + CELL_BYTES * i, CELL_BYTES)
    if not bytes or #bytes < CELL_BYTES then
        return nil
    end
    local out = {}
    for r = 0, CELL_H - 1 do
        local bits = 0
        for k = 1, 8 do
            local b = bytes:byte(8 * r + k)
            bits = (bits << 2) | (((b & 0x0F) ~= 0) and 2 or 0) | (((b >> 4) ~= 0) and 1 or 0)
        end
        out[#out + 1] = string.char(bits >> 8, bits & 0xFF)
    end
    return table.concat(out)
end

--- The row a page that runs to row `last` starts at.
local function page_start(window, last)
    for r = last, 1, -1 do
        local place = mem.u32(window + ROWS + ROW_BYTES * r + 16)
        if not place or place >> 24 == 0 then
            return r
        end
    end
    return 0
end

--- Rows `first` to `last`, each as { first cell, characters }, the characters in all, whether the page ends in
--- them, and where the last is typed to; nil where they can't be read.
local function rows_of(window, first, last)
    local rows, total, ended, to = {}, 0, false, nil
    for r = first, last do
        local at = window + ROWS + ROW_BYTES * r
        local from, cell_at = mem.u32(at), mem.u32(at + 8)
        to = mem.u32(at + 4)
        if not in_ram(from) or not in_ram(to) or to < from or not cell_at then
            return nil
        end
        local n, ends = characters(from, to)
        if not n then
            return nil
        end
        rows[#rows + 1] = { first = (cell_at >> 16) * 16 + (cell_at & 0xFFFF) // 16, count = n }
        total, ended = total + n, ends
    end
    return rows, total, ended, to
end

--- The page shown, as its rows, once it has typed out; nil otherwise. A page that has begun with nothing typed
--- yet leaves the one before it shown.
local function page_shown(window)
    local last = mem.u32(window + LAST_ROW)
    if not last or last >= MOST_ROWS then
        return nil
    end
    local first = page_start(window, last)
    local rows, total, ended, to = rows_of(window, first, last)
    if rows and total == 0 and first > 0 then
        rows, total, ended, to = rows_of(window, page_start(window, first - 1), first - 1)
    end
    if not rows or not (ended or (mem.u8(to) == TURN and mem.u8(to + 1) == 0x80)) then
        return nil
    end
    return rows
end

local said, last, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
end)

reader.on_frame(function()
    local window = mem.u32(SHOWN)
    if not in_ram(window) then
        last, stood = nil, 0
        return
    end
    local rows = page_shown(window)
    if not rows then
        last, stood = nil, 0
        return
    end
    local key = {}
    for _, row in ipairs(rows) do
        for i = row.first, row.first + row.count - 1 do
            local c = cell(i)
            if not c then
                return
            end
            key[#key + 1] = c
        end
        key[#key + 1] = "|"
    end
    local now = table.concat(key)
    stood = now == last and stood + 1 or 0
    last = now
    if stood ~= STILL then
        return
    end
    local lines = {}
    for _, row in ipairs(rows) do
        local out, marks = {}, {}
        for i = row.first, row.first + row.count - 1 do
            local character, hash = dictionary:read(cell(i), CELL_W, CELL_H, 1)
            out[#out + 1] = character or "\u{FFFD}"
            marks[#marks + 1] = character or hash
        end
        if #out > 0 then
            dictionary:line(marks)
            lines[#lines + 1] = table.concat(out)
        end
    end
    local page = table.concat(lines, "\n")
    if page ~= "" and page ~= said then
        said = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end)
