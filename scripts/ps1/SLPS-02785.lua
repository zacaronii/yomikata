-- reader: title ペルソナ2 罰 (Persona 2: Batsu)
-- reader: reads the message window, a message at a time; a choice's answers as part of its message
-- reader: version SLPS-02785 (disc 1)
-- reader: build 2052488cf17bd9cbb61bfc4039143aee6c7a0c4d
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- Persona 2: Tsumi's engine at other addresses (lib/persona2.lua reads it), with Tsumi's glyphs and its own
-- from 0x0A0F on: the table beside this script is Tsumi's with this game's after it.
--
-- A control word 0x1Nxx has N minus 0x11 argument words after it. The window's object has, at +0x14, its
-- state: bit 0x04 is set while a message is up.
--
-- A message of several screens reads whole on each. A choice reads as its question's last line, then an answer
-- a line. 43 codes the script uses aren't in the table and read as U+FFFD. Shops and battles weren't checked.

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
__module["lib.persona2"] = function()
-- The message window of Persona 2 (Tsumi and Batsu share the engine, at other addresses and with other codes).
--
-- A scene's messages are loaded as one stretch of 16-bit codes, one message after another, each ended by the
-- code 0x1103. A code is the number of a glyph in the game's own 12x12 font, or a control; some controls have
-- argument words after them. The window's object holds its kind and the address the game is reading the
-- stretch at: the message on screen is the one that address stands in, and it rests on the message's end code
-- while the window waits.

local tbl = require("lib.tbl")

local persona2 = {}

local END = "\x03\x11"
local NEWLINE, CLEAR = "\x01\x11", "\x02\x11"
local KIND, READING = 0x04, 0x0C
local LONGEST = 36864 -- bytes read of a scene's text: more than the longest has

--- The bytes of the message the game is reading: the last one with anything in it that starts at or before
--- `at`, an offset into the scene's text. An offset outside the text is on its last message. `sizes` gives the
--- bytes of each control that has arguments.
local function message_at(text, at, sizes)
    local inside = at >= 0 and at <= #text
    local found
    local start, i = 1, 1
    while i + 1 <= #text do
        local code = text:sub(i, i + 1)
        if code == END then
            if i > start and (start - 1 <= at or not inside) then
                found = text:sub(start, i - 1)
            end
            start, i = i + 2, i + 2
            if inside and start - 1 > at then
                return found
            end
        else
            i = i + (sizes[code] or 2)
        end
    end
    if #text + 1 > start and (start - 1 <= at or not inside) then
        found = text:sub(start)
    end
    return found
end

local function decode(message, game)
    local out, i = {}, 1
    while i + 1 <= #message do
        local code = message:sub(i, i + 1)
        local size = game.sizes[code]
        if size then
            i = i + size
        else
            if code == NEWLINE then
                out[#out + 1] = "\n"
            elseif code == CLEAR then
                out[#out + 1] = "\n\n"
            elseif not game.silent[code] then
                out[#out + 1] = game.characters[code] or "\u{FFFD}"
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

--- Codes as the strings of their bytes: {0x1106, …} gives a set, {[0x1205] = 4, …} a map.
function persona2.codes(list)
    local out = {}
    for key, value in pairs(list) do
        if math.type(key) == "integer" and key > 0xFF then
            out[string.pack("<I2", key)] = value
        else
            out[string.pack("<I2", value)] = true
        end
    end
    return out
end

--- Reads the window each frame. `game` has: scene and window (addresses of the two words), kind (the message
--- window's), up (a function of the window object's address: whether a message is on screen), silent (controls
--- that draw nothing) and sizes (controls with arguments: their bytes in all), both from persona2.codes.
function persona2.read(game)
    local source = reader.data("tbl")
    if not source then
        error(reader.game_id .. ".tbl is missing: the game's character table", 0)
    end
    game.characters = tbl.parse(source).chars

    reader.on_state_loaded(function()
        reader.forget()
    end)

    reader.on_frame(function()
        local window = mem.u32(game.window)
        local kind = window and mem.u16(window + KIND)
        if kind ~= game.kind or not game.up(window) then
            reader.forget("message")
            return
        end
        local scene, reading = mem.u32(game.scene), mem.u32(window + READING)
        if not scene or not reading then
            return
        end
        local message = message_at(mem.read(scene, LONGEST), reading - scene, game.sizes)
        if message and reader.changed("message", scene .. ":" .. message) then
            local shown = decode(message, game)
            if shown ~= "" then
                reader.emit{ text = shown, id = text.hash(message) }
            end
        end
    end)
end

return persona2
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
local persona2 = require("lib.persona2")

-- Three calls in the game's code.
if mem.u32(0x80022A90) ~= 0x0C008999 or mem.u32(0x8003236C) ~= 0x0C00CC16 or mem.u32(0x8004A498) ~= 0x0C011C00 then
    error("this script is for another build of the game", 0)
end

local STATE, MESSAGE_UP = 0x14, 0x04

persona2.read{
    scene = 0x800EC014,  -- word: the loaded scene's text
    window = 0x8007CEA0, -- word: the window's object
    kind = 0x5290,
    up = function(window)
        return (mem.u8(window + STATE) or 0) & MESSAGE_UP ~= 0
    end,
    -- Controls that draw nothing: a wait, a choice's wait, names put in by code, and one with no known effect.
    silent = persona2.codes{ 0x1106, 0x1107, 0x1109, 0x1120, 0x1121, 0x1122 },
    -- Controls with arguments, by their bytes in all.
    sizes = persona2.codes{ [0x1205] = 4, [0x1208] = 4, [0x120E] = 4, [0x1226] = 4, [0x122E] = 4, [0x122F] = 4,
                            [0x1235] = 4, [0x1428] = 8, [0x1429] = 8, [0x1528] = 10, [0x1828] = 16,
                            [0x182A] = 16, [0x1928] = 18 },
}
