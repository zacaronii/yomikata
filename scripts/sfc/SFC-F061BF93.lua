-- reader: title ドラゴンクエストV 天空の花嫁 (Dragon Quest V: Tenkuu no Hanayome)
-- reader: reads each message of the window as the game starts to draw it, a wait at a time, with who speaks
-- reader: version SFC-F061BF93
-- reader: build f061bf93
-- reader: no hooks
-- reader: no layout where the text is drawn isn't looked into

-- How the game keeps what it shows.
--
-- The game keeps no line of text: its messages are packed in the cartridge, and each character is drawn as a
-- picture into the window's tiles. But it unpacks ahead of what it draws, into a queue of 16-bit codes
-- (QUEUE): the next character to draw is at its front, and the queue moves up by one as each is drawn. The
-- messages still to come lie in it whole: 0x1003 before each, 0x1001 where the game waits for a press,
-- 0x1000 a new line, 0 a space. So a message is said when its first character leaves the queue, from what
-- has left and what is still in it up to the next wait.
--
-- A code is the character's number in the dialogue's own font (the table beside this script); the naming
-- screen and the menus use other codes and aren't read here. Who speaks is in the message itself: a name,
-- then 「.
--
-- Not covered: menus, battle messages, names and shops (other codes, drawn as tiles); the hero's name where
-- a message has it (it isn't in the queue); a character the table hasn't got comes out as U+FFFD, and the
-- table has only those met in the opening. Checked on the opening only.

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

local QUEUE = 0x7E0E4B -- 16-bit codes still to be drawn, the next one first
local LENGTH = 192     -- codes of it that are read
local LOOK = 24        -- codes compared to tell how far it has moved
local NEW_LINE, WAIT, START = 0x1000, 0x1001, 0x1003
local NAME_MOST = 8    -- characters of a speaker's name

if mem.read(0x7EB7D1, 16) ~= "\xB1\x05\xB1\x0A\xB1\x0F\xB1\x14\xB1\x1B\xB1\x22\xB1\x29\xB1\x30" then
    error("this script is for another build of the game", 0)
end

local source = reader.data("tbl")
if not source then
    error("SFC-F061BF93.tbl is missing: the game's table of characters", 0)
end
local characters = {}
for code, character in pairs(tbl.parse(source).chars) do
    characters[string.unpack(">I2", code)] = character
end

--- Codes as text: what a message shows.
local function decode(codes)
    local out = {}
    for _, code in ipairs(codes) do
        if code == 0 then
            out[#out + 1] = "　"
        elseif code == NEW_LINE then
            out[#out + 1] = "\n"
        elseif code < 0x1000 then
            out[#out + 1] = characters[code] or "\u{FFFD}"
        end
    end
    return table.concat(out)
end

--- The queue's codes now.
local function queue()
    local bytes = mem.read(QUEUE, 2 * LENGTH)
    local codes = {}
    for i = 1, #bytes - 1, 2 do
        codes[#codes + 1] = string.unpack("<I2", bytes, i)
    end
    return codes
end

--- How many codes have left the front since `before`: 0 when it stands, nil when it isn't the same queue.
--- Only the message at the front is compared: what lies after its wait may be unpacked anew at any time.
local function moved(before, now)
    local length = LOOK
    for i = 1, LOOK do
        if now[i] == WAIT then
            length = i
            break
        end
    end
    for k = 0, LOOK do
        local same = true
        for i = 1, length do
            if now[i] ~= before[i + k] then
                same = false
                break
            end
        end
        if same then
            return k
        end
    end
    -- A queue just unpacked may still be filling behind its front: a few codes gone, by the front alone.
    for k = 1, 3 do
        if now[1] == before[1 + k] and now[2] == before[2 + k] and now[3] == before[3 + k] and now[1] ~= 0 then
            return k
        end
    end
end

local before         -- the queue a frame ago
local drawn = {}     -- the codes of the message being drawn that have left the queue
local said = false   -- whether it has been said

reader.on_state_loaded(function()
    reader.forget()
    before, drawn, said = nil, {}, false
end)

--- Says the message being drawn: what has left the queue of it, then `more` (codes that left after those,
--- this frame), then the queue as it is now, as far as the next wait.
local function say(more, now)
    said = true
    local codes = { table.unpack(drawn) }
    for _, rest in ipairs{ more, now } do
        for _, code in ipairs(rest) do
            if code == WAIT or code == START then
                goto done
            end
            codes[#codes + 1] = code
        end
    end
    ::done::
    local line = decode(codes):gsub("[\n　]+$", "")
    local speaker
    local at = line:find("「", 1, true)
    if at and at > 1 and utf8.len(line:sub(1, at - 1)) <= NAME_MOST and not line:sub(1, at - 1):find("\n") then
        speaker, line = line:sub(1, at - 1), line:sub(at)
    end
    if line ~= "" then
        reader.emit{ text = line, speaker = speaker, channel = "dialogue", id = text.hash(line) }
    end
end

reader.on_frame(function()
    local now = queue()
    if #now < LENGTH then
        return
    end
    local gone = before and moved(before, now)
    if not gone then
        drawn, said = {}, false -- another queue: what is being drawn of it isn't known
    end
    for i = 1, gone or 0 do
        local code = before[i]
        if code == WAIT or code == START then
            drawn, said = {}, false
        else
            drawn[#drawn + 1] = code
            if not said and code ~= 0 and code < 0x1000 then
                say({ table.unpack(before, i + 1, gone) }, now)
            end
        end
    end
    before = now
end)
