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

-- Where a scene still waits in the map the party is on (docs/script-api.md, "Events"), for the harness to walk
-- to. Each map's event places are in the cartridge: a table of 2-byte offsets into bank 0xC4 (TRIGGERS, a map's
-- number at MAP), each place 5 bytes (x and y in the party's tile numbers, then the event's address from
-- 0xCA0000). An event that is done is guarded at its start by "if flag n is set, return" (0xC0, n with its top
-- bit set, and an address that holds 0xFE); the flags are bits from FLAGS. A place is given while its guard's
-- flag is clear, or when it has no such guard, unless its event only takes the party to another map (0x6A or
-- 0x6B first): a door, which expedition finds by itself.
local MAP, TRIGGERS, EVENTS, FLAGS = 0x7E1F64, 0xC40000, 0xCA0000, 0x7E1E80
local GUARD, RETURN, DOORS = 0xC0, 0xFE, { [0x6A] = true, [0x6B] = true }

function events()
    local map = mem.u16(MAP)
    if not map then
        return {}
    end
    map = map & 0x1FF
    local from, to = mem.u16(TRIGGERS + 2 * map), mem.u16(TRIGGERS + 2 * map + 2)
    local out = {}
    if not from or not to then
        return out
    end
    for k = from, to - 5, 5 do
        local at = TRIGGERS + k
        local x, y = mem.u8(at), mem.u8(at + 1)
        local event = EVENTS + mem.u8(at + 2) + 256 * mem.u8(at + 3) + 65536 * mem.u8(at + 4)
        local body, waits = event, true
        if mem.u8(event) == GUARD then
            local n = mem.u16(event + 1)
            local target = EVENTS + mem.u8(event + 3) + 256 * mem.u8(event + 4) + 65536 * mem.u8(event + 5)
            if n & 0x8000 ~= 0 and mem.u8(target) == RETURN then
                local bit = n & 0x7FFF
                waits = (mem.u8(FLAGS + (bit >> 3)) >> (bit & 7)) & 1 == 0
            end
            body = event + 6
        end
        if waits and not DOORS[mem.u8(body)] then
            out[#out + 1] = { x, y }
        end
    end
    return out
end
