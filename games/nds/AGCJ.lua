-- reader: title 逆転裁判4 (Gyakuten Saiban 4)
-- reader: reads a message window's page once the game has typed it out and waits
-- reader: version AGCJ
-- reader: build 75928841
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The game's script is in the card's file system, packed (LZ77) in sections; the section being played is
-- unpacked into main memory and stays there: 16-bit words, a character (0x90 and up: the table beside this
-- script) or a command (below 0x90) followed by the words it takes. Nothing copies a line anywhere. Two words
-- in main memory say where the script has got to (AT) and where its section's script begins (SECTION). As a
-- page types, AT walks through it; on a command that waits for a button it stands until the button comes.
-- The same engine as the first game's (AGB-ASBJ), with its commands below 0x90 instead of 0x80.
--
-- So a page is the characters from the last page's end up to AT, and it is said when AT stands on a wait, or
-- on the pause (0x0C n) before a page that goes on by itself, while the whole page is on screen.
-- Commands that matter here:
--   0x01        a new row
--   0x02, 0x2D  wait for a button, then a new page (after 0x2D the scene changes first)
--   0x2E        a page that goes on by itself: said once AT has passed it
--   0x0E n      who speaks: n's high byte is a number for the name on the window's tab (a picture)
--   0x03 n      a colour; 0x0C n a pause of n frames; 0x0B n the typing's speed
-- The others only have to be stepped over: ARGS has how many words each takes, as the first game's, and 0x4E's
-- one (found by stepping through the card's whole script: counted as none, it put a following command's
-- arguments among the characters on 55 pages). This game has more commands, whose counts aren't known: an
-- argument of one that falls among the characters' numbers stands alone between commands, and is left out.
-- The section is read from its start each time, so that the stepping stays in step with the commands.
--
-- Not covered: choices, the court record's texts, cross-examination; a page the player skips before it is
-- whole is still said whole (it is read from the script, not from the screen). Who speaks: the name on the
-- window's tab is a picture, so only the speaker's number is known, and it is given as it is
-- (meta.speaker_number).

local AT, SECTION = 0x020EC12C, 0x020EC130 -- words: where the script has got to, where its section begins
local LOW, HIGH = 0x02000000, 0x02400000
local WAIT, WAIT_ON, AUTO, ROW, SPEAKER, PAUSE = 0x02, 0x2D, 0x2E, 0x01, 0x0E, 0x0C
local CHARS = 0x90
local SOFT = { [0x01] = true, [0x03] = true, [0x0B] = true, [0x0C] = true }
local MOST = 0x8000 -- bytes of a section read at most
local ARGS = { [0x03] = 1, [0x04] = 1, [0x05] = 2, [0x06] = 1, [0x08] = 2, [0x09] = 3, [0x0A] = 1, [0x0B] = 1,
               [0x0C] = 1, [0x0E] = 1, [0x0F] = 2, [0x10] = 1, [0x12] = 3, [0x13] = 1, [0x17] = 1, [0x18] = 1,
               [0x19] = 2, [0x1A] = 4, [0x1B] = 1, [0x1C] = 1, [0x1D] = 1, [0x1E] = 3, [0x20] = 1, [0x22] = 2,
               [0x23] = 2, [0x25] = 1, [0x26] = 1, [0x27] = 2, [0x28] = 1, [0x29] = 1, [0x2A] = 3, [0x2C] = 1,
               [0x2F] = 2, [0x30] = 1, [0x31] = 2, [0x32] = 2, [0x33] = 5, [0x34] = 1, [0x35] = 2,
               [0x4E] = 1 }

-- The build: the card's header, which the console keeps a copy of at the top of main memory.
if mem.read(0x023FFE00, 16) ~= "GYAKUTEN4 JPAGCJ" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("AGCJ.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

--- What stands for a code the table hasn't got.
local function unknown(code)
    return "\u{FFFD}"
end

--- The script from `from` up to `to` as a list of { address, word, first argument, its two bytes }: commands
--- with their arguments stepped over. Nil when the words can't be read.
local function words(from, to)
    local bytes = mem.read(from, to - from)
    if not bytes or #bytes < to - from then
        return nil
    end
    local out, i = {}, 1
    while i + 1 <= #bytes do
        local word = bytes:byte(i) + 256 * bytes:byte(i + 1)
        local takes = word < CHARS and ARGS[word] or 0
        local first = takes > 0 and i + 3 <= #bytes and bytes:byte(i + 2) + 256 * bytes:byte(i + 3) or nil
        out[#out + 1] = { from + i - 1, word, first, bytes:sub(i, i + 1) }
        i = i + 2 + 2 * takes
    end
    return out
end

local said, before, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, before, stood = nil, nil, 0
end)

reader.on_frame(function()
    local at, section = mem.u32(AT), mem.u32(SECTION)
    if not at or not section or section < LOW or at < section or at >= HIGH or at - section > MOST then
        before = nil
        return
    end
    stood = at == before and stood + 1 or 0
    before = at
    local list = words(section, at + 2)
    if not list or #list == 0 or list[#list][1] ~= at then
        return -- the stepping doesn't fall on AT: a command's arguments counted wrong before it
    end
    -- The page that ends here: AT stands on a wait; or on the pause before a page goes on by itself (the page
    -- is whole on screen while it lasts), which ends at that 0x2E; or has just passed one.
    local last = #list
    local pausing = list[last][2] == PAUSE and stood >= 2 and mem.u16(at + 4) == AUTO
    if pausing then
        list[last + 1] = { at + 4, AUTO }
        last = last + 1
    elseif (list[last][2] ~= WAIT and list[last][2] ~= WAIT_ON) or stood < 2 then
        last = nil
        for i = #list - 1, math.max(1, #list - 12), -1 do
            if list[i][2] == AUTO then
                last = i
                break
            elseif list[i][2] >= CHARS or list[i][2] == WAIT or list[i][2] == WAIT_ON then
                break
            end
        end
        if not last then
            return
        end
    end
    local ends = list[last][1]
    if said == ends then
        return
    end
    local first, speaker = 1, nil
    for i = last - 1, 1, -1 do
        if list[i][2] == WAIT or list[i][2] == WAIT_ON or list[i][2] == AUTO then
            first = i + 1
            break
        end
    end
    for i = last - 1, 1, -1 do -- who speaks is set before the page, and stays until it is set again
        if list[i][2] == SPEAKER then
            speaker = list[i][3] and list[i][3] >> 8
            break
        end
    end
    -- A character is kept when the nearest word before it or after it, past the soft commands (a row, a pause,
    -- the speed, a colour), is a character too: a scene's commands before a page take arguments this script
    -- doesn't count, and one of those that falls among the characters' numbers stands alone between commands.
    local function char_at(i)
        return i >= first and i <= last - 1 and list[i][2] >= CHARS
    end
    local function beside(i, step)
        local j = i + step
        while j >= first and j <= last - 1 and SOFT[list[j][2]] do
            j = j + step
        end
        return char_at(j)
    end
    local out = {}
    for i = first, last - 1 do
        local word = list[i][2]
        if word >= CHARS then
            if beside(i, -1) or beside(i, 1) then
                out[#out + 1] = characters[list[i][4]] or unknown(list[i][4])
            end
        elseif word == ROW then
            out[#out + 1] = "\n"
        end
    end
    -- Without the blanks and empty rows a page begins and ends with.
    while out[1] == "\u{3000}" or out[1] == "\n" do
        table.remove(out, 1)
    end
    while out[#out] == "\u{3000}" or out[#out] == "\n" do
        out[#out] = nil
    end
    local shown = table.concat(out)
    said = ends
    if shown:find("%S") then
        reader.emit{ text = shown, channel = "dialogue", meta = speaker and { speaker_number = speaker } or nil,
                     id = text.hash(string.format("%08X", ends) .. shown) }
    end
end)
