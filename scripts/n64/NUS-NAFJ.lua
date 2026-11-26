-- reader: title どうぶつの森 (Doubutsu no Mori)
-- reader: reads a speech bubble's page once the game has typed it out and waits for the button
-- reader: version NUS-NAFJ
-- reader: build NUS-NAFJ-00
-- reader: no hooks
-- reader: no layout the bubble's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- A message is put whole into RDRAM before the bubble types it. A word (AT) is where the bubble has got to,
-- and the word after it (TYPED) how many bytes of the page it is on have been typed, so the page begins at
-- AT minus TYPED. A character is a byte: the table beside this script says which (the codes are the game's
-- own; digits and signs are ASCII). Other bytes:
--   0xCD        a new row
--   0x7F n ...  a command: 0x7F 0x04 waits for the button (AT then stands just past it), 0x7F 0x02 begins a
--               new page, 0x7F 0x03 n is a pause, 0x7F 0x09 takes three bytes more (it puts a name in, the
--               player's among them, which this script leaves out), 0x7F 0x16, 0x17 or 0x18 puts a choice of
--               two, three or four answers up, their numbers after it (AT then stands past them), 0x7F 0x55 puts the keyboard up for a name (the
--               same), 0x7F 0x00 ends the message (AT stands on it, the last page then being whole)
-- So a page is the bytes from AT minus TYPED to AT, said once AT stands past a wait, a choice or the
-- keyboard, or on the message's end.
--
-- Not covered: who speaks (the name on the bubble's tab), names put into a page (the player's, the village's),
-- a choice's answers and its cursor, the name and
-- letter screens, and characters the table hasn't got (U+FFFD). AT stays on a message's end after its
-- bubble closes, so a state loaded then says the last page again.

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
local AT, TYPED = 0x80147644, 0x80147648 -- words: where the bubble has got to; bytes typed of its page
local LOW, HIGH = 0x80000000, 0x80800000
local ROW, COMMAND = 0xCD, 0x7F
local WAIT, CHOICE, PAUSE, INSERT, FINISH, KEYBOARD = 0x04, 0x16, 0x03, 0x09, 0x00, 0x55
local CHOICES = { [0x16] = true, [0x17] = true, [0x18] = true } -- a choice of two, three or four answers
-- A command's bytes in all, 2 unless said: 0x7F 0x05 takes a colour (three bytes), 0x7F 0x50 a colour and a
-- byte (the colour of a name put into the page, four bytes), 0x7F 0x51,
-- 0x7F 0x52 and 0x7F 0x5A a byte (they set how a row is drawn; a telephone's voice has them on each row).
local LENGTH = { [PAUSE] = 3, [INSERT] = 5, [CHOICE] = 6, [0x05] = 5, [0x50] = 6, [0x51] = 3, [0x52] = 3, [0x5A] = 3 }
local MOST = 0x400 -- the most bytes a page is read over

-- The build: pointers of the game's own at 0x80040000 and a stretch of its data, there from the title on.
if mem.read(0x80040000, 16) ~= "\x80\x03\xFF\x60\x80\x03\xF3\x30\x00\x08\x00\x00\x00\x00\x00\x08"
    or mem.read(0x801215FC, 16) ~= "\xAA\xAA\xBB\xBB\x66\x66\xB3\x33\x33\x33\x33\x22\x44\x44\x44\x44" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("NUS-NAFJ.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

--- A page's bytes as text: its rows, without the blanks they end with.
local function decode(bytes)
    local rows, row, i = {}, {}, 1
    while i <= #bytes do
        local c = bytes:byte(i)
        if c == COMMAND and CHOICES[bytes:byte(i + 1)] then
            break -- the choice's answers follow: not the page's
        elseif c == COMMAND then
            i = i + (LENGTH[bytes:byte(i + 1)] or 2)
        elseif c == ROW then
            rows[#rows + 1] = table.concat(row)
            row, i = {}, i + 1
        else
            row[#row + 1] = characters[string.char(c)] or "\u{FFFD}"
            i = i + 1
        end
    end
    rows[#rows + 1] = table.concat(row)
    for k, r in ipairs(rows) do
        while r:sub(-3) == "\u{3000}" do
            r = r:sub(1, -4)
        end
        rows[k] = r
    end
    while #rows > 0 and rows[#rows] == "" do
        rows[#rows] = nil
    end
    while #rows > 0 and rows[1] == "" do
        table.remove(rows, 1)
    end
    return table.concat(rows, "\n")
end

-- The last pages said, by where each starts and what it says: a page isn't said again while it is among them.
-- (Some scenes switch between two places from one frame to the next, so the last one alone isn't enough.)
local said, RECENT = {}, 8

reader.on_state_loaded(function()
    reader.forget()
    said = {}
end)

reader.on_frame(function()
    local at, typed = mem.u32be(AT), mem.u32be(TYPED)
    if not at or not typed or at < LOW or at >= HIGH or typed > MOST or typed < 2 then
        return
    end
    local start = at - typed
    local bytes = mem.read(start, typed)
    if not bytes or #bytes < typed then
        return
    end
    local ends
    local last = bytes:byte(typed)
    if bytes:byte(typed - 1) == COMMAND and (last == WAIT or last == KEYBOARD) then
        ends = typed - 2
    elseif mem.read(at, 2) == string.char(COMMAND, FINISH) then
        ends = typed
    else
        for k = math.max(1, typed - 12), typed - 1 do -- a choice put up: its answers' numbers after it
            if bytes:byte(k) == COMMAND and CHOICES[bytes:byte(k + 1)] then
                ends = typed
                break
            end
        end
    end
    if not ends then
        return -- typing, or past the page's end on the way to a choice
    end
    local shown = decode(bytes:sub(1, ends))
    local key = string.format("%08X", start) .. shown
    for _, k in ipairs(said) do
        if k == key then
            return
        end
    end
    table.insert(said, 1, key)
    said[RECENT + 1] = nil
    if shown:find("%S") then
        reader.emit{ text = shown, channel = "dialogue", id = text.hash(string.format("%08X", start) .. shown) }
    end
end)
