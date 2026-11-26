-- reader: title 逆転裁判2 (Gyakuten Saiban 2)
-- reader: reads a message window's page once the game has typed it out and waits
-- reader: version AGB-A3GJ
-- reader: build a2916307
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
--   0x07        wait for a button, then a choice: its answers follow, a row each, up to 0x08 n n
--   0x0A n      wait for a button, then a choice kept elsewhere (n says which)
--   0x2E        a page that goes on by itself: said once AT has passed it
--   0x0E n      who speaks: n's high byte is a number for the name on the window's tab (a picture)
--   0x03 n      a colour; 0x0C n a pause of n frames; 0x0B n the typing's speed
-- The other commands only have to be stepped over: ARGS has how many words each takes.
--
-- Not covered: the answers of a choice kept elsewhere (0x0A), the cursor on a choice, and the court record's
-- texts; a page the player skips before it is whole is still said whole (it is read from the script, not from
-- the screen). Who speaks: the name on the window's tab is a picture, so only the speaker's number is known,
-- and it is given as it is (meta.speaker_number).

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
__module["lib.tbl"] = function()
-- A character table in the Thingy form the catalog keeps (docs/format.md): "0100=学" for a code and its
-- character, the code as hex in the order its bytes lie in memory; "/FFFF" for a code that ends a text and
-- "*FFFE" for one that breaks a line; "#" or ";" for a comment.

local tbl = {}

local function bytes(hex)
    return (hex:gsub("%x%x", function(pair)
        return string.char(tonumber(pair, 16))
    end))
end

--- Reads a table's text (the file a script asks reader.data for). Gives { chars = code -> character,
--- ends = code -> true, newlines = code -> true }, each code a string of its bytes.
function tbl.parse(source)
    local out = { chars = {}, ends = {}, newlines = {} }
    for line in source:gmatch("[^\r\n]+") do
        local first = line:sub(1, 1)
        if first == "/" or first == "*" then
            local code = line:match("^.(%x+)")
            if code and #code % 2 == 0 then
                out[first == "/" and "ends" or "newlines"][bytes(code)] = true
            end
        elseif first ~= "#" and first ~= ";" then
            local code, character = line:match("^(%x+)=(.*)$")
            if code and #code % 2 == 0 and character ~= "" then
                out.chars[bytes(code)] = character
            end
        end
    end
    return out
end

--- Text from `bytes`, `unit` bytes a code, with a parsed table: a code the table ends a text with is a blank
--- line, one it breaks a line with a line break, and one it hasn't got is U+FFFD.
function tbl.decode(bytes, parsed, unit)
    local out = {}
    for i = 1, #bytes - unit + 1, unit do
        local code = bytes:sub(i, i + unit - 1)
        if parsed.ends[code] then
            out[#out + 1] = "\n\n"
        elseif parsed.newlines[code] then
            out[#out + 1] = "\n"
        else
            out[#out + 1] = parsed.chars[code] or "\u{FFFD}"
        end
    end
    return table.concat(out)
end

--- A line without the wide spaces at its end, as a game pads a window's rows.
function tbl.trim_end(line)
    while line:sub(-3) == "\u{3000}" do
        line = line:sub(1, -4)
    end
    return line
end

--- A line without the wide spaces at its start and its end, as a game pads a window's rows.
function tbl.trim_wide(line)
    local space = "\u{3000}"
    local first, last = 1, #line
    while line:sub(first, first + 2) == space do
        first = first + 3
    end
    while last - 2 >= first and line:sub(last - 2, last) == space do
        last = last - 3
    end
    return line:sub(first, last)
end

return tbl
end
local AT, SECTION = 0x03003C44, 0x03003C48 -- words: where the script has got to, where its section begins
local LOW, HIGH = 0x02000000, 0x02040000
local WAIT, WAIT_ON, AUTO, ROW, SPEAKER = 0x02, 0x2D, 0x2E, 0x01, 0x0E
local CHOICE, CHOICE_END, ASK = 0x07, 0x08, 0x0A
local MOST = 0x800 -- bytes of a section looked back through
local ARGS = { [0x03] = 1, [0x04] = 1, [0x05] = 2, [0x06] = 1, [0x08] = 2, [0x09] = 3, [0x0A] = 1, [0x0B] = 1,
               [0x0C] = 1, [0x0D] = 1, [0x0E] = 1, [0x0F] = 2, [0x10] = 1, [0x12] = 3, [0x13] = 1, [0x17] = 1,
               [0x18] = 1, [0x19] = 2, [0x1A] = 4, [0x1B] = 1, [0x1C] = 1, [0x1D] = 1, [0x1E] = 3, [0x20] = 1,
               [0x22] = 2, [0x23] = 2, [0x25] = 1, [0x26] = 1, [0x27] = 2, [0x28] = 1, [0x29] = 1, [0x2A] = 3,
               [0x2C] = 1, [0x2F] = 2, [0x30] = 1, [0x31] = 2, [0x32] = 2, [0x33] = 5, [0x34] = 1, [0x35] = 2,
               -- Met in this game and not in the first: lengths read off the first scene's script, where the words
               -- after each fall back into step only with these.
               [0x42] = 1, [0x46] = 1, [0x4E] = 1, [0x54] = 2, [0x5C] = 2, [0x65] = 2, [0x6F] = 1 }

-- The build: a table of four of the program's own addresses that the game keeps in the CPU's memory from
-- its title on. And the cartridge's header (its title and code), where a host gives the cartridge to read.
local header = mem.read(0x080000A0, 16)
if mem.read(0x03005630, 16) ~= "\xC5\x8B\x01\x08\x35\x8C\x01\x08\x55\x8C\x01\x08\x71\x8C\x01\x08"
    or (header and #header == 16 and header ~= "GYAKUTEN_SA2A3GJ") then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("AGB-A3GJ.tbl is missing: the game's table of characters", 0)
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

--- Whether a command ends a page that waits for a button.
local function waits(word)
    return word == WAIT or word == WAIT_ON or word == CHOICE or word == ASK
end

--- The answers of the choice whose 0x07 is at `at`: the characters up to its 0x08, a row each. Nil when
--- they can't be read.
local function answers(at)
    local list = words(at + 2, math.min(at + 0x102, HIGH))
    if not list then
        return nil
    end
    local out = {}
    for _, w in ipairs(list) do
        if w[2] == CHOICE_END then
            return table.concat(out)
        elseif w[2] >= 0x80 then
            out[#out + 1] = characters[w[4]] or unknown(w[4])
        elseif w[2] == ROW then
            out[#out + 1] = "\n"
        end
    end
    return nil
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
    if not waits(list[last][2]) or stood < 2 then
        last = nil
        for i = #list - 1, math.max(1, #list - 12), -1 do
            if list[i][2] == AUTO then
                last = i
                break
            elseif list[i][2] >= 0x80 or waits(list[i][2]) then
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
        if waits(list[i][2]) or list[i][2] == AUTO or list[i][2] == CHOICE_END then -- a page after a choice's answers
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
    local offered = list[last][2] == CHOICE and answers(ends)
    if offered and offered:find("%S") then
        reader.emit{ text = offered, channel = "choice", id = text.hash(string.format("%08X?", ends) .. offered) }
    end
end)
