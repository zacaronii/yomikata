-- reader: title かまいたちの夜2 監獄島のわらべ唄 (Kamaitachi no Yoru 2: Kangoku-jima no Warabe-uta)
-- reader: reads the story's text as it is typed, a sentence at a time, with its furigana
-- reader: version SLPS-25135
-- reader: build SLPS-25135-30690da2
-- reader: no hooks
-- reader: no layout the text is drawn over the whole picture, and where isn't looked into

-- How the game keeps what it shows.
--
-- The story is a program of its own, loaded whole: commands of a few bytes, labels, and among them the text,
-- each piece Shift-JIS between a byte 0x02 and a byte 0x03. Nothing copies a piece anywhere as it is shown:
-- there is no line in memory but the program's own. What there is, is the place the program has got to: AT
-- points into it, and while a piece is typed AT walks through it, a character a frame, and then stands
-- after it while the game waits for a button. Pieces the program jumps over (it keeps a sentence in more
-- than one cut, and takes one) are never walked through.
--
-- So a piece is read when AT is seen inside it, or just after its end, and the pieces are put end to end.
-- What has gathered is said once AT has stood still: the game is waiting, and the reader has what the
-- player has. The picture fills with such sentences until its page is turned; each is said once, as it comes.
--
-- Between two pieces of one text a single byte may stand:
--   0x04        a new row
--   0x06, 0x07  before and after readings: the pieces between them are furigana, one for each of the first
--               characters of the piece that follows
--   0x0D, 0x0E  the name the player gave the hero, and the heroine
-- and "L", a byte and "!" change the colour inside a sentence; "e" and "U" with four bytes say where a row
-- begins and make a pause. Anything else between two pieces (a wait for a button among it) parts two texts.
--
-- A name isn't in the text. Typed out, it is a piece of its own somewhere else, filled up to twelve bytes with
-- "!", and AT walks through it like any other. Found already standing, it is known by its command, and the
-- names themselves are kept after AT (12 and 25 bytes on).
--
-- Not covered: choices; a piece of one character that a host which doesn't see every frame steps over;
-- menus and the memory card's messages.

local AT = 0x01F575C8 -- word: where the story's program has got to
local LOW, HIGH = 0x01000000, 0x02000000 -- the program lies somewhere in here
local STX, ETX, ROW, READINGS, READ = 0x02, 0x03, 0x04, 0x06, 0x07
local NAMES = { [0x0D] = 0x0C, [0x0E] = 0x19 } -- a name's command -> where the name is, from AT
local MOST = 600 -- bytes of a piece
local NEAR = 8 -- bytes from a piece's end to where AT stands after it
local STILL = 3 -- ticks AT stands before what has gathered is said

if mem.u32(0x00100084) ~= 0x0C074E50 or mem.u32(0x00175CF4) ~= 0x0C05D048 then
    error("this script is for another build of the game", 0)
end

--- The piece that ends with the 0x03 at place `e` of `bytes`: where its text begins there, or nil.
local function begins(bytes, e)
    local i = e - 1
    while i >= 1 and bytes:byte(i) >= 0x20 do
        i = i - 1
    end
    if i >= 1 and bytes:byte(i) == STX and i + 1 < e then
        return i + 1
    end
end

--- The piece of text that `at` is inside, or that ends within `near` bytes before it (NEAR unless given): where its text begins
--- and ends, and its bytes. Nil when `at` isn't at one. A piece has no byte below a space in it.
local function piece(at, near)
    local from = at - MOST
    local bytes = mem.read(from, 2 * MOST)
    if not bytes or #bytes < 2 * MOST then
        return nil
    end
    local here = MOST + 1 -- the byte at `at`
    -- Inside one: forward from here to its 0x03, and back from there to its 0x02.
    local e = here
    while e <= #bytes and bytes:byte(e) >= 0x20 do
        e = e + 1
    end
    local b = e <= #bytes and bytes:byte(e) == ETX and begins(bytes, e)
    if not b or b > here then
        -- Or just after one: its 0x03 a command or two back (a command's own bytes may be anything).
        b = nil
        for back = here - 1, here - (near or NEAR), -1 do
            if bytes:byte(back) == ETX then
                b, e = begins(bytes, back), back
                if b then
                    break
                end
            end
        end
    end
    if not b then
        return nil
    end
    return from + b - 1, from + e - 2, bytes:sub(b, e - 1)
end

--- What stands between a piece that ends at `last` and the next, which begins at `first`, when it is
--- commands that belong inside one text: how many new rows they make, and the name's command among them
--- if any. Nil when the two pieces aren't next to each other so, and after a wait: then they are two texts.
local function between(last, first)
    local size = first - last - 3
    if size < 0 or size > 24 then
        return nil
    end
    local gap = size > 0 and mem.read(last + 2, size) or ""
    if #gap ~= size then
        return nil
    end
    local rows, named, i = 0, nil, 1
    while i <= size do
        local b = gap:byte(i)
        if b == ROW then
            rows, i = rows + 1, i + 1
        elseif b == READINGS or b == READ then
            i = i + 1
        elseif NAMES[b] then
            named, i = b, i + 1
        elseif b == 0x4C and gap:byte(i + 2) == 0x21 then -- "L", a colour, "!"
            i = i + 3
        elseif (b == 0x65 or b == 0x55) and i + 4 <= size then -- "e" or "U" and four bytes: where the row begins, a pause
            i = i + 5
        else
            return nil
        end
    end
    return rows, named
end

--- Whether the piece from `first` to `last` is a reading: 0x06 stands before it or before the readings it
--- follows, and 0x07 after it or after those that follow it. (Either byte alone may be part of a command.)
local function reading(first, last)
    local bytes = mem.read(first - 65, 64) -- up to the byte before the 0x02 the piece begins with
    local i, opened = bytes and #bytes == 64 and 64, false
    while i and i >= 1 do
        local b = bytes:byte(i)
        if b == READINGS then
            opened = true
            break
        elseif b ~= ETX then
            break
        end
        i = i - 1
        while i >= 1 and bytes:byte(i) >= 0x20 do
            i = i - 1
        end
        if i < 1 or bytes:byte(i) ~= STX then
            break
        end
        i = i - 1
    end
    if not opened then
        return false
    end
    bytes = mem.read(last + 2, 96) or ""
    i = 1
    while i <= #bytes do
        local b = bytes:byte(i)
        if b == READ then
            return true
        elseif b ~= STX then
            return false
        end
        i = i + 1
        while i <= #bytes and bytes:byte(i) >= 0x20 do
            i = i + 1
        end
        if bytes:byte(i) ~= ETX then
            return false
        end
        i = i + 1
    end
    return false
end

--- The name a command puts into a sentence, as the player gave it.
local function name(command)
    local bytes = mem.cstr(AT + NAMES[command], 12)
    return bytes and text.sjis(bytes) or ""
end

local gathered = {} -- the pieces since the last text said: { first, last, text, reading }
local seen = {} -- where the pieces gathered or last said begin
local stood, before = 0, nil

reader.on_state_loaded(function()
    reader.forget()
    gathered, seen, stood, before = {}, {}, 0, nil
end)

--- Adds a piece's text to what is being built: its first characters under the readings waiting, if any.
local function add(built, shown, readings)
    local at = 1
    for _, over in ipairs(readings) do
        local next = utf8.offset(shown, 2, at)
        if not next then
            break
        end
        built:add_ruby(shown:sub(at, next - 1), over)
        at = next
    end
    built:add(shown:sub(at))
end

reader.on_frame(function()
    local at = mem.u32(AT)
    if not at or at < LOW or at >= HIGH then
        gathered, seen, stood, before = {}, {}, 0, nil
        return
    end
    local first, last, bytes = piece(at)
    if first and not seen[first] then
        -- A piece AT wasn't seen walking through (it was up before the script looked, or a press brought
        -- it up at once) may be the end of a text in several pieces: those just before it were shown with it.
        local found = { { first = first, last = last, bytes = bytes } }
        if at > last then
            for _ = 1, 24 do
                local f, l, b = piece(found[1].first - 1, 26)
                if not f or seen[f] or l >= found[1].first - 1 or not between(l, found[1].first) then
                    break
                end
                table.insert(found, 1, { first = f, last = l, bytes = b })
            end
        end
        for _, part in ipairs(found) do
            -- A name as it is typed out is filled up to twelve bytes with "!".
            local shown = text.sjis(#part.bytes == 12 and part.bytes:gsub("!+$", "") or part.bytes)
            if not shown:find("\u{FFFD}", 1, true) then
                if #gathered == 0 then
                    seen = {} -- a new text begins: the one before may come again later, and is then said again
                end
                seen[part.first] = true
                gathered[#gathered + 1] = { first = part.first, last = part.last, text = shown,
                                            reading = reading(part.first, part.last) }
            end
        end
    end
    if at == before then
        stood = stood + 1
    else
        stood, before = 0, at
    end
    if stood ~= STILL or #gathered == 0 then
        return
    end
    local built, key, readings, previous, first_reading = reader.builder(), {}, {}, nil, nil
    -- A name the text begins with: its command stands alone before the first piece.
    local lead = mem.read(gathered[1].first - 3, 2)
    if lead and #lead == 2 and NAMES[lead:byte(2)] and (lead:byte(1) == 0 or lead:byte(1) == ETX) then
        built:add(name(lead:byte(2)))
    end
    for _, part in ipairs(gathered) do
        key[#key + 1] = part.first
        if part.reading then
            readings[#readings + 1] = part.text
            first_reading = first_reading or part
        else
            -- Rows and a name between this piece and the one before it (the readings between aside).
            local rows, named = nil, nil
            if previous then
                rows, named = between(previous.last, (first_reading or part).first)
            end
            if rows and rows > 0 then
                built:add("\n")
            end
            if named then
                built:add(name(named))
            end
            first_reading = nil
            add(built, part.text, readings)
            readings, previous = {}, part
        end
    end
    local after = gathered[#gathered].last + 2
    local trail = mem.u8(after)
    if trail and NAMES[trail] and at > after then
        built:add(name(trail))
    end
    gathered = {}
    local shown = built:text()
    if shown:find("%S") then
        reader.emit{ text = shown, ruby = built:ruby(), channel = "dialogue",
                     id = text.hash(table.concat(key, " ") .. shown) }
    end
end)
