-- reader: title クロノ・クロス (Chrono Cross, PlayStation)
-- reader: reads the message window's rows off the sprites that draw them once the page waits, each by its picture
-- reader: version SLPS-02364 (disc 1)
-- reader: build 26c3da2599af76f7e352b8594d048b527d6bd0d9
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The scene's script lies in memory as it is, in codes of the game's own; a message has slots the game fills as
-- it types (the hero's name, a speaker's own endings). Each window (WINDOWS of them, WINDOW bytes apart) draws
-- each character it has typed as a sprite of its own: a list of them at SPRITES, SPRITE_BYTES each, a textured
-- rectangle of 11 by 11 (its colour, its place on screen, its place in the texture and its palette); one not shown
-- has its colour 0. COUNT holds how many of the list are the page's (the rest are left from before). The
-- speaker's name is the first row, drawn the same way.
--
-- The page's characters are drawn into a texture in video memory (from TEXTURE, 4 bits a texel) four to a place:
-- each in a bit of its own, and the sprite's palette (one of four from PALETTE) shows just that bit. So a sprite's
-- picture is that bit of its 11 by 11 texels, and each is known by its picture in the dictionary beside this
-- script (a hash to a character), read in play. A picture with no ink is a space. A picture it hasn't got comes
-- out as U+FFFD and is logged for `yomikata unread`.
--
-- READER is where the game reads the script next; the word at READER + 8 is where it goes back to in the message
-- after a slot's string, far from READER while it is in one. The page waits for the button where READER stands,
-- out of a slot, on 0x02 (or 0x00, the message's end). The rows are the sprites shown by their y, each by x.
-- A window's page is said once it waits and its sprites and their pictures have stood the same for STILL frames;
-- each page once, until another comes in that window. It reads video memory (the region `vram`): where the host
-- doesn't give it, it reads nothing.
--
-- Not covered: choices, menus, fights' messages. Spaces at a row's ends are left out.

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
local SPRITES, SPRITE_BYTES, MOST = 0x80171C48, 20, 176
local WINDOWS, WINDOW = 4, 0xC008
local SIZE = 11
local TEXTURE, PALETTE = 640, 0x3E38                   -- the texture's first half-word in a line of video memory
local READER, FAR = 0x80171B8C - SPRITES, 0x10000   -- from the sprites' list
local COUNT = READER - 0x0C
local WAIT, ENDS = 0x02, 0x00
local STILL = 6

-- Three calls in the game's code.
if mem.u32(0x800181A8) ~= 0x0C005301 or mem.u32(0x800223FC) ~= 0x0C0088A2 or mem.u32(0x8002FE94) ~= 0x0C00403D then
    error("this script is for another build of the game", 0)
end

local glyphs = require("lib.glyphs")
local source = reader.data("glyphs")
if not source then
    error("SLPS-02364.glyphs is missing: the pictures of the game's characters", 0)
end
local dictionary = glyphs.open(source)
local vram = mem.region("vram")
local BLANK = string.rep("\0", 2 * SIZE)

--- The sprites shown: { x, y, u, v, bit } of each, by y and then x.
local function shown(list, count)
    local out = {}
    for i = 0, math.min(count, MOST) - 1 do
        local at = SPRITE_BYTES * i
        local word = string.unpack("<I4", list, at + 1)
        if word >> 24 == 0x64 and word & 0xFFFFFF ~= 0 then
            local xy, uv, wh = string.unpack("<I4I4I4", list, at + 5)
            local bit = (uv >> 16) - PALETTE
            if wh == (SIZE << 16 | SIZE) and bit >= 0 and bit < 4 then
                out[#out + 1] = { x = xy & 0xFFFF, y = xy >> 16, u = uv & 0xFF, v = (uv >> 8) & 0xFF, bit = bit }
            end
        end
    end
    table.sort(out, function(a, b) return a.y < b.y or (a.y == b.y and a.x < b.x) end)
    return out
end

--- A sprite's picture as lib/glyphs.lua hashes one: 11 rows of 2 bytes, a bit a pixel, the leftmost in the high
--- bit (a texel's 4 bits keep the leftmost texel in a byte's low half).
local function picture(s)
    local rows = {}
    local first = TEXTURE + s.u // 4
    for r = 0, SIZE - 1 do
        local bytes = vram.read(((s.v + r) * 1024 + first) * 2, 8)
        if not bytes or #bytes < 8 then
            return nil
        end
        local bits = 0
        for k = 0, SIZE - 1 do
            local n = s.u % 4 + k
            local b = bytes:byte(n // 2 + 1)
            local texel = n % 2 == 0 and (b & 0x0F) or (b >> 4)
            bits = (bits << 1) | ((texel >> s.bit) & 1)
        end
        bits = bits << (16 - SIZE)
        rows[#rows + 1] = string.char(bits >> 8, bits & 0xFF)
    end
    return table.concat(rows)
end

--- Whether the page waits for the button: READER on 0x02 or 0x00, out of a slot.
local function waits(window)
    local at, back = mem.u32(window + READER), mem.u32(window + READER + 8)
    if not at or not back or at < 0x80000000 or math.abs(at - back) > FAR then
        return false
    end
    local next_ = mem.u8(at)
    return next_ == WAIT or next_ == ENDS
end

-- Each window's: what it said last, its sprites and pictures as they were, the frames they have stood.
local state = {}

local function forget()
    for w = 1, WINDOWS do
        state[w] = { said = nil, last = nil, stood = 0 }
    end
end
forget()

reader.on_state_loaded(function()
    reader.forget()
    forget()
end)

--- The page window `w` shows, once it has waited STILL frames; nil otherwise.
local function page_of(w)
    local st, window = state[w], SPRITES + WINDOW * (w - 1)
    local list = mem.read(window, SPRITE_BYTES * MOST)
    if not list or #list < SPRITE_BYTES * MOST then
        return nil
    end
    local sprites = shown(list, mem.u16(window + COUNT) or 0)
    if #sprites == 0 or not waits(window) then
        st.last, st.stood = nil, 0
        return nil
    end
    local pictures, key = {}, {}
    for i, s in ipairs(sprites) do
        pictures[i] = picture(s)
        if not pictures[i] then
            return nil
        end
        key[#key + 1] = string.format("%d,%d;", s.x, s.y) .. pictures[i]
    end
    local now = table.concat(key)
    st.stood = now == st.last and st.stood + 1 or 0
    st.last = now
    if st.stood ~= STILL then
        return nil
    end
    local lines, row, marks, y = {}, {}, {}, nil
    local function finish()
        while #row > 0 and row[#row] == "\u{3000}" do
            row[#row], marks[#marks] = nil, nil
        end
        local first = 1
        while first <= #row and row[first] == "\u{3000}" do
            first = first + 1
        end
        if first <= #row then
            dictionary:line({ table.unpack(marks, first) })
            lines[#lines + 1] = table.concat(row, "", first)
        end
        row, marks = {}, {}
    end
    for i, s in ipairs(sprites) do
        if y and s.y ~= y then
            finish()
        end
        y = s.y
        if pictures[i] == BLANK then
            row[#row + 1], marks[#marks + 1] = "\u{3000}", false
        else
            local character, hash = dictionary:read(pictures[i], SIZE, SIZE, 1)
            row[#row + 1] = character or "\u{FFFD}"
            marks[#marks + 1] = character or hash
        end
    end
    finish()
    return table.concat(lines, "\n")
end

reader.on_frame(function()
    if not vram then
        return
    end
    for w = 1, WINDOWS do
        local page = page_of(w)
        if page and page ~= "" and page ~= state[w].said then
            state[w].said = page
            reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
        end
    end
end)
