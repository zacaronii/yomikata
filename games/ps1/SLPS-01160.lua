-- reader: title ゼノギアス (Xenogears)
-- reader: reads a message window's text once the game has typed it out, with who speaks
-- reader: version SLPS-01160
-- reader: build 3f9633f80213427f55894cda9fc8c3346ecf31b4
-- reader: no hooks
-- reader: no layout where a window is drawn isn't looked into

-- How the game keeps what it shows.
--
-- A place's messages are loaded with the place as one block: a count, an offset for each, and the messages,
-- each ending with a zero byte. Nothing copies a message anywhere: a window has a pointer into the block, and
-- as the window types the pointer walks through the message, and then stands on the zero that ends it while
-- the game waits. So a message is read from where the pointer was first seen to that zero, once it is there.
--
-- A character is one byte, or two when the first is 0xF8 to 0xFE (the kanji, and a few kana and signs).
-- Which character a code is, is the table's to say (SLPS-01160.tbl): the game has its own font and order.
-- Other bytes:
--   0x00        the message's end
--   0x01        a new row
--   0x02        a wait for a button inside a message; the window is then cleared for the message's next page
--   0x0F a b    a command: 0x0F 0x00 n waits n frames; 0x0F 0x05 n is a name, by its number in the game's
--               list of names (NAMES, 20 bytes each).
-- A spoken message has who speaks as its first row (a name by its number, or written out) and an opening
-- bracket at the start of the second: the window shows the name above the text.
--
-- The field (and the narration over black at the game's opening, which is the field too) has its window at
-- a fixed place, and a word that points at the place's block. A scene played by the fight's
-- program (the game's first) has words that point at its window and its block; a word of that program's
-- own code tells that it is the one running.
--
-- Not covered: a message is said once it is typed to its end, so one the player cuts short with a press is
-- missed; the window's pointer stays where it was after the window closes, so a state loaded then says the
-- last message again; more than one window at a time; choices, menus, fights; codes the table hasn't got
-- (most kanji so far) come out as U+FFFD.

local AT = 0x1C -- in a window, a word: where in its message it has got to
local FIELD_WINDOW = 0x800C1B84 -- in the field: the window
local FIELD_BLOCK = 0x800AD0C8 -- word, in the field: the place's block of messages
local SCENE_WINDOW = 0x800D24CC -- word, in a scene played by the fight's program: the window being typed in
local SCENE_BLOCK = 0x800D2A60 -- word, there: the scene's block of messages
local SCENE = 0x800AD0C0 -- word: 0x3C02800C when it is the fight's program that runs
local NAMES, NAME = 0x8006CCC4, 20 -- the names: so many bytes each, ended by a zero
local LOW, HIGH = 0x80010000, 0x80200000
local MOST = 1024 -- bytes of a message
local PAGE = 0x02 -- a wait for a button inside a message: what follows is its next page
local QUOTE = "\x5C" -- the code of the opening bracket a spoken message begins with

-- The first of the game's names, as the disc has it: the build's mark.
if mem.u32(NAMES) ~= 0x00A8A9C6 then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")
local source = reader.data("tbl")
if not source then
    error("SLPS-01160.tbl is missing: the game's table of characters", 0)
end
local characters = tbl.parse(source).chars

--- What stands for a code the table hasn't got.
local function unknown(code)
    return "\u{FFFD}"
end

--- The pieces of `bytes`: { kind, value } with kind "char" (a code's bytes), "row", "wait", "name" (a number),
--- "command" and "end". Stops after an end.
local function pieces(bytes)
    local out, i = {}, 1
    while i <= #bytes do
        local b = bytes:byte(i)
        if b == 0x00 then
            out[#out + 1] = { "end", i }
            break
        elseif b == 0x01 then
            out[#out + 1] = { "row", i }
            i = i + 1
        elseif b == 0x02 then
            out[#out + 1] = { "page", i }
            i = i + 1
        elseif b == 0x0F then
            local what, n = bytes:byte(i + 1), bytes:byte(i + 2)
            if not n then
                break
            end
            out[#out + 1] = { what == 0x05 and "name" or what == 0x00 and "wait" or "command", n }
            i = i + 3
        elseif b >= 0xF8 and b <= 0xFE then
            if i == #bytes then
                break
            end
            out[#out + 1] = { "char", bytes:sub(i, i + 1) }
            i = i + 2
        else
            out[#out + 1] = { "char", bytes:sub(i, i) }
            i = i + 1
        end
    end
    return out
end

--- Text by the table: a code it hasn't got is U+FFFD.
local function decode(bytes)
    local out = {}
    for _, piece in ipairs(pieces(bytes)) do
        if piece[1] == "char" then
            out[#out + 1] = characters[piece[2]] or unknown(piece[2])
        end
    end
    return table.concat(out)
end

local function name(n)
    local bytes = mem.cstr(NAMES + NAME * n, NAME)
    return bytes and decode(bytes) or ""
end

--- Whether a block of messages begins at `block`: its count, and the first offset just past the two lists.
local function is_block(block)
    local count = block and block >= LOW and block < HIGH and mem.u32(block)
    return count and count > 0 and count <= 4096 and mem.u16(block + 4) == 8 + 4 * count and count
end

--- Where the window that types has got to, and the block its message is in. Nil when nothing is typed.
local function typing()
    local window, block
    if mem.u32(SCENE) == 0x3C02800C then
        window, block = mem.u32(SCENE_WINDOW), mem.u32(SCENE_BLOCK)
    else
        window, block = FIELD_WINDOW, mem.u32(FIELD_BLOCK)
    end
    if not window or window < LOW or window >= HIGH or not is_block(block) then
        return nil
    end
    local at = mem.u32(window + AT)
    if at and at > block and at < HIGH then
        return at, block
    end
end

--- Where the message that `at` is inside begins, by the block's offsets. Nil when `at` isn't in the block
--- (a window draws a name out of the list of names with the same pointer).
local function begins(at, block)
    local count = is_block(block)
    local offsets = count and mem.read(block + 4, 2 * count)
    local best
    for i = 1, offsets and #offsets // 2 or 0 do
        local from = block + offsets:byte(2 * i - 1) + 256 * offsets:byte(2 * i)
        if from <= at and at - from < MOST and (not best or from > best) then
            best = from
        end
    end
    return best
end

local before, start, said = nil, nil, nil

reader.on_state_loaded(function()
    reader.forget()
    before, start, said = nil, nil, nil
end)

reader.on_frame(function()
    local at, block = typing()
    if not at then
        before, start = nil, nil
        return
    end
    if at ~= before then
        start, before = begins(at, block), at
    end
    local stop = mem.u8(at)
    if not start or (stop ~= 0x00 and stop ~= PAGE) or said == at or at <= start then
        return
    end
    -- The byte is a message's end or a page's only where a piece begins: a wait's own middle byte is zero
    -- too. And not every message has an offset of its own in the block: one may follow another's end
    -- directly, so the messages from the offset on are stepped over until the one AT is in.
    local from, bytes, list = start, nil, nil
    while true do
        bytes = mem.read(from, at - from + 1)
        list = pieces(bytes or "")
        local last = list[#list]
        if not last or last[2] > at - from + 1 or (last[2] < at - from + 1 and last[1] ~= "end") then
            return
        end
        if last[2] == at - from + 1 then
            if last[1] ~= "end" and last[1] ~= "page" then
                return
            end
            break
        end
        from = from + last[2]
    end
    said = at
    bytes = bytes:sub(1, -2)
    -- The rows of the page that ends here (a message may have several, a wait for a button between them),
    -- and the code each row begins with. Who speaks is the message's first row, whichever page this is.
    local rows, first, row, speaker, pages = {}, {}, {}, nil, 1
    local function who()
        if pages == 1 and speaker == nil and #rows >= 2 and first[2] == QUOTE and rows[1] ~= "" then
            speaker = table.remove(rows, 1)
        end
    end
    for index, piece in ipairs(list) do
        local kind, value = piece[1], piece[2]
        if kind == "char" then
            first[#rows + 1] = first[#rows + 1] or value
            row[#row + 1] = characters[value] or unknown(value)
        elseif kind == "name" then
            first[#rows + 1] = first[#rows + 1] or ""
            row[#row + 1] = name(value)
        elseif kind == "row" then
            rows[#rows + 1], row = table.concat(row), {}
        elseif kind == "page" and index < #list then
            rows[#rows + 1] = table.concat(row)
            who()
            rows, first, row, pages = {}, {}, {}, pages + 1
        end
    end
    rows[#rows + 1] = table.concat(row)
    who()
    while rows[1] and not rows[1]:find("[^\u{3000} ]") and #rows > 1 do
        table.remove(rows, 1) -- a later page begins with the row its break was on
    end
    local shown = table.concat(rows, "\n")
    if shown:find("%S") then
        reader.emit{ text = shown, speaker = speaker, channel = "dialogue",
                     id = text.hash(string.format("%08X %d ", from, pages) .. bytes) }
    end
end)
