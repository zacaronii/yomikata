-- reader: title 逆転裁判 (Gyakuten Saiban)
-- reader: reads a message window's page once the game has typed it out and waits
-- reader: version AGB-ASBJ
-- reader: build 0cb3d5b2
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- A scene's script is unpacked into the 256 KB on the board and stays there: 16-bit words, a character
-- (0x80 and up, the game's own numbers: the table beside this script says which) or a command (below 0x80)
-- followed by as many words as that command takes. Nothing copies a line anywhere. The 32 KB in the CPU has
-- the place the script has got to (AT) and where the section it is in begins (SECTION). As a page types, AT
-- walks through it; on the command that waits for a button (0x02) it stands until the button comes.
--
-- So a page is the characters from the last page's end (or the section's start) up to AT, and it is said when
-- AT stands on a wait. Commands that matter here:
--   0x01        a new row
--   0x02, 0x2D  wait for a button, then a new page (after 0x2D the scene changes first)
--   0x2E        a page that goes on by itself: said once AT has passed it
--   0x0E n      who speaks: n's high byte is a number for the name on the window's tab (a picture)
--   0x03 n      a colour; 0x0C n a pause of n frames; 0x0B n the typing's speed
-- The other commands only have to be stepped over: ARGS has how many words each takes.
--
-- Not covered: choices and the court record's texts; a page the player skips before it is whole is still
-- said whole (it is read from the script, not from the screen). Who speaks: the name on the window's tab is a
-- picture, so only the speaker's number is known, and it is given as it is (meta.speaker_number).

local AT, SECTION = 0x03003A74, 0x03003A78 -- words: where the script has got to, where its section begins
local LOW, HIGH = 0x02000000, 0x02040000
local WAIT, WAIT_ON, AUTO, ROW, SPEAKER = 0x02, 0x2D, 0x2E, 0x01, 0x0E
local MOST = 0x800 -- bytes of a section looked back through
local ARGS = { [0x03] = 1, [0x04] = 1, [0x05] = 2, [0x06] = 1, [0x08] = 2, [0x09] = 3, [0x0A] = 1, [0x0B] = 1,
               [0x0C] = 1, [0x0E] = 1, [0x0F] = 2, [0x10] = 1, [0x12] = 3, [0x13] = 1, [0x17] = 1, [0x18] = 1,
               [0x19] = 2, [0x1A] = 4, [0x1B] = 1, [0x1C] = 1, [0x1D] = 1, [0x1E] = 3, [0x20] = 1, [0x22] = 2,
               [0x23] = 2, [0x25] = 1, [0x26] = 1, [0x27] = 2, [0x28] = 1, [0x29] = 1, [0x2A] = 3, [0x2C] = 1,
               [0x2F] = 2, [0x30] = 1, [0x31] = 2, [0x32] = 2, [0x33] = 5, [0x34] = 1, [0x35] = 2 }

-- The build: a table of four of the program's own addresses that the game keeps in the CPU's memory from
-- its title on. And the cartridge's header (its title and code), where a host gives the cartridge to read.
local header = mem.read(0x080000A0, 16)
if mem.read(0x030053C0, 16) ~= "\xA9\x16\x01\x08\x19\x17\x01\x08\x39\x17\x01\x08\x55\x17\x01\x08"
    or (header and #header == 16 and header ~= "GYAKUTEN_SAIASBJ") then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("AGB-ASBJ.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

--- What stands for a code the table hasn't got.
local function unknown(code)
    return "\u{FFFD}"
end

--- The script from `from` up to `to` as a list of { address, word, first argument }: commands with their
--- arguments stepped over. Nil when the words can't be read.
local function words(from, to)
    local bytes = mem.read(from, to - from)
    if not bytes or #bytes < to - from then
        return nil
    end
    local out, i = {}, 1
    while i + 1 <= #bytes do
        local word = bytes:byte(i) + 256 * bytes:byte(i + 1)
        local takes = word < 0x80 and ARGS[word] or 0
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
    if not at or not section or section < LOW or at < section or at >= HIGH or at - section > 0x8000 then
        before = nil
        return
    end
    stood = at == before and stood + 1 or 0
    before = at
    local list = words(math.max(section, at - MOST), at + 2)
    if not list or #list == 0 or list[#list][1] ~= at then
        return -- the words don't fall on AT: the look back began inside a command
    end
    -- The page that ends here: AT stands on a wait, or has just passed a page that goes on by itself.
    local last = #list
    if (list[last][2] ~= WAIT and list[last][2] ~= WAIT_ON) or stood < 2 then
        last = nil
        for i = #list - 1, math.max(1, #list - 12), -1 do
            if list[i][2] == AUTO then
                last = i
                break
            elseif list[i][2] >= 0x80 or list[i][2] == WAIT or list[i][2] == WAIT_ON then
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
    local out = {}
    for i = first, last - 1 do
        local word = list[i][2]
        if word >= 0x80 then
            out[#out + 1] = characters[list[i][4]] or unknown(list[i][4])
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
