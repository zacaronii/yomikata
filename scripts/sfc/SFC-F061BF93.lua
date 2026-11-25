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
-- 0x1002 where it waits and the same speaker goes on in a window of their own, 0x1000 a new line, 0 a space. So a message is said when its first character leaves the queue, from what
-- has left and what is still in it up to the next wait.
--
-- A code is the character's number in the dialogue's own font (the table beside this script); the naming
-- screen and the menus use other codes and aren't read here. Who speaks is in the message itself: a name,
-- then 「. Where there is no name the game draws ＊「 itself, which isn't in the queue, unless the message
-- begins with 0x1006: then nobody speaks. 0x1011 and 0x1012 stand for the hero's name.
--
-- A queue just unpacked comes into sight a character late where a named speaker begins it (the game draws
-- the first as it unpacks): the name is then mended from the speaker's next message in the queue, and stays
-- a character short where there is none.
--
-- Not covered: menus, battle messages, names and shops (other codes, drawn as tiles); a voiced kana in the
-- hero's name; a character the table hasn't got comes out as U+FFFD, and the
-- table has only those met in play so far. Checked from the opening to the pier of the first port: 48 screens
-- over 9 scenes. A question's answers (yes, no) are in a box of their own and aren't read.

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
local NEW_LINE, WAIT, NEXT, START = 0x1000, 0x1001, 0x1002, 0x1003
local PLAIN = 0x1006   -- before a message nobody speaks: no ＊「 is drawn
local PAGE = 0x1010    -- a wait within such a message
local HERO, HERO_TOO = 0x1011, 0x1012 -- the hero's name
local NAME = 0x7E2001  -- where it is kept: 4 codes of the menus' font, 0x01 where it is shorter
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

-- The menus' font, which names are kept in: the plain kana in the order of the fifty sounds, あ at 0x10 and
-- ア at 0x42. A voiced kana's code isn't known yet.
local KANA = {}
do
    local rows = { [0x10] = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをんっゃゅょ",
                   [0x42] = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンッャュョ" }
    for first, row in pairs(rows) do
        local code = first
        for _, point in utf8.codes(row) do
            KANA[code] = utf8.char(point)
            code = code + 1
        end
    end
end

--- The hero's name, as the player entered it.
local function hero()
    local out = {}
    for i = 1, 4 do
        local code = mem.u8(NAME + i - 1)
        if code and code ~= 0x01 then
            out[#out + 1] = KANA[code] or "\u{FFFD}"
        end
    end
    return table.concat(out)
end

--- Codes as text: what a message shows.
local function decode(codes)
    local out = {}
    for _, code in ipairs(codes) do
        if code == 0 then
            out[#out + 1] = "　"
        elseif code == NEW_LINE then
            out[#out + 1] = "\n"
        elseif code == HERO or code == HERO_TOO then
            out[#out + 1] = hero()
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
        if now[i] == WAIT or now[i] == NEXT or now[i] == PAGE then
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
local whole = false  -- whether its start was seen to leave the queue

--- The name before the next message of the queue that begins with a start mark, a name and 「.
local function next_name(now)
    for i = 1, #now - 1 do
        if now[i] == START then
            local name = {}
            for k = i + 1, math.min(i + NAME_MOST + 1, #now) do
                if characters[now[k]] == "「" then
                    return decode(name)
                elseif now[k] == 0 or now[k] >= 0x1000 then
                    break
                end
                name[#name + 1] = now[k]
            end
        end
    end
end

reader.on_state_loaded(function()
    reader.forget()
    before, drawn, said, whole = nil, {}, false, false
end)

--- Says the message being drawn: what has left the queue of it, then `more` (codes that left after those,
--- this frame), then the queue as it is now, as far as the next wait.
local function say(more, now)
    said = true
    local codes = { table.unpack(drawn) }
    local plain = false
    for _, rest in ipairs{ more, now } do
        for _, code in ipairs(rest) do
            if code == WAIT or code == NEXT or code == PAGE or code == START then
                goto done
            end
            codes[#codes + 1] = code
        end
    end
    ::done::
    for _, code in ipairs(codes) do
        plain = plain or code == PLAIN
    end
    -- What ends a message is cut code by code: a pattern would cut by bytes, and into a character.
    while #codes > 0 and (codes[#codes] == 0 or (codes[#codes] >= 0x1000 and codes[#codes] ~= HERO
                                                  and codes[#codes] ~= HERO_TOO)) do
        codes[#codes] = nil
    end
    local line = decode(codes)
    local speaker
    -- A queue just unpacked comes into sight with the first character of a named speaker's message already
    -- drawn and gone. The same speaker's next message, further down the queue, has the name whole.
    if not whole then
        local name = next_name(now)
        local at = line:find("「", 1, true)
        local short = at and line:sub(1, at - 1)
        if name and short and utf8.len(short) and utf8.len(name) == utf8.len(short) + 1
            and name:sub(#name - #short + 1) == short then
            line = name .. line:sub(at)
        end
    end
    local at = line:find("「", 1, true)
    if at and at > 1 and utf8.len(line:sub(1, at - 1)) <= NAME_MOST and not line:sub(1, at - 1):find("\n") then
        speaker, line = line:sub(1, at - 1), line:sub(at)
    elseif not plain and line ~= "" then
        speaker, line = "＊", "「" .. line -- the game draws these itself where a speaker has no name
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
        drawn, said, whole = {}, false, false -- another queue: what is being drawn of it isn't known
    end
    for i = 1, gone or 0 do
        local code = before[i]
        if code == WAIT or code == NEXT or code == PAGE or code == START then
            drawn, said, whole = {}, false, code == START
        else
            drawn[#drawn + 1] = code
            if not said and code ~= 0 and code < 0x1000 then
                say({ table.unpack(before, i + 1, gone) }, now)
            end
        end
    end
    before = now
end)
