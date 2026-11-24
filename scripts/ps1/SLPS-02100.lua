-- reader: title ペルソナ2 罪 (Persona 2: Tsumi)
-- reader: reads the message window, a page at a time; a choice's answers as part of its message
-- reader: version SLPS-02100
-- reader: build f477d5135dc515489f5caeb86ff0d4a9a6f97ce4
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- How the game keeps its text.
--
-- A scene's messages are loaded as one stretch of 16-bit codes, one message after another, each ended by the
-- code 0x1103. SCENE holds that stretch's address. A code is the number of a glyph in the game's own 12x12 font
-- (the table beside this script says which character each is), or a control: 0x11xx without an argument, 0x12xx
-- with one argument word after it. Three of the 0x11xx only leave a gap in the line, and are in the table as
-- a space.
--
-- WINDOW points at the message window's object: its kind at +0x04, and at +0x0C the address the game is
-- reading the stretch at. The message on screen is the one that address stands in: it rests on the message's
-- end code while the window waits. UP is nonzero while the window is on screen.
--
-- Names the game puts in by a code (a speaker's by number, the hero's) read as nothing. 33 codes the script
-- uses aren't in the table and read as U+FFFD. Choices, shops and battles weren't checked.

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

--- Reads a table's text (reader.data("tbl")). Gives { chars = code -> character, ends = code -> true,
--- newlines = code -> true }, each code a string of its bytes.
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

local SCENE = 0x800DE014  -- word: the loaded scene's text
local WINDOW = 0x8007B6E0 -- word: the window's object
local UP = 0x8007B708     -- word: nonzero while the window is on screen
local KIND, READING = 0x04, 0x0C
local MESSAGE_WINDOW = 0x2408
local LONGEST = 36864     -- bytes read of a scene's text: more than the longest has

local END = "\x03\x11"
local NEWLINE, CLEAR = "\x01\x11", "\x02\x11"
-- Controls that draw nothing: a wait, a choice's wait, the hero's names, and one with no known effect.
local SILENT = { ["\x06\x11"] = true, ["\x07\x11"] = true, ["\x09\x11"] = true,
                 ["\x12\x11"] = true, ["\x13\x11"] = true, ["\x14\x11"] = true }
-- Controls with an argument word after them: a pause, a choice, a speaker's name by number, a colour, others.
local WITH_ARGUMENT = {}
for _, low in ipairs{ 0x05, 0x08, 0x0B, 0x0C, 0x0E, 0x10, 0x11, 0x15, 0x1C, 0x1D, 0x1E } do
    WITH_ARGUMENT[string.char(low, 0x12)] = true
end

if mem.u32(0x80020000) ~= 0x02603821 or mem.u32(0x80030000) ~= 0x8FB3002C or mem.u32(0x80018000) ~= 0xAC4A0010 then
    error("this script is for another build of the game", 0)
end

local source = reader.data("tbl")
if not source then
    error(reader.game_id .. ".tbl is missing: the game's character table", 0)
end
local characters = tbl.parse(source).chars

--- The bytes of the message the game is reading: the last one with anything in it that starts at or before
--- `at`, an offset into the scene's text. An offset outside the text is on its last message.
local function message_at(text, at)
    local inside = at >= 0 and at <= #text
    local found
    local start, i = 1, 1
    while i + 1 <= #text do
        if text:sub(i, i + 1) == END then
            if i > start and (start - 1 <= at or not inside) then
                found = text:sub(start, i - 1)
            end
            start, i = i + 2, i + 2
            if inside and start - 1 > at then
                return found
            end
        else
            i = i + (WITH_ARGUMENT[text:sub(i, i + 1)] and 4 or 2)
        end
    end
    if #text + 1 > start and (start - 1 <= at or not inside) then
        found = text:sub(start)
    end
    return found
end

local function decode(message)
    local out, i = {}, 1
    while i + 1 <= #message do
        local code = message:sub(i, i + 1)
        if WITH_ARGUMENT[code] then
            i = i + 4
        else
            if code == NEWLINE then
                out[#out + 1] = "\n"
            elseif code == CLEAR then
                out[#out + 1] = "\n\n"
            elseif not SILENT[code] then
                out[#out + 1] = characters[code] or "\u{FFFD}"
            end
            i = i + 2
        end
    end
    local lines = {}
    for line in (table.concat(out):gsub("\n+$", "") .. "\n"):gmatch("(.-)\n") do
        lines[#lines + 1] = tbl.trim_wide(line)
    end
    return table.concat(lines, "\n")
end

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    local window = mem.u32(WINDOW)
    local kind = window and mem.u16(window + KIND)
    if kind ~= MESSAGE_WINDOW or (mem.u32(UP) or 0) == 0 then
        reader.forget("message")
        return
    end
    local scene, reading = mem.u32(SCENE), mem.u32(window + READING)
    if not scene or not reading then
        return
    end
    local message = message_at(mem.read(scene, LONGEST), reading - scene)
    if message and reader.changed("message", scene .. ":" .. message) then
        local shown = decode(message)
        if shown ~= "" then
            reader.emit{ text = shown, id = text.hash(message) }
        end
    end
end)
