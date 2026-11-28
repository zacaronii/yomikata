-- reader: title この世の果てで恋を唄う少女YU-NO (Yu-No: Konoyo no Hate de Koi o Utau Shoujo)
-- reader: reads the message window's page each time it changes, the speaker's name with it
-- reader: version T-28004G-D1
-- reader: build T-28004G-D1-V1.002
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The scenario is packed on the disc and unpacked into the low work RAM as Shift-JIS. The page on screen is
-- copied whole into a buffer there (PAGE), ended by a mark (0x01) and a zero, before it is drawn: a speaker's name in 【】 first
-- where someone speaks, the line in 《》 or as it is. Between pages the buffer begins with a control byte (0x01),
-- and what follows its zero is left from a longer page before it. A page is said once the buffer has stood the
-- same for STILL frames and differs from the last said.
--
-- Not covered: the rows' breaks (the window wraps a page by its width), choices and their answers, menus, the
-- other discs (their builds are their own).

local PAGE, MOST = 0x2A20E8, 0x200
local STILL = 4

-- The build: the disc's boot header, which the console loads into the high work RAM (its product number and
-- version, and which disc of the set).
if mem.read(0x06000C20, 16) ~= "T-28004G  V1.002" or mem.read(0x06000C38, 6) ~= "CD-1/3" then
    error("this script is for another build of the game", 0)
end

local said, last, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
end)

reader.on_frame(function()
    local raw = mem.cstr(PAGE, MOST)
    raw = raw and raw:gsub("[\1-\31]+$", "")     -- a page ends with a mark (0x01: wait for the button)
    if not raw or raw == "" or raw:byte(1) < 0x20 then
        last, stood = nil, 0
        return
    end
    stood = raw == last and stood + 1 or 0
    last = raw
    if stood ~= STILL or raw == said then
        return
    end
    local shown = text.sjis(raw)
    -- A page of the game's text: every byte reads, and nothing but its characters.
    if not shown:find("%S") or shown:find("[%z\1-\31]") or shown:find("\u{FFFD}") then
        return
    end
    said = raw
    reader.emit{ text = shown, channel = "dialogue", id = text.hash(raw) }
end)
