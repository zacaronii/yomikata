-- reader: title ぼくのなつやすみ (Boku no Natsuyasumi)
-- reader: reads the text panel's page; the speaker's name is part of the text
-- reader: version SCPS-10088
-- reader: build 959ddf98806648a689eeafaaabbff2a4ec0bfbbb
-- reader: no hooks
-- reader: no layout where the panel draws its characters isn't looked into

-- How the game keeps its text.
--
-- PAGE points at the page shown in the text panel (at the right of the screen, written downwards), inside
-- the scene's script, and is 0 while no text is up. The text is 16-bit codes numbering the glyphs of the
-- game's own font (the table beside this script). 0x8001 is a new line; a page runs to 0x8000, which ends the
-- text, or to 0x8002, which ends the page. Rows after the first start with a wide space, dropped here.
--
-- Talk while walking around, choices and menus weren't checked. Menus, lists and the few Shift-JIS system
-- messages aren't read. Six glyphs aren't in the table and read as U+FFFD.

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

local PAGE = 0x800359EC -- word: the page shown, or 0
local LONGEST = 512
local END_OF_TEXT, END_OF_PAGE = "\x00\x80", "\x02\x80"
local RAM, RAM_END = 0x80000000, 0x80200000
local WIDE_SPACE = "\u{3000}"

-- Three calls in the game's code.
if mem.u32(0x8001E6B8) ~= 0x0C014DA1 or mem.u32(0x8003AF74) ~= 0x0C005779 or mem.u32(0x8004E718) ~= 0x0C0138C7 then
    error("this script is for another build of the game", 0)
end

local source = reader.data("tbl")
if not source then
    error(reader.game_id .. ".tbl is missing: the game's character table", 0)
end
local glyphs = tbl.parse(source)

--- The codes from the pointer to the page's end.
local function page_of(bytes)
    for i = 1, #bytes - 1, 2 do
        local code = bytes:sub(i, i + 1)
        if code == END_OF_TEXT or code == END_OF_PAGE then
            return bytes:sub(1, i - 1)
        end
    end
    return bytes:sub(1, #bytes - #bytes % 2)
end

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    local at = mem.u32(PAGE)
    if not at or at < RAM or at >= RAM_END then
        reader.forget("page")
        return
    end
    local page = page_of(mem.read(at, LONGEST))
    if page == "" or not reader.changed("page", at .. ":" .. page) then
        return
    end
    local lines = {}
    for line in (tbl.decode(page, glyphs, 2):gsub("\n+$", "") .. "\n"):gmatch("(.-)\n") do
        lines[#lines + 1] = line:sub(1, #WIDE_SPACE) == WIDE_SPACE and line:sub(#WIDE_SPACE + 1) or line
    end
    local shown = table.concat(lines, "\n")
    if shown ~= "" then
        reader.emit{ text = shown, id = text.hash(page) }
    end
end)
