-- reader: title 真・女神転生if... (Shin Megami Tensei if..., Super Famicom)
-- reader: reads the message window's rows off its pixels once the page stands, each glyph by its picture
-- reader: version SHVC-AMXJ
-- reader: build 91ae6c0b
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- Unlike Shin Megami Tensei II (games/sfc/SFC-819B137D.lua), this game keeps no number of a character it draws:
-- the script lies packed in the cartridge, and each character is drawn straight into the message window's
-- picture in work RAM, WINDOW on: 24 tiles across (192 pixels), tiles of 2 bits a pixel (as the Super Famicom
-- keeps them), the window's colour 1 and the text's 2 and 3. A row of text is 16 pixels, its glyphs the top 12,
-- three rows to a page. The font is proportional (each glyph as wide as its ink), and a kana's voicing mark is a
-- glyph of its own drawn over it (゛ on the kana's last column, ゜ a little after it): where the mark stands apart
-- from its kana, the two are joined into one character.
--
-- So the rows are read off the picture (lib/cells.lua): from each glyph's first column with ink, the widest
-- picture the dictionary beside this script has. The dictionary is the game's own font, out of the cartridge
-- (`yomikata rom-font --glyphs`), with the characters of tables/sfc/SHVC-AMXJ.tbl: digits, letters and kana by the
-- font's order (Shin Megami Tensei's: kana in the order of the fifty sounds), the voiced ones with their mark, and
-- signs and kanji read in play. A glyph it hasn't got comes out as U+FFFD and is logged for `yomikata unread`. A gap of SPACE
-- blank columns or more is a space.
--
-- A page is said once the window's picture has stood the same for STILL frames, with some text in it; each page
-- once, until another comes. The prompt's arrow is drawn into the window's last tile column, at the first row's
-- end, and blinks: that column is left out, of the text and of what has to stand.
--
-- Not covered: who speaks but where the game writes it into the text, menus and choices (drawn elsewhere), the
-- name entry, fights' messages, the screens before the story.

local WINDOW, ACROSS, TILE_ROWS = 0x7E2900, 24, 6
local TEXT = 23 * 8   -- the text's columns: the last tile column holds the prompt's arrow, which blinks
local ROWS, ROW_HEIGHT, GLYPH_HEIGHT = 3, 16, 12
local STILL = 20
local SPACE = 6

-- The build: the cartridge's own header, in its first bank (its title's start, and its checksum with its
-- complement). A host that gives a script no cartridge can't run this script.
if mem.read(0x00FFC0, 8) ~= "DIGITAL " or mem.read(0x00FFDC, 4) ~= "\xDA\x61\x25\x9E" then
    error("this script is for another build of the game", 0)
end

local cells = require("lib.cells")
local glyphs = require("lib.glyphs")
local source = reader.data("glyphs")
if not source then
    error("SHVC-AMXJ.glyphs is missing: the pictures of the game's font", 0)
end
local dictionary = glyphs.open(source)
local widths = cells.widths(source)

local INK = 2   -- the text's values, 2 and 3: the second plane

--- A row's text from its cells: a space for each SPACE blank columns between two.
local function pairs_of(from, to)
    local out, a, b = {}, {}, {}
    for ch in from:gmatch(utf8.charpattern) do a[#a + 1] = ch end
    for ch in to:gmatch(utf8.charpattern) do b[#b + 1] = ch end
    for k = 1, #a do out[a[k]] = b[k] end
    return out
end
local MARKED = {
    ["゛"] = pairs_of("かきくけこさしすせそたちつてとはひふへほカキクケコサシスセソタチツテトハヒフヘホウ",
                      "がぎぐげござじずぜぞだぢづでどばびぶべぼガギグゲゴザジズゼゾダヂヅデドバビブベボヴ"),
    ["゜"] = pairs_of("はひふへほハヒフヘホ", "ぱぴぷぺぽパピプペポ"),
}

local function row_text(found)
    local out, marks, last_end = {}, {}, nil
    for _, c in ipairs(found) do
        local joined = MARKED[c.character or ""]
        joined = joined and last_end and c.x - last_end < SPACE and joined[out[#out]]
        if joined then
            -- A voicing mark drawn apart from its kana (゜ always is): the two are one character.
            out[#out], marks[#marks] = joined, joined
        else
            if last_end and c.x - last_end >= SPACE then
                out[#out + 1] = string.rep("\u{3000}", (c.x - last_end) // SPACE)
                marks[#marks + 1] = false
            end
            out[#out + 1] = c.character or "\u{FFFD}"
            marks[#marks + 1] = c.character or c.hash
        end
        last_end = c.x + c.w
    end
    dictionary:line(marks)     -- the row, logged once where a picture in it has no reading (for yomikata unread)
    return table.concat(out)
end

local function page_text(bytes)
    local pic = cells.picture(bytes, "snes2", ACROSS)
    local lines = {}
    for row = 0, ROWS - 1 do
        local _, found = cells.proportional(pic, row * ROW_HEIGHT, GLYPH_HEIGHT, 0, TEXT, dictionary, widths,
                                            nil, INK)
        lines[#lines + 1] = row_text(found)
    end
    while #lines > 0 and lines[#lines] == "" do
        lines[#lines] = nil
    end
    return table.concat(lines, "\n")
end

local said, last, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
end)

reader.on_frame(function()
    local bytes = mem.read(WINDOW, ACROSS * TILE_ROWS * 16)
    if not bytes or #bytes < ACROSS * TILE_ROWS * 16 then
        return
    end
    local text_part = {}
    for row = 0, TILE_ROWS - 1 do      -- each tile row but its last tile, the arrow's
        text_part[#text_part + 1] = bytes:sub(row * ACROSS * 16 + 1, row * ACROSS * 16 + (ACROSS - 1) * 16)
    end
    local now = table.concat(text_part)
    stood = now == last and stood + 1 or 0
    last = now
    if stood ~= STILL then
        return
    end
    local page = page_text(bytes)
    if page ~= "" and page ~= said then
        said = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end)
