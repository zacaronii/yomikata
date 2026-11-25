-- reader: title ファイナルファンタジーIX (Final Fantasy IX)
-- reader: reads a window's message when it opens, with its speaker, and a choice's answers
-- reader: version SLPS-02000 to SLPS-02003 (the four discs share one program)
-- reader: build 8ba95c45e7c38ae944b74d9a75cb565c36d6a906
-- reader: no hooks
-- reader: no layout a window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The game draws its text a glyph at a time out of a cache of glyph pictures, and keeps no line of text as it
-- stands on screen. What it keeps is the place's whole block of messages, as loaded: BLOCK points at it. The
-- block starts with how many messages it has, then a record of eight bytes for each (the third 16-bit number
-- is where its text starts, counted from the first record), then the messages.
--
-- A message is one byte a character in codes of the game's own (the table beside this script), kanji two
-- bytes from 0xFA to 0xFE, and: 0xF7 a line break, 0xF8 the space that sets a following line under the first,
-- 0xFF the end, and 0xF9 with a second byte for everything else. Of those, 0x10 to 0x17 put in a party
-- member's name (kept in the member's own record, so a name the player chose is read as chosen), 0x08 says
-- the rows that follow are a choice's answers, 0x09 ends what one window shows (the message goes on in the
-- next), and some take bytes of their own (ARGUMENTS), told from where the messages end.
--
-- The windows are eight records of 0x2C bytes from WINDOWS: the 16-bit number at +4 is 1 while a window is shown,
-- and the one at +0x1A is its message's number in the block. A window's message is said when the window
-- opens, whole: the game types it out, but the text is there from the start. A message whose second line
-- opens a bracket has the speaker's name as its first line.
--
-- Kanji are of two kinds. Codes 0xFA nn and 0xFB nn are the same everywhere (the table has them). Codes from
-- 0xFC 00 are a font of the place's own, loaded with its messages: the block is the first part of a small
-- package whose second part lists that font's glyphs, four bytes each (where the glyph is in the game's
-- texture, and its width); the pictures themselves are in video memory. So one code is different kanji in
-- different places, and nothing in main memory says which kanji a glyph is. A place is told here by a hash
-- of its glyph list and its messages' records, and the file of places beside this script says, for the
-- places it knows, which kanji each of its codes is. A kanji neither file has comes out as U+FFFD: the files
-- hold what was read off the screen so far.
--
-- The places file has the 64 places found on the four discs, read off the discs' own data.
-- Checked by playing on disc 1, from the first line to the first battle's start.

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
local tbl = require("lib.tbl")

local BLOCK = 0x8007AE14 -- word: the place's messages
local CURSOR = 0x8007AE18 -- word: the answer a choice's cursor is on, from 0
local WINDOWS, WINDOW_SIZE, SLOTS = 0x8007AE84, 0x2C, 8
local SHOWN, MESSAGE = 0x04, 0x1A -- u16 each, in a window's record
local NAMES, NAME_STEP = 0x8008FA8C, 0x90 -- a party member's name, in the game's codes, 0xFF after it
local RAM, RAM_END = 0x80010000, 0x80200000
local MOST = 1024 -- bytes of one message
-- How many bytes follow 0xF9 and these second bytes.
local ARGUMENTS = { [0x01] = 1, [0x02] = 1, [0x0E] = 1, [0x0F] = 1, [0x40] = 1, [0x41] = 1, [0x43] = 1,
                    [0x44] = 1, [0x48] = 3, [0x49] = 1, [0x4A] = 1 }
local CHOICE, PAGE_END = 0x08, 0x09
local SETTLED = 4 -- frames a window must show a message before it is said

-- Two calls of the program that is there from power-on.
if mem.u32(0x80012614) ~= 0x0C050003 or mem.u32(0x8003F4D0) ~= 0x0C014B25 then
    error("this script is for another build of the game", 0)
end

local source = reader.data("tbl")
if not source then
    error("SLPS-02000.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

-- The places' own kanji: "@<a place's key>" starts a place, then "<code's number, hex>=<kanji>" lines.
local places = {}
do
    local place
    for line in (reader.data("places") or ""):gmatch("[^\r\n]+") do
        local key = line:match("^@(%x+)")
        local number, kanji = line:match("^(%x+)=(.+)$")
        if key then
            place = {}
            places[key] = place
        elseif place and number then
            place[tonumber(number, 16)] = kanji
        end
    end
end

--- The key of the place whose messages `block` is: a hash of its font's glyph list. Nil without a font.
local function place_key(block)
    local font = block - 8 + (mem.u32(block - 4) or 0)
    local count = mem.u32(font + 0x18) or 0
    if count < 1 or count > 1024 then
        return nil
    end
    -- With the records of its messages: two places could have glyph lists alike.
    local messages = math.min(mem.u16(block) or 0, 512)
    return text.hash((mem.read(font + 0x1C, 4 * count) or "") .. (mem.read(block + 4, 8 * messages) or ""))
end

--- A party member's name as the player has it.
local function name(member)
    local bytes = mem.read(NAMES + member * NAME_STEP, 12) or ""
    local out = {}
    for i = 1, #bytes do
        local code = bytes:sub(i, i)
        if code == "\xFF" then
            break
        end
        out[#out + 1] = characters[code] or "\u{FFFD}"
    end
    return table.concat(out)
end

--- A message's text, and whether a choice's answers start in it (the row they start on, from 1).
local function decode(bytes, own)
    local out, answers, row = {}, nil, 1
    local i = 1
    while i <= #bytes do
        local code = bytes:byte(i)
        if code == 0xFF then
            break
        elseif code == 0xF7 then
            out[#out + 1] = "\n"
            row = row + 1
            i = i + 1
        elseif code == 0xF8 then
            i = i + 1
        elseif code == 0xF9 then
            local what = bytes:byte(i + 1)
            if what == nil or what == PAGE_END then
                break
            elseif what == CHOICE then
                answers = row
            elseif what >= 0x10 and what <= 0x17 then
                out[#out + 1] = name(what - 0x10)
            end
            i = i + 2 + (ARGUMENTS[what] or 0)
        elseif code >= 0xFC then
            out[#out + 1] = own and own[(code - 0xFC) * 256 + (bytes:byte(i + 1) or 0)] or "\u{FFFD}"
            i = i + 2
        elseif code >= 0xFA then
            out[#out + 1] = characters[bytes:sub(i, i + 1)] or "\u{FFFD}"
            i = i + 2
        else
            out[#out + 1] = characters[bytes:sub(i, i)] or "\u{FFFD}"
            i = i + 1
        end
    end
    return table.concat(out), answers
end

local WIDE = "\u{3000}"

--- A row without the wide spaces the game sets it in from the window's edge with.
local function trim(line)
    while line:sub(1, #WIDE) == WIDE do
        line = line:sub(#WIDE + 1)
    end
    while line:sub(-#WIDE) == WIDE do
        line = line:sub(1, -#WIDE - 1)
    end
    return line
end

local known_block, own = nil, nil -- the block whose place was last looked up, and that place's kanji
local windows = {}                -- per window: the message it shows, for how long, and what was said of it

reader.on_state_loaded(function()
    reader.forget()
    known_block, own, windows = nil, nil, {}
end)

--- Says a window's message: its speaker and lines, or a choice's question and answers. True for a choice.
local function say(block, count, number)
    -- From where its text starts to where the next message's record says that one starts: a message that
    -- goes on in the next window has no end code of its own.
    local start = mem.u16(block + 4 + 8 * number + 4) or 0
    local next_one = number + 1 < count and mem.u16(block + 4 + 8 * (number + 1)) or start + MOST
    local size = math.max(0, math.min(next_one - start, MOST))
    if block ~= known_block then
        known_block, own = block, places[place_key(block) or ""]
    end
    local shown, answers = decode(mem.read(block + 4 + start, size) or "", own)
    local rows = {}
    for row in (shown .. "\n"):gmatch("(.-)\n") do
        rows[#rows + 1] = trim(row)
    end
    if answers then
        local question, choices = {}, {}
        for n, row in ipairs(rows) do
            if row ~= "" then
                local into = n >= answers and choices or question
                into[#into + 1] = row
            end
        end
        if #question > 0 then
            local speaker
            if #question > 1 and question[2]:sub(1, 3) == "「" then
                speaker = table.remove(question, 1)
            end
            local asked = table.concat(question, "\n")
            reader.emit{ text = asked, speaker = speaker, id = text.hash(asked) }
        end
        if #choices > 0 then
            reader.emit{ text = table.concat(choices, "\n"), channel = "choice" }
        end
        return #choices > 0
    end
    -- A message whose second row opens a bracket has its speaker's name as its first.
    local speaker
    if #rows > 1 and rows[2]:sub(1, 3) == "「" then
        speaker = table.remove(rows, 1)
    end
    while #rows > 0 and rows[1] == "" do
        table.remove(rows, 1)
    end
    while #rows > 0 and rows[#rows] == "" do
        rows[#rows] = nil
    end
    local line = table.concat(rows, "\n")
    if line ~= "" then
        reader.emit{ text = line, speaker = speaker, id = text.hash(line) }
    end
    return false
end

--- Every message of the place in memory, in order, as the script reads them (docs/script-api.md, "dump"): for
--- reading through a place's text when its kanji are being read off its glyphs. Not what is on screen.
function dump()
    local block = mem.u32(BLOCK)
    local count = block and block >= RAM and block < RAM_END and mem.u16(block) or 0
    local own = places[count > 0 and place_key(block) or ""]
    for number = 0, count - 1 do
        local start = mem.u16(block + 4 + 8 * number + 4) or 0
        local next_one = number + 1 < count and mem.u16(block + 4 + 8 * (number + 1)) or start + MOST
        local size = math.max(0, math.min(next_one - start, MOST))
        local shown = decode(mem.read(block + 4 + start, size) or "", own)
        if shown:find("%S") then
            reader.emit{ text = shown, channel = "interface", meta = { message = number } }
        end
    end
end

reader.on_frame(function()
    local choosing = false
    local block = mem.u32(BLOCK)
    local count = block and block >= RAM and block < RAM_END and mem.u16(block) or 0
    for slot = 0, SLOTS - 1 do
        local window = WINDOWS + slot * WINDOW_SIZE
        local number = mem.u16(window + MESSAGE)
        if mem.u16(window + SHOWN) ~= 1 or not number or number >= count then
            windows[slot] = nil
        else
            local seen = windows[slot]
            if not seen or seen.number ~= number or seen.block ~= block then
                seen = { number = number, block = block, frames = 0 }
                windows[slot] = seen
            end
            seen.frames = seen.frames + 1
            -- A window may show a message for a frame on its way to another: it is said once it has stayed.
            if seen.frames == SETTLED then
                seen.choice = say(block, count, number)
            end
            choosing = choosing or seen.choice == true
        end
    end
    reader.cursor(choosing and mem.u32(CURSOR) or nil)
end)
