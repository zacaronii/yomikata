-- reader: title ポケットモンスター 緑 (Pocket Monsters Midori, Game Boy)
-- reader: reads the message window's two rows once they have typed out and stand
-- reader: version GB-923D46DD
-- reader: build 923d46dd
-- reader: no hooks
-- reader: no layout the window's place is fixed (the screen's last six rows) and not given as a layout

-- How the game keeps what it shows. As in Pocket Monsters Aka (games/gb/GB-29D07340.lua), whose script this is,
-- but for this cartridge's header.
--
-- The game keeps a copy of the screen's tiles in work RAM, 20 a row from SCREEN, and draws its message window
-- there: a frame on rows 12 to 17, the text on rows 14 and 16, columns 1 to 17 (column 18 of row 16 holds the
-- prompt's arrow, which blinks). The codes are the game's own, a byte each (the table beside this script, read
-- off the game's font in the cartridge). A kana's voicing marks are drawn over it, on the row above (13 or 15):
-- 0xE5 is ゛ and 0xE4 ゜. Katakana ヘ and リ share hiragana へ and り's glyphs and codes: one among katakana is
-- given as katakana.
--
-- The copy keeps a window's rows after the game has stopped showing it, so the rows count only while the
-- window shows: the game draws it as the LCD's window layer, on (LCDC, 0xFF40, bits 7 and 5) and on the screen
-- (WY, 0xFF4A, below 0x90; the game moves it to 0x90 to hide it). While it shows, its two rows are said when
-- the prompt's arrow (PROMPT, at row 16, column 18) shows that the page waits for the button, or, for a page
-- that ends without it, once they have stood the same for STILL frames; and when it is hidden, the rows it
-- showed are said if they had stood a moment (typed out; a character types every 3 frames). Each pair is said
-- once. A row scrolling up stands a moment with no arrow, shorter than STILL, and isn't said.
--
-- Not covered: who speaks (the game doesn't show it), menus and other boxes (a choice, the start menu), the
-- screens before the story, and battles' text has not been looked at.

local SCREEN = 0xC3A0 -- the copy of the screen's tiles, 20 a row
local WIDTH = 20
local TOP, BOTTOM = 12, 17 -- the window's frame
local ROWS = { 14, 16 } -- its text, each with its marks on the row above
local FIRST, LAST = 1, 17 -- the text's columns
local CORNER_TOP, CORNER_BOTTOM = 0x79, 0x7D -- the frame's left corners
local DAKUTEN, HANDAKUTEN = 0xE5, 0xE4
local STILL = 30 -- frames rows with no arrow stand before they are said (a row scrolling up stands about 26)
local PROMPT, ARROW = 0xC3A0 + 20 * 16 + 18, 0xEE -- the arrow that says a page waits
local LCDC, WY = 0xFF40, 0xFF4A -- the LCD's control and the window's row: whether the window shows
local TYPED = 4 -- frames rows stand before a window hidden is taken to have shown them whole

-- The build: the cartridge's header, from its title to its checksum.
if mem.read(0x0134, 26) ~= "POKEMON GREEN\0\0\0" .. "01\x03\x03\x04\x03\x00\x33\x00\x9C" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("GB-923D46DD.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

local function pairs_of(from, to)
    local out = {}
    local a, b = {}, {}
    for ch in from:gmatch(utf8.charpattern) do a[#a + 1] = ch end
    for ch in to:gmatch(utf8.charpattern) do b[#b + 1] = ch end
    for k = 1, #a do out[a[k]] = b[k] end
    return out
end
local VOICED = pairs_of("かきくけこさしすせそたちつてとはひふへほカキクケコサシスセソタチツテトハヒフヘホウ",
                        "がぎぐげござじずぜぞだぢづでどばびぶべぼガギグゲゴザジズゼゾダヂヅデドバビブベボヴ")
local SEMI = pairs_of("はひふへほハヒフヘホ", "ぱぴぷぺぽパピプペポ")
local KATA = pairs_of("へべぺり", "ヘベペリ")

local function katakana(ch)
    return ch and ch >= "ァ" and ch <= "ヺ" or ch == "ー"
end

local function row_text(row)
    local cells = {}
    for col = FIRST, LAST do
        local code = mem.u8(SCREEN + WIDTH * row + col)
        local mark = mem.u8(SCREEN + WIDTH * (row - 1) + col)
        local ch = code and characters[string.char(code)] or nil
        if ch and mark == DAKUTEN then
            ch = VOICED[ch] or ch
        elseif ch and mark == HANDAKUTEN then
            ch = SEMI[ch] or ch
        end
        cells[#cells + 1] = ch or ""
    end
    -- へ and り (and their voiced forms) among katakana are katakana.
    for k, ch in ipairs(cells) do
        if KATA[ch] then
            local before, after
            for m = k - 1, 1, -1 do if cells[m] ~= "" and cells[m] ~= "　" then before = cells[m] break end end
            for m = k + 1, #cells do if cells[m] ~= "" and cells[m] ~= "　" then after = cells[m] break end end
            if katakana(before) or katakana(after) then
                cells[k] = KATA[ch]
            end
        end
    end
    -- Spaces at the row's end aren't text (a row of nothing but spaces is empty).
    while #cells > 0 and (cells[#cells] == "" or cells[#cells] == "　") do
        cells[#cells] = nil
    end
    return table.concat(cells)
end

local said, last, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
end)

local function say(page)
    said = page
    reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
end

local function shown()
    local control, row = mem.u8(LCDC), mem.u8(WY)
    if not control or not row then
        return true -- a host without the registers: as before them
    end
    return control & 0xA0 == 0xA0 and row < 0x90
end

reader.on_frame(function()
    if not shown() then
        -- Hidden now: the rows it showed last, if they were typed out and not said yet.
        if last and last ~= "" and last ~= said and stood >= TYPED then
            say(last)
        end
        last, stood = nil, 0
        return
    end
    if mem.u8(SCREEN + WIDTH * TOP) ~= CORNER_TOP or mem.u8(SCREEN + WIDTH * BOTTOM) ~= CORNER_BOTTOM then
        last, stood = nil, 0
        return
    end
    local rows = {}
    for _, row in ipairs(ROWS) do
        local line = row_text(row)
        if line ~= "" then
            rows[#rows + 1] = line
        end
    end
    local page = table.concat(rows, "\n")
    stood = page == last and stood + 1 or 0
    last = page
    local waits = mem.u8(PROMPT) == ARROW
    if page == "" or (stood < STILL and not waits) or page == said then
        return
    end
    say(page)
end)
