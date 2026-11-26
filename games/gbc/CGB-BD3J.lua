-- reader: title ドラゴンクエストIII そして伝説へ… (Dragon Quest III, Game Boy Color)
-- reader: reads a message window's text from the cartridge, a page for each wait
-- reader: version CGB-BD3J
-- reader: build b70423cf
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The game's text lies in the cartridge as it is and is drawn from there, a character a frame: nothing is
-- copied into work RAM. Two bytes there say where: BANK, the cartridge's bank, and AT, the address in it
-- (0x4000-0x7FFF, little-endian). When a page has typed, AT stands just past the code that ends it (it also
-- stands a moment at a row's end, which isn't a page's). So a page is the cartridge's bytes from the code that
-- ended the page before up to AT, said once AT has stood a moment just past an end or a wait.
-- A core shows a script only the banks the game has switched in now, so the cartridge is read whole where the
-- host gives it (0x01000000 on, docs/script-api.md).
--
-- The codes are the game's own, a byte each but for kanji (0xE5 and a byte: the table beside this script):
--   0x24-0x51    hiragana, あ to ん in the order of the fifty sounds (small ones by the table)
--   0x5B-0x88    katakana, ア to ン the same; 0xA7-0xBA が to ぼ, 0xBB-0xCE ガ to ボ
--   0xF7 and 0xFF the end of a message, 0xFA a wait, 0xF0 and 0xF1 a new row, 0xDB a space; 0xF9 n a name kept
--   in work RAM (0x40 the hero's, 0x00 the true name given in the opening), 0xFC n; the signs and kanji by the table
--
-- The yes/no box after a question is read off its tiles (lib/gbtiles.lua) through a dictionary of their pictures
-- beside this script, with its cursor; it is at one place on the screen. Needs the host's `vram` for that.
--
-- Not covered: who speaks, other menus,
-- fights, the names of companions (0xF9 with other numbers comes out as U+FFFD); a sign or kanji the table hasn't
-- got comes out as U+FFFD. At one place's entrance the game types pages before any window shows: they are said.

local BANK, AT = 0xC206, 0xC207
local CART = 0x01000000                 -- the cartridge, whole, where the host gives it
local NAMES = { [0x40] = { 0xC55A, 4 },  -- a name by its number: where it is, how long at most (it ends at 0 or 0xF0)
                [0x00] = { 0xC301, 10 } } -- the hero's (four characters); the true name given in the opening
local STILL = 4                         -- frames AT stands before a page is said
local MOST = 400                        -- bytes of a page looked back through
local END, END2, WAIT, ROW, ROW2, SPACE, NAME, KANJI = 0xF7, 0xFF, 0xFA, 0xF1, 0xF0, 0xDB, 0xF9, 0xE5
local ENDS = { [END] = true, [END2] = true, [WAIT] = true }
local ARGS = { [0xF9] = 1, [0xFC] = 1, [0xE5] = 1 }
local HIRAGANA = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
local VOICED = "がぎぐげござじずぜぞだぢづでどばびぶべぼ"

if mem.read(0x0134, 16) ~= "GBDQ3\0\0\0\0\0\0BD3J\xC0" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local glyphs = require("lib.glyphs")
local gbtiles = require("lib.gbtiles")
local source = reader.data("tbl")
if not source then
    error("CGB-BD3J.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars
local dictionary = glyphs.open(reader.data("glyphs"))

-- The yes/no box after a question: its words aren't in the text, the game draws them from tiles of their own.
-- A byte says it is up; it stands at one place of the tile map at 0x1C00, its answers on these rows, with the
-- cursor (a picture the dictionary calls ▶) before the one it is on.
local BOX_UP = 0xC219
local BOX_MAP, BOX_ROWS, BOX_LEFT, BOX_RIGHT = 0x1C00, { 7, 8, 9 }, 27, 30
-- The byte is up for other menus too (a name, a birthday): the box is told by its frame's corners standing
-- there, known by their pictures' hashes (the top left at 26,6, the bottom right at 31,10).
local CORNERS = { { 26, 6, "BEC2F11F82E16D12" }, { 31, 10, "0F55CEA9D92A4BBD" } }
local vram = mem.region("vram")

--- The box's answers as text (a row each) and the cursor's answer from 0; nil when they can't be read.
local function box()
    if not vram then
        return nil
    end
    for _, corner in ipairs(CORNERS) do
        local picture = gbtiles.cell(vram, BOX_MAP, corner[1], corner[2], true)
        if not picture or text.hash(picture):upper() ~= corner[3] then
            return nil
        end
    end
    local answers, cursor = {}, nil
    for _, row in ipairs(BOX_ROWS) do
        local cells = {}
        for x = BOX_LEFT, BOX_RIGHT do
            local picture, blank = gbtiles.cell(vram, BOX_MAP, x, row, true)
            if not picture then
                return nil
            end
            if not blank then
                local character = dictionary:read(picture, 8, 8, 2)
                if not character then
                    return nil -- a picture not known: the box is still being drawn, or holds a word not read yet
                elseif character == "▶" then
                    cursor = #answers
                else
                    cells[#cells + 1] = character
                end
            end
        end
        if #cells > 0 then
            answers[#answers + 1] = table.concat(cells)
        elseif cursor == #answers then
            cursor = nil -- a cursor on a row of nothing isn't on an answer
        end
    end
    return #answers > 0 and table.concat(answers, "\n") or nil, cursor
end

local function nth(list, n) -- the nth character (from 0) of a UTF-8 string, or nil
    local from = utf8.offset(list, n + 1)
    local to = from and utf8.offset(list, n + 2)
    return to and list:sub(from, to - 1) or nil
end

--- A one-byte code's character, by the order of the kana or by the table; nil for one neither has.
local function character(code)
    local ch = characters[string.char(code)]
    if ch then
        return ch
    elseif code >= 0x24 and code <= 0x51 then
        return nth(HIRAGANA, code - 0x24)
    elseif code >= 0x5B and code <= 0x88 then
        local h = nth(HIRAGANA, code - 0x5B)
        return h and utf8.char(utf8.codepoint(h) + 0x60)
    elseif code >= 0xA7 and code <= 0xBA then
        return nth(VOICED, code - 0xA7)
    elseif code >= 0xBB and code <= 0xCE then
        local h = nth(VOICED, code - 0xBB)
        return h and utf8.char(utf8.codepoint(h) + 0x60)
    end
    return nil
end

local function name(bytes)
    local out = {}
    for i = 1, #bytes do
        local b = bytes:byte(i)
        if b == 0 or b == 0xF0 then
            break
        end
        out[#out + 1] = character(b) or "\u{FFFD}"
    end
    return table.concat(out)
end

--- The text of the codes in `bytes`: commands stepped over, rows kept.
local function decode(bytes)
    local out, i = {}, 1
    while i <= #bytes do
        local b = bytes:byte(i)
        if b == KANJI and i < #bytes then
            out[#out + 1] = characters[bytes:sub(i, i + 1)] or "\u{FFFD}"
        elseif b == NAME and i < #bytes then
            local where = NAMES[bytes:byte(i + 1)]
            out[#out + 1] = where and name(mem.read(where[1], where[2]) or "") or "\u{FFFD}"
        elseif b == ROW or b == ROW2 then
            if #out > 0 then
                out[#out + 1] = "\n"
            end
        elseif b == SPACE then
            out[#out + 1] = "\u{3000}"
        elseif b < 0xE0 then
            out[#out + 1] = character(b) or "\u{FFFD}"
        end
        i = i + 1 + (ARGS[b] or 0)
    end
    local shown = table.concat(out):gsub("\n+$", "")
    return shown
end

local before, stood, said, warned, offered = nil, 0, nil, false, nil

reader.on_state_loaded(function()
    reader.forget()
    before, stood, said, offered = nil, 0, nil, nil
end)

reader.on_frame(function()
    if mem.u8(BOX_UP) == 1 then
        local answers, cursor = box()
        if answers and answers ~= offered then
            offered = answers
            reader.emit{ text = answers, channel = "choice", id = text.hash("box" .. answers) }
        end
        reader.cursor(answers and cursor or nil)
    elseif offered then
        offered = nil
        reader.cursor(nil)
    end
    local bank, at = mem.u8(BANK), mem.u16(AT)
    if not bank or not at or at < 0x4000 or at > 0x7FFF or bank == 0 then
        return
    end
    local place = bank * 0x4000 + (at - 0x4000)
    stood = place == before and stood + 1 or 0
    before = place
    if stood ~= STILL or said == place then
        return
    end
    local back = math.min(MOST, place)
    local bytes = mem.read(CART + place - back, back)
    if not bytes or #bytes < back then
        if not warned then
            reader.log("the cartridge can't be read here (it is wanted at 0x01000000): nothing is read")
            warned = true
        end
        return
    end
    -- From AT back over the codes that end this page, then back to the code that ended the page before. The
    -- look back stops on any byte that could be the second of a kanji or an argument: a page starts after an
    -- end or a wait.
    local last = #bytes
    if not ENDS[bytes:byte(last)] then
        return -- AT stands at a row's end or a pause: the page isn't whole
    end
    while last > 0 and ENDS[bytes:byte(last)] do
        last = last - 1
    end
    local first = last
    while first > 1 and not ENDS[bytes:byte(first - 1)] do
        first = first - 1
    end
    if first <= 1 then
        return -- no page's start within reach
    end
    local shown = decode(bytes:sub(first, last))
    said = place
    if shown:find("%S") then
        reader.emit{ text = shown, channel = "dialogue", id = text.hash(string.format("%X", place) .. shown) }
    end
end)
