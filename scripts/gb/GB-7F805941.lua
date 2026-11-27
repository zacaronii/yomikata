-- reader: title カエルの為に鐘は鳴る (Kaeru no Tame ni Kane wa Naru, Game Boy)
-- reader: reads a page of the message window or a speech bubble once it has typed out
-- reader: version GB-7F805941
-- reader: build 7f805941
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The game's text lies packed in the cartridge. A page is unpacked whole into work RAM before it types: PAGE
-- holds its two rows of 18 cells, each a number of the page's own (0, 1, 2, ... in the order the page's
-- characters first come, 0x54 a blank), and LIST holds, for each of those numbers in turn, the character's code
-- in the game's font (two bytes, big-endian, 0xFFFF after the last). The table beside this script says which
-- character a code is. The page is then drawn a character at a time into fresh tiles, its font proportional;
-- WHERE is the tile map's place the next character goes (two bytes, big-endian), and stands once it has typed.
--
-- So a page is said once it has begun to type (WHERE moved after the page changed) and the page, its list and
-- WHERE have stood the same for STILL frames: the rows read through the list, blanks at a row's end left out.
-- A list without its end, or a cell number it hasn't got, isn't a page. The byte after WHERE is 1 while a
-- page's characters are drawn; a page of big letters (a laugh in the opening) is drawn otherwise (0x40) and
-- from other cells, and isn't read. Each page is said once, until another comes.
--
-- Not covered: who speaks (a bubble's place, not a name), pages of big letters, the naming screen, menus and
-- the status rows, the screens before the story. A character the table hasn't got comes out as U+FFFD.

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
local PAGE, WIDTH, ROWS = 0xCAC2, 18, 2 -- the page's cells: two rows of 18
local LIST, MOST = 0xC4EE, 64             -- each cell number's code, big-endian; numbers at most
local BLANK, ENDS = 0x54, 0xFFFF
local WHERE = 0xC280                      -- the tile map's place the next character goes, big-endian
local KIND, TYPING = 0xC282, 0x01         -- 1 while a page's characters are drawn (0x40 for big letters)
local STILL = 8                           -- frames all of it stands before a page is said

-- The build: the cartridge's header, from its title to its checksum.
if mem.read(0x0134, 26) ~= "KAERUNOTAMENI\0\0\0\0\0\0\x03\x04\x02\x00\x01\x00\x0A" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("GB-7F805941.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

--- The page's text: each row's cells through the list, blanks as wide spaces, those at a row's end left out.
local function page_text()
    local cells, list = mem.read(PAGE, WIDTH * ROWS), mem.read(LIST, 2 * MOST)
    if not cells or #cells < WIDTH * ROWS or not list or #list < 2 * MOST then
        return nil
    end
    local codes, count = {}, nil
    for k = 0, MOST - 1 do
        local code = list:sub(2 * k + 1, 2 * k + 2)
        if code == "\xFF\xFF" then
            count = k
            break
        end
        codes[k] = code
    end
    if not count then
        return nil -- no list's end: not a page (the screens before the story leave other bytes here)
    end
    for i = 1, #cells do
        local n = cells:byte(i)
        if n ~= BLANK and n >= count then
            return nil -- a number the list hasn't got: not a page
        end
    end
    local rows = {}
    for row = 0, ROWS - 1 do
        local out = {}
        for col = 1, WIDTH do
            local n = cells:byte(row * WIDTH + col)
            if n == BLANK then
                out[#out + 1] = "　"
            else
                local code = codes[n]
                out[#out + 1] = code and characters[code] or "\u{FFFD}"
            end
        end
        while #out > 0 and out[#out] == "　" do
            out[#out] = nil
        end
        rows[#rows + 1] = table.concat(out)
    end
    while #rows > 0 and rows[#rows] == "" do
        rows[#rows] = nil
    end
    return table.concat(rows, "\n")
end

local said, last, stood = nil, nil, 0
local page_was, where_was, typed = nil, nil, false

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
    page_was, where_was, typed = nil, nil, false
end)

reader.on_frame(function()
    local cells, list, where = mem.read(PAGE, WIDTH * ROWS), mem.read(LIST, 2 * MOST), mem.read(WHERE, 2)
    if not cells or not list or not where then
        return
    end
    -- A page counts once it has begun to type: WHERE moves after the page changed.
    local page_now = cells .. list
    if page_now ~= page_was then
        typed = false
    elseif where ~= where_was then
        typed = true
    end
    page_was, where_was = page_now, where
    local now = cells .. list .. where
    stood = now == last and stood + 1 or 0
    last = now
    if stood < STILL or not typed or mem.u8(KIND) ~= TYPING then
        return
    end
    local page = page_text()
    if page and page:find("%S") and page ~= said then
        said = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end)
