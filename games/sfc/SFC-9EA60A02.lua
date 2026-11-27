-- reader: title ファイナルファンタジーV (Final Fantasy V, Super Famicom)
-- reader: reads the message window's page from the game's own page buffer once it stands
-- reader: version SFC-9EA60A02
-- reader: build 9ea60a02
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The script lies in the cartridge with its own codes (a kanji is 0x1E or 0x1F and a byte, some codes are words of
-- a dictionary, others insert a character's name or indent a row). The game unpacks a page whole into PAGE in work
-- RAM, laid out as the window shows it: ROWS rows of ACROSS cells, a byte a cell, the dictionary's words and the
-- names written out, a kanji as its second byte only, with a byte for each cell at LEADS saying which it was (1 for
-- 0x1E, 2 for 0x1F, 0 for any other code). 0xFF is a blank cell; a row's text ends at 0x01 or 0x00 (the rest of
-- the row is filler), and 0x17 ends the page's text (a wait follows). 0x0C is a pause, shown as a gap. The table
-- beside this script gives the kana and signs by code (Final Fantasy VI's own codes for them, held against this
-- game's pages, and those it hasn't got read in play) and the kanji as their two bytes, read in play.
--
-- The buffer keeps the last page after its window closes (and a state loaded keeps it too): WINDOW is 0 while no
-- message window is up, and a page is held to stand only while it isn't (5 while a window is up, 1 as it opens;
-- seen over every snap with a window and without one).
--
-- A page is said once PAGE and LEADS have stood the same for STILL frames with a window up; rows of nothing but
-- spaces are left out, and the spaces a row is indented by. Each page once, until another comes.
--
-- Not covered: menus, a fight's messages, the screens before the story. A code the table hasn't got comes out as
-- U+FFFD.

local PAGE, LEADS = 0x7E19D3, 0x7E1A13
local ROWS, ACROSS = 4, 16
local CELLS = ROWS * ACROSS
local BLANK, ROW_END, PAGE_END, CLOSE, PAUSE = 0xFF, 0x01, 0x00, 0x17, 0x0C
local LEAD_BYTES = { 0x1E, 0x1F }
local WINDOW = 0x7E0BB5
local STILL = 20

-- The build: the cartridge's header (its title), in its first bank.
if mem.read(0xC0FFC0, 21) ~= "FINAL FANTASY 5      " then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("SFC-9EA60A02.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

local function page_text(cells, leads)
    local kept = {}
    for r = 0, ROWS - 1 do
        local row, closed = {}, false
        for i = r * ACROSS + 1, (r + 1) * ACROSS do
            local b, lead = cells:byte(i), leads:byte(i)
            if lead == 0 and (b == ROW_END or b == PAGE_END or b == CLOSE) then
                closed = b == CLOSE
                break
            elseif lead == 0 and b == PAUSE then
                -- a pause: the blanks around it already make its gap
            elseif lead == 0 and b == BLANK then
                if #row > 0 and row[#row] ~= "\u{3000}" then
                    row[#row + 1] = "\u{3000}"
                end
            elseif lead ~= 0 then
                row[#row + 1] = characters[string.char(LEAD_BYTES[lead] or 0, b)] or "\u{FFFD}"
            else
                row[#row + 1] = characters[string.char(b)] or "\u{FFFD}"
            end
        end
        local line = table.concat(row):gsub("\u{3000}+$", "")
        if line ~= "" then
            kept[#kept + 1] = line
        end
        if closed then
            break
        end
    end
    return table.concat(kept, "\n")
end

local said, last, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
end)

reader.on_frame(function()
    local cells, leads = mem.read(PAGE, CELLS), mem.read(LEADS, CELLS)
    if not cells or #cells < CELLS or not leads or #leads < CELLS then
        return
    end
    if mem.u8(WINDOW) == 0 then
        -- No window up: what the buffer holds was said already, or belongs to no page on screen.
        last, stood = nil, 0
        return
    end
    local now = cells .. leads
    stood = now == last and stood + 1 or 0
    last = now
    if stood ~= STILL then
        return
    end
    local page = page_text(cells, leads)
    -- Before the story the buffer holds no page (zeros, which the table doesn't give): not a page.
    local unknown = select(2, page:gsub("\u{FFFD}", ""))
    local known = utf8.len((page:gsub("\u{FFFD}", ""):gsub("\u{3000}", ""):gsub("\n", ""))) or 0
    if page ~= "" and page ~= said and known > unknown then
        said = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end)
