-- reader: title クロノ・クロス (Chrono Cross, PlayStation)
-- reader: reads a page of the message window as the game types it, its speakers' own words filled in
-- reader: version SLPS-02364 (disc 1)
-- reader: build 26c3da2599af76f7e352b8594d048b527d6bd0d9
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps its text.
--
-- The scene's script lies in memory as it is. A message has slots (0x12 and a number) that the game fills, as it
-- types, with words of the speaker's own (an ending, a name): strings elsewhere in memory, each ended by 0x00.
-- No page is put together anywhere: the game types it from the script, a character at a time, and a slot's
-- string in its place. READER is where it reads next. In a slot it points into the slot's string, and the word
-- at READER + 8 is where it goes back to in the message, just past the slot.
--
-- So this script follows the typing: each frame, what READER went over since the frame before is the page's
-- next characters, and in a slot the string it points into. The page is said when READER stands on its end,
-- 0x02 (the button) or 0x00 (the message's end), where it waits.
--
-- The codes, a byte each but for those from 0xF8 (two bytes): the table beside this script. 0x01 starts a row;
-- 0x04, 0x08, 0x10 and 0x18 take a byte after them (a pause, who speaks, the window); other codes below 0x20
-- are stepped over.
--
-- Not covered: who speaks (the window's name row: the message names the speaker by a number), choices, menus,
-- fights' messages. A character the table hasn't got comes out as U+FFFD.

local tbl = require("lib.tbl")

local READER = 0x80171B8C
local FAR = 0x10000                 -- READER this far from where it goes back to: it is in a slot's string
local MOST = 64                     -- bytes of a slot's string read at most
local ROW, WAIT, ENDS, SLOT = 0x01, 0x02, 0x00, 0x12
local WITH_BYTE = { [0x04] = true, [0x08] = true, [0x10] = true, [0x18] = true, [SLOT] = true }

-- Three calls in the game's code.
if mem.u32(0x800181A8) ~= 0x0C005301 or mem.u32(0x800223FC) ~= 0x0C0088A2 or mem.u32(0x8002FE94) ~= 0x0C00403D then
    error("this script is for another build of the game", 0)
end

local source = reader.data("tbl")
if not source then
    error("SLPS-02364.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

local page, was, slot_back = {}, nil, nil

--- The text of `bytes`, as far as a page's end: its characters, and whether it ended the page.
local function take(bytes)
    local i = 1
    while i <= #bytes do
        local b = bytes:byte(i)
        if b == WAIT or b == ENDS then
            return true
        elseif b == ROW then
            page[#page + 1] = "\n"
            i = i + 1
        elseif WITH_BYTE[b] then
            i = i + 2
        elseif b < 0x20 then
            i = i + 1
        elseif b >= 0xF8 then
            page[#page + 1] = characters[bytes:sub(i, i + 1)] or "\u{FFFD}"
            i = i + 2
        else
            page[#page + 1] = characters[string.char(b)] or "\u{FFFD}"
            i = i + 1
        end
    end
    return false
end

local function say()
    local text_ = table.concat(page):gsub("^[\n　]+", ""):gsub("[\n　]+$", ""):gsub("\n+", "\n")
    page = {}
    if text_:find("%S") then
        reader.emit{ text = text_, channel = "dialogue", id = text.hash(text_) }
    end
end

reader.on_state_loaded(function()
    reader.forget()
    page, was, slot_back = {}, nil, nil
end)

reader.on_frame(function()
    local at, back = mem.u32(READER), mem.u32(READER + 8)
    if not at or not back or at < 0x80000000 then
        return
    end
    local slot = math.abs(at - back) > FAR
    if slot then
        if back ~= slot_back then
            -- Into a slot (or from one straight into the next): the message up to it, then the slot's string.
            if was and back - 2 > was and back - was < 0x400 then
                if take(mem.read(was, back - 2 - was) or "") then say() end
            end
            -- READER may be past the string's first characters already: from the 0x00 before it.
            local before = mem.read(at - MOST, MOST) or ""
            local after = mem.read(at, MOST) or ""
            take((before:match("[^%z]*$") or "") .. (after:match("^[^%z]*") or ""))
            slot_back = back
        end
        was = back
        return
    end
    slot_back = nil
    if not was or at < was or at - was > 0x400 then
        was = at -- a new message, or one read from elsewhere: from here on
        return
    end
    if at > was then
        if take(mem.read(was, at - was) or "") then
            say()
        end
        was = at
    end
    -- READER stands on the page's end while the page waits for the button: the page is whole.
    local next_ = mem.u8(at)
    if (next_ == WAIT or next_ == ENDS) and #page > 0 then
        say()
    end
end)
