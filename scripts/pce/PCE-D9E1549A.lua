-- reader: title 桃太郎伝説II (Momotarou Densetsu II, PC Engine)
-- reader: reads the message window's rows off the screen's tile map once they have typed out and stand
-- reader: version PCE-D9E1549A
-- reader: build d9e1549a
-- reader: no hooks
-- reader: no layout the window is found on the tile map each frame, not given as a layout

-- How the game keeps what it shows.
--
-- The game draws its text into the background's tile map (the BAT at the start of video memory: a 16-bit word a
-- cell, the tile's number in its low 11 bits, the map 64 cells across), each character two cells tall: its top
-- half on one row and its bottom half on the next. The font's tiles lie in pages of 64 characters, each page
-- rows of 16 tops then 16 bottoms (a character's bottom tile 0x10 after its top): from 0x100 the signs, digits
-- and capitals in the order of ASCII from 0x20; from 0x180 hiragana and from 0x200 katakana, each in the order
-- of the half-width katakana codes (JIS X 0201) from 0xA0 (a space, 。「」、・を, the small kana, ー, あ to ん). A
-- kana with its voicing mark has its top half drawn from tiles of their own (0x0A0 on), its bottom half the
-- kana's own, so a character is known by its bottom half, and voiced where its top half isn't its own. From 0x0E0
-- a few characters of their own (a kanji, "…"), each read in play.
--
-- The message window is a frame of tiles (the party's status and the command menu have the same frame, their
-- text in other tiles: of the framed windows, the one with the most characters these rules give is read): its top row a corner (0x081), a run of 0x088 and a corner (0x082),
-- its sides 0x085 and 0x086, its bottom row beginning 0x083. Its text is on pairs of rows from the second row
-- below the top to the bottom. The window is looked for on the map each frame; its rows are said once they have
-- stood the same for STILL frames, rows of nothing but spaces left out, each page once until another comes.
--
-- It reads video memory (the region `vram`): where the host doesn't give it, it reads nothing.
--
-- Not covered: who speaks (the game doesn't show it but in the text), menus and other windows, fights' numbers.
-- A character the rules here don't give comes out as U+FFFD.

local MAP_ACROSS, MAP_ROWS = 64, 32
local TOP_LEFT, TOP, TOP_RIGHT, LEFT, BOTTOM_LEFT = 0x081, 0x088, 0x082, 0x085, 0x083
local MARKED_FIRST, MARKED_LAST = 0x0A0, 0x0DF     -- top halves of voiced kana
local BLANK = 0x080                                -- the window's own blank cell
-- Characters of their own from 0x0E0, a top tile over its bottom 0x10 after, read in play.
local OWN = { [0x0E3] = "両", [0x0E6] = "王", [0x0ED] = "…" }
local STILL = 20

-- The build: a routine the game copies into the console's RAM and keeps there (seen the same from the title
-- through the opening).
if mem.read(0x1F1370, 15) ~= "\x88\xC0\xFF\xD0\xF5\xEE\x6C\x33\xEE\x6F\x33\xCA\xD0\xEC\x60" then
    error("this script is for another build of the game", 0)
end

local vram = mem.region("vram")

local function chars(s)
    local out = {}
    for ch in s:gmatch(utf8.charpattern) do out[#out + 1] = ch end
    return out
end
-- The half-width katakana codes from 0xA0, as hiragana and as katakana; ゛ and ゜ at their codes' places.
local HIRAGANA = chars("　。「」、・をぁぃぅぇぉゃゅょっーあいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわん゛゜")
local KATAKANA = chars("　。「」、・ヲァィゥェォャュョッーアイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワン゛゜")
local function pairs_of(from, to)
    local out, a, b = {}, chars(from), chars(to)
    for k = 1, #a do out[a[k]] = b[k] end
    return out
end
local VOICED = pairs_of("かきくけこさしすせそたちつてとはひふへほカキクケコサシスセソタチツテトハヒフヘホウ",
                        "がぎぐげござじずぜぞだぢづでどばびぶべぼガギグゲゴザジズゼゾダヂヅデドバビブベボヴ")

local function cell(row, col)
    row, col = row % MAP_ROWS, col % MAP_ACROSS
    local word = vram.u16(2 * (row * MAP_ACROSS + col))
    return word and word & 0x7FF or nil
end

--- The character a cell pair shows: the bottom half says which, the top half whether it is voiced.
local function character(top, bottom)
    if top == BLANK and bottom == BLANK then
        return "　"
    end
    if OWN[top] and bottom == top + 0x10 then
        return OWN[top]
    end
    for _, page in ipairs({ { 0x100, nil }, { 0x180, HIRAGANA }, { 0x200, KATAKANA } }) do
        local base, set = page[1], page[2]
        if bottom >= base and bottom < base + 0x80 and (bottom - base) & 0x10 ~= 0 then
            local index = ((bottom - base) >> 5) * 16 + (bottom & 0x0F)
            local ch
            if set then
                ch = set[index + 1]
            else
                local code = 0x20 + index
                ch = code == 0x20 and "　" or utf8.char(0xFF00 + code - 0x20)
            end
            if top ~= bottom - 0x10 then
                if top >= MARKED_FIRST and top <= MARKED_LAST then
                    ch = VOICED[ch] or "\u{FFFD}"
                else
                    ch = "\u{FFFD}"
                end
            end
            return ch
        end
    end
    return "\u{FFFD}"
end

local logged = {}   -- cells logged once, unknown to these rules

--- The framed windows on the map: each { top, left, right, bottom }.
local function find_windows()
    local found = {}
    for row = 0, MAP_ROWS - 1 do
        for col = 0, MAP_ACROSS - 2 do
            if cell(row, col) == TOP_LEFT and cell(row, col + 1) == TOP then
                local right = col + 1
                while right < col + MAP_ACROSS and cell(row, right) == TOP do
                    right = right + 1
                end
                if cell(row, right) == TOP_RIGHT then
                    for down = 3, MAP_ROWS - 1 do
                        local c = cell(row + down, col)
                        if c == BOTTOM_LEFT then
                            found[#found + 1] = { row, col, right, row + down }
                            break
                        elseif c ~= LEFT then
                            break
                        end
                    end
                end
            end
        end
    end
    return found
end

--- A window's rows as text, and how many of its characters are known and unknown.
local function text_of(w, log)
    local top, left, right, bottom = w[1], w[2], w[3], w[4]
    local rows, known, unknown = {}, 0, 0
    for row = top + 2, bottom - 2, 2 do
        local line = {}
        for col = left + 1, right - 1 do
            local t, b = cell(row, col), cell(row + 1, col)
            local ch = (t and b) and character(t, b) or "\u{FFFD}"
            if ch == "\u{FFFD}" then
                unknown = unknown + 1
                local mark = string.format("%03X/%03X", t or 0, b or 0)
                if log and t and b and not logged[mark] then
                    logged[mark] = true
                    reader.warn("unknown cell " .. mark .. " (top/bottom tile)")
                end
            elseif ch ~= "\u{3000}" then
                known = known + 1
            end
            line[#line + 1] = ch
        end
        while #line > 0 and line[#line] == "\u{3000}" do
            line[#line] = nil
        end
        rows[#rows + 1] = table.concat(line)
    end
    while #rows > 0 and rows[#rows] == "" do
        rows[#rows] = nil
    end
    return table.concat(rows, "\n"), known, unknown
end

--- The message window's text: of the framed windows, the one with the most characters these rules give, if
--- they outnumber those they don't (the party's status and the command menu are framed the same way, in other
--- tiles).
local function window_text()
    local best, most = nil, 0
    for _, w in ipairs(find_windows()) do
        local _, known, unknown = text_of(w, false)
        if known > unknown and known > most then
            best, most = w, known
        end
    end
    return best and (text_of(best, true))     -- the cells it doesn't know logged once
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
    local page = window_text()
    stood = page == last and stood + 1 or 0
    last = page
    if not page or page == "" or stood ~= STILL or page == said then
        return
    end
    said = page
    reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
end)
