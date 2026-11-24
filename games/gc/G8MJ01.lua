-- reader: title ペーパーマリオRPG (Paper Mario: The Thousand-Year Door)
-- reader: reads speech bubbles and system messages a page at a time, with their furigana, and a choice's answers
-- reader: version Japan, disc version 0
-- reader: build G8MJ01-00
-- reader: no hooks
-- reader: no layout each character's place in its window is known, the window's place on screen isn't checked yet

-- How the game keeps what it shows.
--
-- The game has seven window slots (WINDOWS points at them). A slot in use has a list of records, one for each
-- thing the window draws in order: a character as its number in the game's font, with where it goes and how
-- large; or a code: the end of a line, a wait for a press, the scroll to the next page, a colour, the end.
-- The message's own text, with its tags, isn't kept once the list is made.
--
-- The list's header counts how many records the window has got through, so the page on screen is the one that
-- count stands in. A page is emitted when the window reaches it, whole: its characters are all in the list
-- before they type out.
--
-- The font (FONT points at it) is in Nintendo's BFN form, whose MAP1 parts say which Shift-JIS code each
-- number is. They are read back here, from memory, into a table of number to character.
--
-- Furigana are characters drawn small, listed right after the word they stand over. Which characters that
-- word is isn't said: it is taken to be the kanji just before, as far back as the small characters reach.
--
-- Not covered: narration (the opening book) and anything not drawn by a window (menus, their help line, names
-- in battle); pictures inside a message (button icons) are left out of its text; which answer the cursor is on.

local WINDOWS = 0x80418808 -- word: the window slots
local FONT = 0x80418758    -- word: the font file in memory
local SLOTS, SLOT_SIZE = 7, 0x48
local MEM1, MEM1_END = 0x80000000, 0x81800000

-- A slot.
local SLOT_FLAGS, SLOT_TYPE, SLOT_DATA = 0x02, 0x08, 0x28
local IN_USE = 1
local TYPE_SELECT = 5
-- A window's data: the header, then the records.
local DATA_AT, DATA_STARTED, DATA_COUNT, DATA_RECORDS = 0x10, 0x18, 0x38, 0x3C
local RECORD_SIZE, MAX_RECORDS = 0x18, 1298
local RECORD = ">I4 I2 i2 i2 xx I4 f xxxx" -- flags, glyph, x, y, when it appears, scale
-- Codes among the records.
local END_OF_LINE, NEXT_PAGE = 0xFFFB, 0xFFFD
local FIRST_CODE = 0xFF00

if mem.read(0x80000000, 6) ~= "G8MJ01" or mem.u8(0x80000007) ~= 0
    or mem.u32be(0x80073C00) ~= 0x80CD14F8 or mem.u32be(0x8007EF18) ~= 0x83ED15A8 then
    error("this script is for another build of the game", 0)
end

local function pointer(address)
    local value = mem.u32be(address)
    if value and value >= MEM1 and value < MEM1_END then
        return value
    end
end

-- ---- The font: number to character ----

local font_at, characters, kanji = nil, {}, {}

local function add(glyph, code)
    if characters[glyph] == nil then
        characters[glyph] = text.sjis(code > 0xFF and string.pack(">I2", code) or string.char(code))
        kanji[glyph] = code >= 0x889F or code == 0x8158 -- the kanji, and the mark that repeats one
    end
end

--- Reads the font's MAP1 parts, each a stretch of codes and a way to their numbers.
local function read_font(font)
    characters, kanji = {}, {}
    if mem.read(font, 8) ~= "FONTbfn1" then
        return false
    end
    local at = font + 0x20
    for _ = 1, math.min(mem.u32be(font + 0x0C) or 0, 64) do
        local size = mem.u32be(at + 4)
        if not size or size < 8 then
            break
        end
        if mem.read(at, 4) == "MAP1" then
            local method, first, last, entries = string.unpack(">I2 I2 I2 I2", mem.read(at + 8, 8))
            if method == 0 then
                for code = first, last do
                    add(code - first, code)
                end
            elseif method == 1 then
                local base = entries == 0 and 0x2BE or (mem.u16be(at + 0x10) or 0) - 0x5E
                for code = first, last do
                    local low = code & 0xFF
                    add(((code >> 8) - 0x88) * 188 + base + low - (low >= 0x80 and 0x41 or 0x40), code)
                end
            elseif method == 2 then
                local table_bytes = mem.read(at + 0x10, 2 * (last - first + 1))
                for i = 0, #table_bytes // 2 - 1 do
                    add(string.unpack(">I2", table_bytes, 2 * i + 1), first + i)
                end
            elseif method == 3 then
                local pairs_bytes = mem.read(at + 0x10, 4 * entries)
                for i = 0, #pairs_bytes // 4 - 1 do
                    local code, glyph = string.unpack(">I2 I2", pairs_bytes, 4 * i + 1)
                    add(glyph, code)
                end
            end
        end
        at = at + size
    end
    return next(characters) ~= nil
end

-- ---- A window's records, as pages ----

--- The pages of a list of records: { {text = …, ruby = …}, … }, an empty text for a page that draws none.
local function read_pages(records, count, select)
    local pages = {}
    local parts, length, line = {}, 0, {} -- the page so far; its length in code points; this line's characters
    local small, small_x = {}, nil        -- furigana waiting for the end of their run
    local spans = {}

    local function close_small()
        if #small == 0 then
            return
        end
        -- The word they stand over: the kanji at the end of the line, back to where the furigana begin.
        local first = #line + 1
        while first > 1 and line[first - 1].kanji and line[first - 1].right > small_x do
            first = first - 1
        end
        if first <= #line then
            spans[#spans + 1] = { line[first].at, #line - first + 1, table.concat(small) }
        end
        small, small_x = {}, nil
    end
    local function close_line()
        close_small()
        line = {}
    end
    local function close_page()
        close_line()
        local shown = table.concat(parts)
        local trimmed = shown:gsub("\n+$", "")
        pages[#pages + 1] = { text = trimmed, ruby = spans }
        parts, length, spans = {}, 0, {}
    end

    for i = 0, count - 1 do
        local _, glyph, x, _, _, scale = string.unpack(RECORD, records, i * RECORD_SIZE + 1)
        if glyph < FIRST_CODE then
            local character = characters[glyph]
            if character then
                if scale < 0.9 then
                    small[#small + 1] = character
                    small_x = small_x or x
                else
                    close_small()
                    if #line > 0 then
                        line[#line].right = math.min(line[#line].right, x)
                    end
                    line[#line + 1] = { at = length, kanji = kanji[glyph], right = x + 22 }
                    parts[#parts + 1] = character
                    length = length + 1
                end
            end
        elseif glyph == END_OF_LINE then
            close_line()
            parts[#parts + 1] = "\n"
            length = length + 1
        elseif glyph == NEXT_PAGE and not select then
            close_page()
        end
    end
    close_page()
    return pages
end

--- How many pages the window has left behind: the scrolls among the records it has got through.
local function page_number(records, at)
    local number = 0
    for i = 0, at - 1 do
        if string.unpack(">I2", records, i * RECORD_SIZE + 5) == NEXT_PAGE then
            number = number + 1
        end
    end
    return number
end

-- ---- Each frame ----

local messages = {} -- per slot: the message whose records were read, and its pages

reader.on_state_loaded(function()
    reader.forget()
    messages = {}
end)

reader.on_frame(function()
    local slots = pointer(WINDOWS)
    local font = pointer(FONT)
    if not slots or not font then
        return
    end
    if font ~= font_at then
        font_at = read_font(font) and font or nil
        if not font_at then
            return
        end
    end
    for slot = 0, SLOTS - 1 do
        local entry = slots + slot * SLOT_SIZE
        local holder = (mem.u16be(entry + SLOT_FLAGS) or 0) & IN_USE ~= 0 and pointer(entry + SLOT_DATA)
        local data = holder and pointer(holder)
        local count = data and mem.s32be(data + DATA_COUNT)
        if not count or count < 1 or count > MAX_RECORDS then
            messages[slot] = nil
            reader.forget(slot)
        else
            local select = mem.s32be(entry + SLOT_TYPE) == TYPE_SELECT
            -- The same data at the same place can be another message: the time it began tells them apart.
            local key = data .. ":" .. (mem.u64be(data + DATA_STARTED) or 0) .. ":" .. count
            local message = messages[slot]
            if not message or message.key ~= key then
                local records = mem.read(data + DATA_RECORDS, count * RECORD_SIZE)
                if #records == count * RECORD_SIZE then
                    message = { key = key, records = records, pages = read_pages(records, count, select) }
                    messages[slot] = message
                else
                    message = nil
                end
            end
            if message then
                local at = math.max(0, math.min(mem.s32be(data + DATA_AT) or 0, count))
                local number = select and 0 or page_number(message.records, at)
                local page = message.pages[number + 1]
                if page and page.text ~= "" and reader.changed(slot, key .. ":" .. number) then
                    reader.emit{
                        text = page.text,
                        ruby = page.ruby,
                        channel = select and "choice" or "dialogue",
                        id = text.hash(page.text),
                        meta = { page = number + 1, pages = #message.pages },
                    }
                end
            end
        end
    end
end)
