-- reader: title ゼルダの伝説 トワイライトプリンセス (Zelda no Densetsu: Twilight Princess, Wii)
-- reader: reads the message window's page each time the game puts a new one in
-- reader: version RZDJ01
-- reader: build RZDJ01-00
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- The window's page is kept in a buffer in MEM1 as it is drawn: a header ("HM"), then 0x10 bytes on the page,
-- in Shift-JIS, ending with a zero byte. The buffer is made anew for each scene: the word at OBJECT points at
-- an object of the game's, and the word at TEXT_AT bytes into it at the page. 0x0A starts a new row. An
-- escape, 0x1B, two letters and a value in brackets ("\x1BCR[33]"), sets how what follows is drawn (colour,
-- place): it is left out. The furigana over kanji aren't in the page: they are drawn from elsewhere.
--
-- The page types into the buffer a few characters at a time. So a page is said once the buffer has stood the
-- same for STILL frames (a page waiting for the button stands) and differs from the last said.
--
-- Not covered: who speaks (the game shows no name), furigana, a choice's answers and its cursor, the menus and
-- the screens before the story (another buffer). A page the player cuts short isn't said. The buffer keeps the
-- last page after its window closes, so a state loaded then says it again.

local OBJECT, TEXT_AT, MOST = 0x80533320, 0x1D30, 0x400
local LOW, HIGH = 0x80000000, 0x81800000 -- MEM1
local STILL = 20 -- frames the page stands the same before it is said

-- The build: the disc's id, which the console puts at the start of MEM1.
if mem.read(0x80000000, 6) ~= "RZDJ01" then
    error("this script is for another build of the game", 0)
end

local said, last, stood = nil, nil, 0

reader.on_state_loaded(function()
    reader.forget()
    said, last, stood = nil, nil, 0
end)

reader.on_frame(function()
    local object = mem.u32be(OBJECT)
    local page = object and object >= LOW and object < HIGH and mem.u32be(object + TEXT_AT)
    if not page or page < LOW + 0x10 or page >= HIGH or mem.read(page - 0x10, 4) ~= "HM\0\xFF" then
        return
    end
    local raw = mem.cstr(page, MOST)
    if not raw or raw == "" then
        return
    end
    stood = raw == last and stood + 1 or 0
    last = raw
    if stood < STILL or raw == said then
        return
    end
    said = raw
    local plain = raw:gsub("\x1B%a%a%[%x*%]", "")
    local shown = text.sjis(plain)
    shown = shown:gsub("^\n+", ""):gsub("\n+$", "")
    if shown:find("%S") then
        reader.emit{ text = shown, channel = "dialogue", id = text.hash(raw) }
    end
end)
