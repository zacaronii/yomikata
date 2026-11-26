-- reader: title ライブ・ア・ライブ (Live A Live)
-- reader: reads a message window's text as the game draws it, a text for each wait
-- reader: version SFC-A07624CD
-- reader: build a07624cd
-- reader: no hooks
-- reader: no layout where the window is drawn isn't looked into

-- How the game keeps what it shows.
--
-- No message is ever in work RAM. The story's script is in the cartridge, and the game draws a message a
-- character a frame straight from it: AT is where in the script it has got to (16 bits; BANK says which bank
-- of the cartridge), and COUNT how many characters of the window it has drawn. So what a window gained is
-- the script's bytes that AT has passed, and it is said when AT stands: the game is waiting for a button.
--
-- The codes are the game's own, a byte each but for kanji:
--   0xB1 on      hiragana: あ to わ, ん, the small ones, the voiced (が to ど, ぱ to ぽ, ば to ぼ); を is 0xB0
--   0x61-0xAF    katakana, the same less 0x50
--   0x1A-0x1F    begin a kanji of two bytes: the table beside this script
--   0x30-0x39, 0x41-0x5A  digits and capitals, as ASCII has them
--   0x01         a new row; 0x20 a space; 0x00 and 0x02 wait for a button; 0x05 comes before a question's
--                answers (they are given as rows of the text, not as a choice)
--   others below 0x61: signs, and a sign with a wait after it, by the table (0x5B opens a speech, 0x0F is a
--   full stop and a wait); 0x14 and 0x19 stand for a name the chapter gives them
--
-- This script reads the cartridge, at the addresses the game itself uses for it (0xC00000 on). A host that
-- gives a script work RAM only can't run it: it says so, and reads nothing.
--
-- The build: a table the game writes into work RAM as it starts is checked, there from the first second on.
--
-- Not covered: who speaks (the window's shape and tail show it, no name); menus, fights, the chapter select.

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
local AT = 0x7E01C6 -- u16: where in the script the game has got to
local BANK = 0x7E01C3 -- u8: the bank of the cartridge the script is in
local COUNT = 0x7E01D0 -- u16: characters of the window drawn so far
local STILL = 4 -- ticks AT stands before what was gathered is said
local MOST = 512 -- bytes of script in one text
local WAITS = { [0x00] = true, [0x02] = true } -- codes that wait for a button and show nothing

if mem.read(0x7F7618, 16) ~= "\x11\x01\x29\x01\x2F\x01\x35\x01\x3B\x01\x50\x01\x62\x01\x77\x01" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("SFC-A07624CD.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

local HIRAGANA = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわん"
    .. "ゃゅょっぁぃぅぇぉ" .. "がぎぐげござじずぜぞだぢづでど" .. "ぱぴぷぺぽ" .. "ばびぶべぼ"

--- A hiragana (0xB1 on) or a katakana (the same less 0x50) by its code, or nil.
local function kana(code)
    local katakana = code < 0xB1
    local at = code - (katakana and 0x61 or 0xB1)
    local from = at >= 0 and utf8.offset(HIRAGANA, at + 1)
    local to = from and utf8.offset(HIRAGANA, at + 2)
    if not to then
        return nil
    end
    local ch = HIRAGANA:sub(from, to - 1)
    return katakana and utf8.char(utf8.codepoint(ch) + 0x60) or ch
end

local from_id = 0
local function UNKNOWN(code)
    return "\u{FFFD}"
end

--- A stretch of the script as text. A code the table has is what the table says (it may be nothing: a wait);
--- 0x1A to 0x1F begin a kanji of two bytes; letters and digits are as ASCII has them; the rest are kana.
local function decode(bytes)
    local out, i = {}, 1
    while i <= #bytes do
        local b = bytes:byte(i)
        local one = bytes:sub(i, i)
        if b >= 0x1A and b <= 0x1F and i < #bytes then
            out[#out + 1] = characters[bytes:sub(i, i + 1)] or UNKNOWN(bytes:sub(i, i + 1))
            i = i + 2
        else
            if characters[one] then
                out[#out + 1] = characters[one]
            elseif WAITS[b] then
                out[#out + 1] = ""
            elseif b == 0x01 or b == 0x05 then
                out[#out + 1] = "\n"
            elseif b == 0x20 then
                out[#out + 1] = "\u{3000}"
            elseif (b >= 0x30 and b <= 0x39) or (b >= 0x41 and b <= 0x5A) then
                out[#out + 1] = utf8.char(0xFF00 + b - 0x20)
            else
                out[#out + 1] = (b >= 0x61 and kana(b)) or UNKNOWN(one)
            end
            i = i + 1
        end
    end
    -- The spaces a window's rows begin with are its margin, and those at a text's end are nothing.
    local shown = table.concat(out):gsub("\n\u{3000}+", "\n")
    while shown:sub(1, 3) == "\u{3000}" do
        shown = shown:sub(4)
    end
    while shown:sub(-3) == "\u{3000}" or shown:sub(-1) == "\n" do
        shown = shown:sub(1, shown:sub(-1) == "\n" and -2 or -4)
    end
    return shown
end

local from, before, stood, count = nil, nil, 0, nil -- where the text being gathered began; AT a tick ago

reader.on_state_loaded(function()
    reader.forget()
    from, before, stood, count = nil, nil, 0, nil
end)

reader.on_frame(function()
    local at, bank, drawn = mem.u16(AT), mem.u8(BANK), mem.u16(COUNT)
    if not at or not bank or bank < 0xC0 then
        return
    end
    if before == nil then
        before, count = at, drawn
        return
    end
    if at == before then
        stood = stood + 1
        if stood == STILL and from and at > from and at - from <= MOST then
            local bytes = mem.read(bank * 0x10000 + from, at - from)
            from = nil
            if #bytes == 0 then
                reader.warn("this host gives a script no cartridge to read: nothing can be read of this game")
                return
            end
            local shown = decode(bytes)
            if shown:find("[^%s\u{3000}]") then
                reader.emit{ text = shown, channel = "dialogue", id = text.hash(bank .. ":" .. from_id .. bytes),
                             meta = { bank = bank, at = from_id } }
            end
        end
        return
    end
    -- AT moved. Forward by a little while characters are drawn: the text goes on, or begins here. Anywhere
    -- else (a jump in the script, a word kept outside it): what was gathered stays, and nothing is added.
    if at > before and at - before <= 8 and drawn ~= count then
        if not from then
            from, from_id = before, before
        end
    elseif at < before or at - before > 64 then
        if at >= 0x8000 and not (from and at > from and at - from <= MOST) then
            from = nil
        end
    end
    before, stood, count = at, 0, drawn
end)
