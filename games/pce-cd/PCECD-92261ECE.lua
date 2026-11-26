-- reader: title 天外魔境II 卍MARU (Tengai Makyou II: Manji Maru)
-- reader: reads a message window's page once the game has typed it out and waits for the button
-- reader: version PCECD-92261ECE
-- reader: build 92261ece
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- A message is unpacked whole into the card's RAM before the window types it, and stays there while it is
-- shown. A word in the console's own 8 KB (AT) is where the window has got to: an address as the program
-- sees it, 0xE000 and up, the card's RAM from 0xD0000 being what it sees there (BUFFER). As a page types, AT
-- walks through it, and it stands on the code that waits for the button until the button comes.
--
-- A message is Shift-JIS for kanji and signs (two bytes, the first 0x81-0x9F or 0xE0-0xEF), and one byte
-- for the rest: 0xA0-0xDF (the half-width katakana codes, standing for hiragana) and 0x20-0x7F (voiced kana,
-- digits and the commonest kanji). The game turns each one-byte code into Shift-JIS with a table of its own
-- (TABLE: two bytes for each code, 0xA0-0xDF first and 0x20-0x7F after), and the script uses that table as
-- the game has it in memory. Other bytes:
--   0x0D        a new row
--   0x0E, 0x0F  the kana between are katakana
--   0x12        wait for the button; the next page, if any, follows
--   0x0C        a new page begins (after 0x12)
--   0x00        the message's end (after its last 0x12)
-- The table has a bar for 0x21, which the game's font draws as a double exclamation mark.
--
-- So a page is the bytes from the last 0x00, 0x0C or 0x12 before AT up to AT, and it is said once AT stands
-- on a 0x12.
--
-- Not covered: who speaks (the game shows no name), menus and fights' texts, choices; a page the player cuts
-- short is still said whole (it is read from the message, not the screen). That the program's 0xE000 is the
-- card's RAM at 0xD0000 is as it was in every scene played (the house, the village, the shrine), not known
-- to hold everywhere: where it doesn't, AT points at no 0x12 there and nothing is said.

local AT = 0x1F0095 -- word: where the window has got to, as the program sees it
local SEEN, BUFFER = 0xE000, 0xD0000 -- the program's 0xE000 is the card's RAM at 0xD0000
local TABLE = 0xD4E8E -- two bytes (Shift-JIS) for each one-byte code: 0xA0-0xDF, then 0x20-0x7F
local WAIT, PAGE, FINISH, ROW, KATAKANA, HIRAGANA = 0x12, 0x0C, 0x00, 0x0D, 0x0E, 0x0F
local MOST = 0x200 -- bytes looked back through for a page's start
-- The table's first entries as the game has them (a full-width space, 。, 「 and 」): a check that it is there.
local TABLE_START = "\x81\x40\x81\x42\x81\x75\x81\x76"

-- The build: the table's entries for 0x20-0x2F (a space, the bar, and the fourteen kanji the game gave codes of
-- one byte first), there from the title on. Not there in the console's first moments, before the game runs.
if mem.read(TABLE + 2 * 0x40, 32) ~= "\x81\x40\x81\x62\x91\xE5\x90\x6C\x8E\x71\x97\x6C\x8C\xA9\x93\x56"
                                    .. "\x90\x5F\x93\x81\x8D\x95\x91\xBA\x8D\x91\x90\x85\x8E\xE8\x88\xEA" then
    error("this script is for another build of the game", 0)
end

--- The two Shift-JIS bytes the game draws for a one-byte code, or nil.
local function from_table(table_bytes, code)
    local k = code >= 0xA0 and code - 0xA0 or 0x40 + code - 0x20
    return table_bytes:sub(2 * k + 1, 2 * k + 2)
end

--- Hiragana as katakana.
local function katakana(s)
    local out = {}
    for _, c in utf8.codes(s) do
        out[#out + 1] = utf8.char((c >= 0x3041 and c <= 0x3096) and c + 0x60 or c)
    end
    return table.concat(out)
end

--- A page's bytes as text.
local function decode(bytes, table_bytes)
    local out, run, kata = {}, {}, false
    local function flush()
        if #run > 0 then
            local s = text.sjis(table.concat(run))
            out[#out + 1] = kata and katakana(s) or s
            run = {}
        end
    end
    local i = 1
    while i <= #bytes do
        local c = bytes:byte(i)
        if (c >= 0x81 and c <= 0x9F) or (c >= 0xE0 and c <= 0xEF) then
            run[#run + 1] = bytes:sub(i, i + 1)
            i = i + 2
        else
            if c == 0x21 then
                flush()
                out[#out + 1] = "\u{203C}"
            elseif c >= 0x20 and c <= 0x7F or c >= 0xA0 and c <= 0xDF then
                run[#run + 1] = from_table(table_bytes, c)
            elseif c == ROW then
                flush()
                out[#out + 1] = "\n"
            elseif c == KATAKANA or c == HIRAGANA then
                flush()
                kata = c == KATAKANA
            end
            i = i + 1
        end
    end
    flush()
    return table.concat(out)
end

local said, before, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, before, stood = nil, nil, 0
end)

reader.on_frame(function()
    local at = mem.u16(AT)
    if not at or at < SEEN then
        before = nil
        return
    end
    stood = at == before and stood + 1 or 0
    before = at
    local where = BUFFER + at - SEEN
    if mem.u8(where) ~= WAIT then
        said = nil -- the page was passed: the same one shown again is said again
        return
    end
    if stood < 1 or said == where then
        return
    end
    local table_bytes = mem.read(TABLE, 2 * 160)
    if not table_bytes or #table_bytes < 2 * 160 or table_bytes:sub(1, #TABLE_START) ~= TABLE_START then
        return
    end
    local back = math.min(MOST, where - BUFFER)
    local bytes = mem.read(where - back, back)
    if not bytes or #bytes < back then
        return
    end
    local first = 1
    for i = #bytes, 1, -1 do
        local c = bytes:byte(i)
        if c == WAIT or c == PAGE or c == FINISH then
            first = i + 1
            break
        end
    end
    said = where
    local shown = decode(bytes:sub(first), table_bytes)
    -- Without the blanks and empty rows a page begins and ends with.
    for _, edge in ipairs({ "\u{3000}", "\n" }) do
        while shown:sub(1, #edge) == edge do
            shown = shown:sub(#edge + 1)
        end
    end
    local trimmed = true
    while trimmed do
        trimmed = false
        for _, edge in ipairs({ "\u{3000}", "\n" }) do
            if #shown >= #edge and shown:sub(-#edge) == edge then
                shown, trimmed = shown:sub(1, -#edge - 1), true
            end
        end
    end
    if shown:find("%S") then
        reader.emit{ text = shown, channel = "dialogue", id = text.hash(string.format("%05X", where) .. shown) }
    end
end)
