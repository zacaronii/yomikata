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
