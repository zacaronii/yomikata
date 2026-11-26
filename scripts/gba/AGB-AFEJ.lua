-- reader: title ファイアーエムブレム 封印の剣 (Fire Emblem: Fuuin no Tsurugi, Game Boy Advance)
-- reader: reads the message window's or speech bubble's page once it has typed out
-- reader: version AGB-AFEJ
-- reader: build fdaac108
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The game's messages lie compressed in the cartridge. One being shown is unpacked whole into a buffer in work
-- RAM, Shift-JIS with the game's own codes between: 0x00 ends it, 0x01 starts a new row, 0x03 waits for the
-- button (a page's end), 0x08-0x0F say which face speaks, 0x10 and two bytes load a face, 0x80 and a byte is
-- another command, and the other codes below 0x20 are one byte each. A word in the CPU's memory, AT, points
-- at the character being typed. A page that types out stands AT on the 0x03 that ends it for a few frames,
-- then just past it, where it waits for the button; a press while it types puts AT past the 0x03 at once. So a
-- page is said when AT stands on a 0x03 or just past one: the characters from the 0x03 before (or the
-- message's start) up to it.
--
-- Not covered: who speaks (a face, not a name), the menus and the map's windows, a choice's answers, the
-- screens before the story. The opening's narration that scrolls up over the title hasn't been looked at.

local AT = 0x03000038 -- the character being typed, in the unpacked message
local LOW, HIGH = 0x02000000, 0x02040000 -- work RAM, where messages are unpacked
local MOST = 2048 -- bytes looked back for the message's start
local END, ROW, WAIT, FACE, COMMAND = 0x00, 0x01, 0x03, 0x10, 0x80

-- The build: a table of four of the program's own addresses that the game keeps in the CPU's memory (seen the
-- same from the map's narration to the camp); and the cartridge's header (its title and code), where the host
-- gives the cartridge to read.
local header = mem.read(0x080000A0, 16)
if mem.read(0x03006320, 16) ~= "\x8D\xBF\x09\x08\xFD\xBF\x09\x08\x1D\xC0\x09\x08\x39\xC0\x09\x08"
    or (header and #header == 16 and header ~= "FIREEMBLEM6\0AFEJ") then
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

reader.on_state_loaded(function()
    reader.forget()
    said_at, said_text = nil, nil
end)

reader.on_frame(function()
    local at = mem.u32(AT)
    if not at or at < LOW + 1 or at >= HIGH then
        return
    end
    local wait
    if mem.u8(at) == WAIT then
        wait = at
    elseif mem.u8(at - 1) == WAIT then
        wait = at - 1
    else
        return
    end
    if wait == said_at then
        return
    end
    said_at = wait
    local page = page_ending(wait)
    if page and page:find("%S") and page ~= said_text then
        said_text = page
        reader.emit{ text = page, channel = "dialogue", id = text.hash(page) }
    end
end)
