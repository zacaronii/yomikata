-- reader: title ゼルダの伝説 風のタクト (Zelda no Densetsu: Kaze no Takt)
-- reader: reads the message window's page each time it changes, with the game's own furigana kept on their words
-- reader: version GZLJ01
-- reader: build GZLJ01-00
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The page on screen is in a buffer in main memory, in Shift-JIS: the base text only, a row a line (0x0A), with
-- escapes (0x1B, two letters and a value in brackets: "\x1BCR[54]", where a row starts) that are left out, ended by
-- a zero byte. A talk's window is an object a static word (TALK) points at, the page's text at the word TEXT_AT
-- into it. Some talks (the villagers') show in a second window instead: its object is in the slot SECOND bytes on
-- from TALK, set only while it is up, and the word LINES into it points at the page (a buffer whose header begins
-- "HM", 16 bytes before the text; its empty rows at the end are left out). Found with `yomikata state-text`.
-- The opening's narration has its own buffer: a static word (STORY) points PAGE bytes before it, and its
-- first word is always the same (STORY_KIND), the text four bytes further on.
--
-- The furigana aren't in those buffers: the game draws them from another, by place. But the whole message file
-- (zel_00.bmg, read from the disc at power-on) stays in memory, a static word (BMG) pointing at it, and there each
-- message keeps its furigana with the word they stand over: an escape 0x1A, its length, FF 00 02, how many
-- characters of base follow, then the reading. So the file is read once into an index of each message's base
-- bytes and its furigana, a page's bytes are looked for in it, and the page is said with the furigana on their
-- words (reader.builder). A page not found (a name put in, a number) is said without furigana.
--
-- A page is said once its buffer has stood the same for STILL frames and differs from the last said. The index is
-- read a few hundred messages a frame (about 15 frames in all), and nothing is said until it is whole.
--
-- Not covered: who speaks (the game shows no name on the window), a choice's answers and its cursor, the menus,
-- signs and item screens that use other windows.

local TALK, TEXT_AT = 0x803AE9A0, 0x90
local SECOND, LINES = 0xC, 0xD80
local STORY, PAGE, STORY_KIND = 0x803FFFAC, 0xE4, 0x80387E24
local BMG = 0x803EA4DC
local LOW, HIGH = 0x80000000, 0x81800000
local STILL = 20

-- The build: the disc's id, which the console puts at the start of main memory.
if mem.read(0x80000000, 6) ~= "GZLJ01" then
    error("this script is for another build of the game", 0)
end

local function inside(at)
    return at and at >= LOW and at < HIGH
end

--- Whether Shift-JIS byte `b` begins a character of two bytes.
local function lead(b)
    return (b >= 0x81 and b <= 0x9F) or (b >= 0xE0 and b <= 0xFC)
end

--- The page in the window now, as Shift-JIS bytes without the escapes; nil when no window has one.
local function page_bytes()
    local raw = nil
    local talk = mem.u32be(TALK)
    if inside(talk) then
        local at = mem.u32be(talk + TEXT_AT)
        if inside(at) then
            raw = mem.cstr(at, 0x400)
        end
    end
    if not raw or raw == "" then
        local second = mem.u32be(TALK + SECOND)
        if inside(second) then
            local at = mem.u32be(second + LINES)
            if inside(at) and mem.read(at - 16, 2) == "HM" then
                raw = mem.cstr(at, 0x400)
                raw = raw and (raw:gsub("\27%u%u%[%x*%]", ""):gsub("\n+$", ""))
            end
        end
    end
    if not raw or raw == "" then
        local story = mem.u32be(STORY)
        if inside(story) and mem.u32be(story + PAGE) == STORY_KIND then
            raw = mem.cstr(story + PAGE + 4, 0x400)
        end
    end
    if not raw or raw == "" then
        return nil
    end
    return (raw:gsub("\27%u%u%[%x*%]", ""))
end

local index = nil -- { { base = bytes, ruby = { { at = byte, chars = n, reading = bytes }, ... } }, ... }
local building = nil -- the file being read into the index, a few hundred messages a frame
local EACH = 300

--- A message's base bytes (no escapes, no rows' breaks) and its furigana, from its text at `i` in `file`. Steps
--- from escape to escape (an escape's own bytes may be zero; the message ends at a zero outside them): neither
--- 0x1A nor 0x0A nor 0x00 is ever the second byte of a Shift-JIS character.
local function read_message(file, i)
    local base, ruby, length = {}, {}, 0
    while i <= #file do
        local at = file:find("[%z\26]", i) or #file + 1
        local piece = file:sub(i, at - 1):gsub("\n", "")
        base[#base + 1] = piece
        length = length + #piece
        if at > #file or file:byte(at) == 0 then
            break
        end
        local len = math.max(file:byte(at + 1) or 2, 2)
        if file:sub(at + 2, at + 4) == "\255\0\2" then
            ruby[#ruby + 1] = { at = length + 1, chars = file:byte(at + 5), reading = file:sub(at + 6, at + len - 1) }
        end
        i = at + len
    end
    return { base = table.concat(base), ruby = ruby }
end

--- Reads the next messages of the file into the index; true once the index is whole.
local function build_index()
    if index then
        return true
    end
    if not building then
        local bmg = mem.u32be(BMG)
        if not inside(bmg) or mem.read(bmg, 8) ~= "MESGbmg1" then
            return false
        end
        local size = mem.u32be(bmg + 8) * 32 -- in blocks of 32 bytes
        local file = mem.read(bmg, size)
        if not file or #file < size then
            return false
        end
        local inf, dat, at = nil, nil, 0x21
        while at + 7 <= #file do
            local tag, len = file:sub(at, at + 3), string.unpack(">I4", file, at + 4)
            if tag == "INF1" then
                inf = at
            elseif tag == "DAT1" then
                dat = at + 8
            end
            if len == 0 then
                break
            end
            at = at + len
        end
        if not inf or not dat then
            return false
        end
        local count, each = string.unpack(">I2I2", file, inf + 8)
        building = { file = file, inf = inf, dat = dat, count = count, each = each, next = 0, out = {} }
    end
    local w = building
    for k = w.next, math.min(w.next + EACH, w.count) - 1 do
        w.out[k + 1] = read_message(w.file, w.dat + string.unpack(">I4", w.file, w.inf + 16 + k * w.each))
    end
    w.next = math.min(w.next + EACH, w.count)
    if w.next >= w.count then
        index, building = w.out, nil
        return true
    end
    return false
end

--- The page through reader.builder, with the furigana of the message it is found in; nil when it isn't found.
local function with_furigana(page)
    if not index then
        return nil
    end
    local flat = page:gsub("\n", "")
    for _, m in ipairs(index) do
        local start = #flat > 0 and m.base:find(flat, 1, true)
        if start then
            local over = {} -- byte of the page without its rows' breaks where a word with furigana starts -> { chars, reading }
            for _, r in ipairs(m.ruby) do
                local from = r.at - start + 1
                if from >= 1 and from <= #flat then
                    over[from] = { r.chars, r.reading }
                end
            end
            -- The page again, its breaks kept: a word with furigana is its characters from where it starts.
            local b, i, f = reader.builder(), 1, 1
            while i <= #page do
                local r = over[f]
                if page:byte(i) == 0x0A then
                    b:add("\n")
                    i = i + 1
                elseif r then
                    local word, n = {}, 0
                    while n < r[1] and i <= #page and page:byte(i) ~= 0x0A do
                        local w = lead(page:byte(i)) and 2 or 1
                        word[#word + 1] = page:sub(i, i + w - 1)
                        i, f, n = i + w, f + w, n + 1
                    end
                    b:add_ruby(text.sjis(table.concat(word)), text.sjis(r[2]))
                else
                    local w = lead(page:byte(i)) and 2 or 1
                    b:add(text.sjis(page:sub(i, i + w - 1)))
                    i, f = i + w, f + w
                end
            end
            return b
        end
    end
    return nil
end

local said, last, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
end)

reader.on_frame(function()
    if not build_index() then
        return -- a page waits until the index is whole, so that it is said with its furigana
    end
    local page = page_bytes()
    if not page then
        return
    end
    stood = page == last and stood + 1 or 0
    last = page
    if stood ~= STILL or page == said then
        return
    end
    said = page
    local shown = text.sjis(page)
    -- A page of the game's text has no control characters but its rows' breaks, and every byte reads.
    if not shown:find("%S") or shown:find("[%z\1-\9\11-\31]") or shown:find("\u{FFFD}") then
        return
    end
    local b = with_furigana(page)
    if b then
        reader.emit{ text = b:text(), ruby = b:ruby(), channel = "dialogue", id = text.hash(page) }
    else
        reader.emit{ text = shown, channel = "dialogue", id = text.hash(page) }
    end
end)
