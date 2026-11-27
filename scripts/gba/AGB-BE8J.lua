-- reader: title ファイアーエムブレム 聖魔の光石 (Fire Emblem: Seima no Kouseki, Game Boy Advance)
-- reader: reads the message window's or speech bubble's page once it has typed out
-- reader: version AGB-BE8J
-- reader: build 105761d9
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows. As in Fire Emblem: Rekka no Ken (games/gba/AGB-AE7J.lua), whose script this
-- is, but for where the unpacked message lies, the build, and the tactician's name (this game has none).
--
-- The game's messages lie compressed in the cartridge. One being shown is unpacked whole into buffers in work
-- RAM (three copies, 0x400 apart), Shift-JIS with the game's own codes between: 0x00 ends it, 0x01 starts a new
-- row, 0x03 waits for the button (a page's end), 0x08-0x0F say which face speaks, 0x10 and two bytes load a face,
-- 0x80 and a byte is another command, and
-- the other codes below 0x20 are one byte each. A word points at the character being typed: in the story's talk
-- one in the CPU's memory, into the first copy; in other windows one the game places in work RAM, into the third.
-- A page that types out stands that word on the 0x03 that ends it for a few frames, then just past it, where it
-- waits for the button; a press while it types puts it past the 0x03 at once. So a page is said when it stands on
-- a 0x03 or just past one: the characters from the 0x03 before (or the message's start) up to it.
--
-- Not covered: who speaks but the name a talk's first page begins with, menus, a choice's answers, fights' and
-- the map's numbers. The opening's narration that scrolls up over the title hasn't been looked at.

-- The character being typed: in the story's talk a word in the CPU's memory, AT, into the first copy; in the
-- windows of the map, the tutorial and the screens before the story, a word that the game keeps in work RAM among
-- PROCS, at a place of its own each time, into the third copy. A word there counts while it moves (typing), for
-- MOVED frames after it last did.
local AT = 0x03000040
local PROCS, PROCS_SIZE = 0x02024E00, 0x700
local COPIES, COPIES_END = 0x0202A600, 0x0202B600
local MOVED = 240
local LOW, HIGH = 0x02000000, 0x02040000 -- work RAM, where messages are unpacked
local MOST = 2048 -- bytes looked back for the message's start
local END, ROW, WAIT, FACE, COMMAND = 0x00, 0x01, 0x03, 0x10, 0x80

-- The build: a table of four of the program's own addresses that the game keeps in the CPU's memory (seen the
-- same from the title through the opening's narration); and the cartridge's header (its title and code), where the host
-- gives the cartridge to read.
local header = mem.read(0x080000A0, 16)
if mem.read(0x03006470, 16) ~= "\x21\x46\x0D\x08\x91\x46\x0D\x08\xB1\x46\x0D\x08\xCD\x46\x0D\x08"
    or (header and #header == 16 and header ~= "FIREEMBLEM8\0BE8J") then
    error("this script is for another build of the game", 0)
end

-- How many bytes a code takes, a character or a command.
local function length(b)
    if (b >= 0x81 and b <= 0x9F) or (b >= 0xE0 and b <= 0xFC) then
        return 2
    elseif b == FACE then
        return 3
    elseif b == COMMAND then
        return 2
    end
    return 1
end

-- The message AT is in: its start, found as the zero byte before it.
local function start_of(at)
    local bytes = mem.read(at - MOST, MOST)
    if not bytes then
        return nil
    end
    for i = #bytes, 1, -1 do
        if bytes:byte(i) == END then
            return at - MOST + i
        end
    end
    return nil
end

-- The page that ends at `at`: the message read forward from its start, a code at a time, the page's text
-- begun anew after each wait.
local function page_ending(at)
    local first = start_of(at)
    if not first then
        return nil
    end
    local bytes = mem.read(first, at - first)
    if not bytes then
        return nil
    end
    local page, i = {}, 1
    while i <= #bytes do
        local b = bytes:byte(i)
        local n = length(b)
        if n == 2 and b ~= COMMAND then
            page[#page + 1] = bytes:sub(i, i + 1)
        elseif b == ROW then
            page[#page + 1] = "\n"
        elseif b == WAIT then
            page = {}
        end
        i = i + n
    end
    local text_ = text.sjis(table.concat(page))
    return (text_:gsub("^\n+", ""):gsub("\n+$", ""))
end

local said_at, said_text = nil, nil

local last_values, last_moved, frame_no = {}, {}, 0

reader.on_state_loaded(function()
    reader.forget()
    said_at, said_text = nil, nil
    last_values, last_moved = {}, {}
end)

local function waiting_at(at)
    if not at or at < LOW + 1 or at >= HIGH then
        return nil
    end
    if mem.u8(at) == WAIT then
        return at
    elseif mem.u8(at - 1) == WAIT then
        return at - 1
    end
    return nil
end

reader.on_frame(function()
    frame_no = frame_no + 1
    local wait = waiting_at(mem.u32(AT))
    local words = mem.read(PROCS, PROCS_SIZE)
    if words and #words == PROCS_SIZE then
        for i = 0, PROCS_SIZE - 4, 4 do
            local v = string.unpack("<I4", words, i + 1)
            if v >= COPIES and v < COPIES_END then
                if last_values[i] ~= v then
                    last_values[i], last_moved[i] = v, frame_no
                end
                if not wait and frame_no - last_moved[i] <= MOVED then
                    wait = waiting_at(v)
                end
            else
                last_values[i] = nil
            end
        end
    end
    if not wait or wait == said_at then
        return
    end
    said_at = wait
    local page = page_ending(wait)
    if page and page:find("%S") and page ~= said_text then
        said_text = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end)
