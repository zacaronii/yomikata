-- reader: title 龍が如く2 (Ryuu ga Gotoku 2)
-- reader: reads the subtitles of scenes and what people say in play, with the speaker's name and a question's answers, each page as it is shown
-- reader: version SLPM-66602 (disc 1)
-- reader: build SLPM-66602-c6b95c48
-- reader: no hooks
-- reader: no layout where the rows are drawn isn't looked into

-- How the game keeps what it shows.
--
-- Text comes in blocks, each with a font of its own: only the characters its lines use, numbered in the
-- order they come up. So a code is another character in every block, and nothing in memory says which.
-- Two words point at the blocks in use: PLACE at the one of the place being played (what people say in
-- play, in a window under the speaker's name) and SCENE at the one a scene brings for its subtitles. Each
-- points at:
--   +0x10  the font: cells of 24 by 24 pixels, four bits a pixel (288 bytes; the left pixel of two in a
--          byte's low half). A cell holds two glyphs, one in bits 0-1 of each pixel and one in bits 2-3
--   +0x14  how many cells; +0x16 how many half-width places the font begins with (below)
--   +0x20  the text: a word that points at its list of messages, a word not read here, a word that points
--          at the speakers' names (8 codes each), and, where the names end, pages of one or more rows
-- A row is 12 bytes: its length in codes (a byte), which row of the page it is, the size it is drawn at
-- (two bytes), where its codes are, and a word that isn't read here. A code is 16 bits and counts halves of
-- a glyph: a full-width character has an even code, twice its glyph's number; a code below the count at
-- +0x16 is a half-width character (ASCII), the left or right half of a glyph. 0xFFFD is a wide space,
-- 0xFFFE a narrow one, and 0xFFFF starts a new line inside a row (a window's message is one row).
--
-- What is on screen: ROWS points at the first of the rows drawn now, or is zero. Each is a record that points
-- at the next (+0), at its row (+0x0C; for a speaker's name a row made up for it, whose codes are in the
-- block's names), and has the colour it is drawn in (+0x428), whose top byte goes from 0 to 0xFF as the row
-- fades in. A page is there whole, for its time or until a button is pressed. Which block a row is of, and
-- so which font, is told by where its codes lie.
--
-- A glyph is known by a hash of its picture, repacked as lib/glyphs.lua asks (two bits a pixel), and the
-- file of glyphs beside this script says which character each hash is: they were read off the pictures. One
-- that file hasn't got comes out as U+FFFD; it has those of what was played through so far.
--
-- A line spoken in Korean is shown in hangul, in grey, over its Japanese: both rows are read, as shown.
--
-- A question in play is a page like any other; its answers come up as rows in a colour of their own, the
-- one the cursor is on at full strength (+0x42B is 0xFF) and the others at half.
--
-- A place's name, shown in a corner as the place comes up, is such a row too, drawn twice (for its shadow):
-- it is said on the interface channel.
--
-- Not covered: who speaks in a scene (nothing shows it); menus, and whatever isn't drawn as such rows.

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
local PLACE = 0x0063C800 -- word: the text of the place played, as above
local SCENE = 0x0063C804 -- word: a scene's subtitles, likewise
local ROWS = 0x00B92444 -- word: the first record of the rows drawn now, or 0
local CELL = 288 -- bytes of a font's cell
local WIDE, NARROW, BREAK = 0xFFFD, 0xFFFE, 0xFFFF
local MOST = 0x100000 -- a block isn't longer

if mem.u32(0x0012A000) ~= 0x0C051D06 or mem.u32(0x0038E000) ~= 0x0C0E4358 then
    error("this script is for another build of the game", 0)
end

local dictionary = require("lib.glyphs").open(reader.data("glyphs"))

local function inside(address)
    return address and address >= 0x00100000 and address < 0x02000000
end

--- A glyph's picture out of its cell: `shift` says which of the cell's two it is, `from` and `bytes` which
--- of a row's 12 bytes (all, or a half). Two bits a pixel, the leftmost in a byte's high bits.
local function picture(cell, shift, from, bytes)
    local out = {}
    for row = 0, 23 do
        local at = row * 12 + from
        for i = at + 1, at + bytes, 2 do
            local a, b = cell:byte(i, i + 1)
            out[#out + 1] = string.char(((a >> shift) & 3) << 6 | ((a >> (shift + 4)) & 3) << 4
                | ((b >> shift) & 3) << 2 | ((b >> (shift + 4)) & 3))
        end
    end
    return table.concat(out)
end

--- The block that codes at `codes` are of: what PLACE or SCENE points at, and its text.
local function block_of(codes)
    local found, text
    for _, slot in ipairs({ PLACE, SCENE }) do
        local object = mem.u32(slot)
        local block = inside(object) and mem.u32(object + 0x20)
        if inside(block) and block <= codes and codes < block + MOST and (not text or block > text) then
            found, text = object, block
        end
    end
    return found, text
end

--- The character of one code in a block's font, and its picture's hash.
local function character(object, code)
    if code == WIDE then
        return "\u{3000}", false
    elseif code == NARROW then
        return " ", false
    elseif code == BREAK then
        return "\n", false
    end
    local font, cells, halves = mem.u32(object + 0x10), mem.u16(object + 0x14), mem.u16(object + 0x16)
    local cell = inside(font) and code // 4 < cells and mem.read(font + CELL * (code // 4), CELL)
    if not cell or #cell < CELL then
        return "\u{FFFD}", false
    end
    local shift = (code // 2 & 1) * 2
    local read, hash
    if code < halves then
        read, hash = dictionary:read(picture(cell, shift, (code & 1) * 6, 6), 12, 24, 2)
    else
        read, hash = dictionary:read(picture(cell, shift, 0, 12), 24, 24, 2)
    end
    return read or "\u{FFFD}", hash
end

--- Codes as text, in the font of the block they are of.
local function decode(object, bytes)
    local out, hashes = {}, {}
    for i = 1, #bytes, 2 do
        out[#out + 1], hashes[#hashes + 1] = character(object, string.unpack("<I2", bytes, i))
    end
    dictionary:line(hashes)
    return table.concat(out)
end

local NAME = 70 -- frames a window's first row may stand before the speaker's name comes
local WHITE = 0xFFFFFF -- the colour of a row of text; an answer's is another
local waiting, since -- the rows of the frame before, as their codes, and the frame they came up in
local asked = false -- whether a question's answers are up

--- A line without the spaces, narrow and wide, at its ends.
local function trimmed(line)
    line = line:gsub("^ +", ""):gsub(" +$", "")
    while line:sub(1, 3) == "\u{3000}" do
        line = line:sub(4):gsub("^ +", "")
    end
    while line:sub(-3) == "\u{3000}" do
        line = line:sub(1, -4):gsub(" +$", "")
    end
    return line
end

reader.on_state_loaded(function()
    reader.forget()
    waiting, asked = nil, false
end)

reader.on_frame(function()
    local record = mem.u32(ROWS)
    -- The rows drawn now: each one's block, where its codes are, and the codes. A row whose codes are
    -- among its block's names is the speaker's (a scene's rows have none). The answers to a question are
    -- rows of the place's in a colour of their own, the one the cursor is on drawn fully and the others
    -- half through.
    local rows, answers, key, named = {}, {}, {}, false
    for _ = 1, 12 do
        if not inside(record) then
            break
        end
        local row, colour = mem.u32(record + 0x0C), mem.u32(record + 0x428)
        local length, codes = inside(row) and mem.u8(row), inside(row) and mem.u32(row + 4)
        local alpha = colour and colour >> 24
        if alpha and alpha > 0 and length and length > 0 and inside(codes) then
            local object, block = block_of(codes)
            local bytes = object and mem.read(codes, 2 * length)
            if bytes and #bytes == 2 * length then
                local place = object == mem.u32(PLACE)
                local names, messages = mem.u32(block + 8), mem.u32(block)
                local pages = inside(messages) and mem.u32(messages + 4)
                local name = names and pages and codes >= names and codes < pages
                if name and alpha < 0xFF then -- the name is coming or going: the page isn't settled
                    return
                end
                local found = { object = object, codes = codes, bytes = bytes, name = name, alpha = alpha }
                if place and not name and colour & WHITE ~= WHITE then
                    answers[#answers + 1] = found
                else
                    rows[#rows + 1] = found
                    named = named or name or not place
                end
            end
        end
        record = mem.u32(record)
    end
    -- Top to bottom: the rows stand in their block in that order.
    local function above(a, b)
        return a.codes < b.codes
    end

    -- A caption (the place's name, in a corner as the place comes up) is drawn twice, for its shadow:
    -- rows that are there twice are taken out of the page and said apart.
    local times, kept, captions = {}, {}, {}
    for _, row in ipairs(rows) do
        times[row.codes] = (times[row.codes] or 0) + 1
    end
    key = {}
    for _, row in ipairs(rows) do
        if times[row.codes] == 1 then
            kept[#kept + 1] = row
            key[#key + 1] = row.codes .. row.bytes
        elseif times[row.codes] > 1 then
            times[row.codes] = 0
            captions[#captions + 1] = row
        end
    end
    rows = kept
    if #captions > 0 then
        table.sort(captions, above)
        local mark = {}
        for i, caption in ipairs(captions) do
            mark[i] = caption.codes .. caption.bytes
        end
        if reader.changed("caption", table.concat(mark)) then
            local lines = {}
            for i, caption in ipairs(captions) do
                lines[i] = trimmed(decode(caption.object, caption.bytes))
            end
            local shown = table.concat(lines, "\n")
            reader.emit{ text = shown, channel = "interface", id = text.hash("caption\n" .. shown) }
        end
    else
        reader.forget("caption")
    end

    if #answers >= 2 then
        table.sort(answers, above)
        local lines, on, mark = {}, -1, {}
        for i, answer in ipairs(answers) do
            lines[i] = trimmed(decode(answer.object, answer.bytes))
            mark[i] = answer.codes .. answer.bytes
            if answer.alpha == 0xFF then
                on = i - 1
            end
        end
        if reader.changed("answers", table.concat(mark)) then
            local shown = table.concat(lines, "\n")
            reader.emit{ text = shown, channel = "choice", id = text.hash("choice\n" .. shown) }
        end
        if reader.changed("cursor", on) then
            reader.cursor(on)
        end
        asked = true
    elseif asked then
        asked = false
        reader.forget("answers")
        reader.forget("cursor")
        reader.cursor(nil)
    end

    if #rows == 0 then
        reader.forget("page")
        waiting = nil
        return
    end
    -- A window's first row is there about a second before the speaker's name is: so a page is taken once
    -- it has stood for two frames, and one of the place's that has no name yet only after NAME frames.
    local now, frame = table.concat(key), reader.frame()
    if now ~= waiting then
        waiting, since = now, frame
        return
    end
    if not named and frame >= since and frame - since < NAME then
        return
    end
    if not reader.changed("page", now) then
        return
    end
    table.sort(rows, above)
    local lines, speaker = {}, nil
    for _, row in ipairs(rows) do
        local line = decode(row.object, row.bytes)
        if row.name then
            speaker = trimmed(line)
        else
            lines[#lines + 1] = line
        end
    end
    if #lines > 0 then
        local shown = table.concat(lines, "\n")
        reader.emit{ text = shown, speaker = speaker, channel = "dialogue",
                     id = text.hash((speaker or "") .. "\n" .. shown) }
    end
end)
