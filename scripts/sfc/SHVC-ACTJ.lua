-- reader: title クロノ・トリガー (Chrono Trigger)
-- reader: reads each page of the message window once it has been typed, with who speaks
-- reader: version SHVC-ACTJ
-- reader: build bd8d62db
-- reader: no hooks
-- reader: no layout where the text is drawn isn't looked into

-- How the game keeps what it shows.
--
-- The game keeps no line of text in memory: its messages are packed in the cartridge (with a dictionary of
-- common words), and it unpacks a character at a time as it draws. What memory has is the character being
-- drawn: its code (CODE, 16 bits: kana and signs below 0x100, kanji above), and where the next one will go
-- across the window (NEXT_X), which moves on with every character drawn and goes back at a new line. NEXT_X
-- also goes back when a page is cleared, with nothing drawn: a character has been drawn only when the
-- unpacker has moved as well (AT: where in the cartridge it reads the message, and how far into a word of
-- the dictionary it is), and moved on by a code or two, not to another message.
--
-- WINDOW says what the message window is doing: 0 when there is none, 1 to 5 as it opens, 6 while a page is
-- typed, 7 while it waits for a press, 8 to 12 as it clears for the next page, 13 as it closes. So the
-- script gathers the characters drawn while it is 6, and says the page when it is 6 no longer.
--
-- A code is the character's number in the game's font (the table beside this script). Who speaks is in the
-- message itself: a name, then 「.
--
-- Not covered: menus, battle messages, names of places and items (drawn another way); a character the
-- table hasn't got comes out as U+FFFD.

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

local AT = 0x7E0230     -- the unpacker: a flag, where it reads (3 bytes), …, a word's place (3) and what is left of it
local NEXT_X = 0x7E0234 -- where the next character will be drawn across the window, in pixels
local CODE = 0x7E0235   -- the code of the character being drawn, 16 bits
local WINDOW = 0x7E0129 -- what the message window is doing
local TYPING = 6
local NAME_MOST = 8     -- characters of a speaker's name

if mem.read(0x7E27EB, 13) ~= "\x33\x2B\x0C\x32\x14\x21\x48\x25\xF4\x7D\x01\x90\x01" then
    error("this script is for another build of the game", 0)
end

local source = reader.data("tbl")
if not source then
    error("SHVC-ACTJ.tbl is missing: the game's table of characters", 0)
end
local characters = {}
for code, character in pairs(tbl.parse(source).chars) do
    characters[string.unpack(">I2", code)] = character
end

local last_x          -- NEXT_X a frame ago
local last_at         -- and the unpacker's state
local last_from       -- and where it read the message
local page = {}       -- what has been drawn of the page: characters and line breaks

reader.on_state_loaded(function()
    reader.forget()
    last_x, last_at, last_from, page = nil, nil, nil, {}
end)

local function say()
    while page[#page] == "\n" or page[#page] == "　" do
        page[#page] = nil
    end
    while page[1] == "\n" or page[1] == "　" do
        table.remove(page, 1)
    end
    local line = table.concat(page)
    page = {}
    if line == "" then
        return
    end
    local speaker
    local at = line:find("「", 1, true)
    if at and at > 1 and utf8.len(line:sub(1, at - 1)) <= NAME_MOST and not line:sub(1, at - 1):find("\n") then
        speaker, line = line:sub(1, at - 1), line:sub(at)
    end
    reader.emit{ text = line, speaker = speaker, channel = "dialogue", id = text.hash(line) }
end

reader.on_frame(function()
    local x, code, state, window = mem.u8(NEXT_X), mem.u16(CODE), mem.read(AT, 11), mem.u8(WINDOW)
    if not x or not code or not window or #state < 11 then
        return
    end
    local at = state:sub(1, 4) .. state:sub(8, 11)
    local from = string.unpack("<I3", state, 2)
    local on = last_from and from - last_from
    if window ~= TYPING then
        if #page > 0 then
            say()
        end
    elseif last_x and x ~= last_x and at ~= last_at and on >= 0 and on <= 3 then
        if x < last_x and #page > 0 then
            page[#page + 1] = "\n"
        end
        page[#page + 1] = characters[code] or "\u{FFFD}"
    end
    last_x, last_at, last_from = x, at, from
end)
