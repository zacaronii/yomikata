-- reader: title ファイナルファンタジーVI (Final Fantasy VI, Super Famicom)
-- reader: reads each page of the message window from the cartridge, where the game's pointer has walked
-- reader: version SFC-0ACC8D36
-- reader: build 0acc8d36
-- reader: no hooks
-- reader: no layout the window's place isn't looked into

-- How the game keeps what it shows.
--
-- The text is never copied into work RAM. A 24-bit pointer at POINTER (low byte first) walks the script in the
-- cartridge as a page types, and this script reads the cartridge where the game does (0xC00000 on, HiROM: bank
-- 0xC0 is the file's first 64 KB). A host that doesn't give it gets nothing, and the script then says nothing.
--
-- The script's codes: 0x12 begins a page (a 0x00 after it); 0x01 a new row; 0x16 and a byte a timed wait; 0x11 a
-- wait for the button; 0x14 and a byte a command; 0xFF a space. 0x1C-0x1F and the byte after are a kanji (the
-- table gives them as four hex digits); any other byte is a kana, a sign or a word of the game's dictionary.
--
-- A page is said when the pointer has stood STILL frames just past a wait (the timed one or the button's): its
-- text is read from the page's start, found back from the pointer at its 0x12, to the wait. Each page once,
-- until another comes.
--
-- Not covered: who speaks (the game writes a name into the text, which is read as it stands), menus, a fight's
-- messages (another way of text), the opening's lines over the title. A code the table hasn't got comes out as
-- U+FFFD.

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
local POINTER = 0x7E00C9          -- low, middle, bank
local CART = 0xC00000
local PAGE, ROW, TIMED, BUTTON, COMMAND, SPACE, ENDS = 0x12, 0x01, 0x16, 0x11, 0x14, 0xFF, 0x00
local KANJI_FIRST, KANJI_LAST = 0x1C, 0x1F
local BACK = 0x400                -- how far back a page's start is looked for
local STILL = 20

-- The build: the cartridge's header (its title and the bytes to its checksum).
if mem.read(0xC0FFC0, 32) ~= "FINAL FANTASY 6      1\x02\x0C\x03\x00\xC3\x00\x8D^r\xA1" then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("SFC-0ACC8D36.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

--- Whether the byte at `k` (1-based in `bytes`) is the second of a two-byte code: a kanji's, a wait's or a
--- command's. Followed back as far as the run of such first bytes goes.
local function second(bytes, k)
    local n = 0
    local j = k - 1
    while j >= 1 do
        local b = bytes:byte(j)
        if (b >= KANJI_FIRST and b <= KANJI_LAST) or b == TIMED or b == COMMAND then
            n = n + 1
            j = j - 1
        else
            break
        end
    end
    return n % 2 == 1
end

--- The page that ends at `at` (the pointer, just past its wait): its text. Read back no further than `begin`,
--- where the pointer jumped to when the message began (what lies before a message isn't text: bank 0xCD opens
--- with a table of every byte).
local function page_at(at, begin)
    local from = math.max(CART, at - BACK, begin or 0)
    local bytes = mem.read(from, at - from)
    if not bytes or #bytes < 2 then
        return nil
    end
    local last = #bytes
    -- Just past a wait (the timed one's two bytes, or the button's and the byte after it), or on the message's
    -- end (0x00), where its last page stands.
    local b1, b2 = bytes:byte(last - 1), bytes:byte(last)
    local ends = mem.u8(at) == ENDS and not second(bytes .. "\0", last + 1)
    if not ends and not ((b1 == TIMED and not second(bytes, last - 1)) or (b1 == BUTTON and not second(bytes, last - 1))
            or (b2 == BUTTON and not second(bytes, last))) then
        return nil
    end
    -- Back to the page's start: after the last 0x12 that is a code of its own, or, on a message's first page,
    -- after the 0x00 that ended the message before it.
    local start = nil
    for k = (ends and last or last - 2), 1, -1 do
        local b = bytes:byte(k)
        if (b == PAGE or b == ENDS) and not second(bytes, k) then
            start = k + 1
            break
        end
    end
    if not start and begin and from == begin then
        start = 1                       -- the message's first page: from where the pointer jumped to
    end
    if not start then
        return nil
    end
    local rows, row, k = {}, {}, start
    while k <= last do
        local b = bytes:byte(k)
        if b >= KANJI_FIRST and b <= KANJI_LAST and k < last then
            row[#row + 1] = characters[bytes:sub(k, k + 1)] or "\u{FFFD}"
            k = k + 2
        elseif b == TIMED or b == COMMAND then
            k = k + 2
        elseif b == BUTTON then
            break
        elseif b == ROW then
            rows[#rows + 1] = table.concat(row)
            row = {}
            k = k + 1
        elseif b == 0x00 then
            k = k + 1
        else
            row[#row + 1] = characters[string.char(b)] or "\u{FFFD}"
            k = k + 1
        end
    end
    rows[#rows + 1] = table.concat(row)
    for n, line in ipairs(rows) do
        rows[n] = line:gsub("　+$", "")
    end
    while #rows > 0 and rows[#rows] == "" do
        rows[#rows] = nil
    end
    return table.concat(rows, "\n")
end

local said, last, stood, begin = nil, nil, 0, nil
local JUMP = 16                   -- a pointer that moves back, or on further than this, has begun a message

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood, begin = nil, nil, 0, nil
end)

reader.on_frame(function()
    local p = mem.read(POINTER, 3)
    if not p or #p < 3 then
        return
    end
    local at = p:byte(3) * 0x10000 + p:byte(2) * 0x100 + p:byte(1)
    if last and at ~= last and (at < last or at > last + JUMP) then
        begin = at - 1                  -- the pointer lands past a message's first byte (its 「, most often)
    end
    stood = at == last and stood + 1 or 0
    last = at
    if stood ~= STILL or at < CART then
        return
    end
    local page = page_at(at, begin)
    if page and page ~= "" and page ~= said then
        said = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end)
