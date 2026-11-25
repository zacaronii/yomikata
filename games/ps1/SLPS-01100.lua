-- reader: title テイルズ オブ デスティニー (Tales of Destiny)
-- reader: reads the message window, a page at a time; a page's first row is the speaker's name
-- reader: version SLPS-01100
-- reader: build eba83e945427714cc1a2c0d8728f5821d5f60bea
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- How the game keeps its text.
--
-- A stretch of script, several pages with a 0x0C byte between them, is copied into one buffer (TEXT) as
-- Shift-JIS up to a zero byte. READING is the address the game is reading the stretch at, about one character
-- ahead of what is drawn, and 0 once the stretch's last page is typed out. STATE is the window's: 0 none,
-- 1 opening, 2 closing, 3 typing or the last page complete, 4 waiting with more pages to come, 6 moving on.
--
-- The page on screen is the last one that begins at or before READING; with READING at 0, the stretch's last.
-- A page is emitted whole when the window gets to it.
--
-- Battles, shops, menus and choices weren't reached.

local TEXT = 0x801C1870    -- the stretch of script the window is showing
local READING = 0x801BEA34 -- word: where the game is reading it, or 0
local STATE = 0x801BEA3A   -- half word: the window's state, 0 with no window
local LONGEST = 2048
local NEXT_PAGE = "\x0C"

-- Three calls in the game's code.
if mem.u32(0x800B0840) ~= 0x0C03DFA3 or mem.u32(0x80161B20) ~= 0x0C0586CF or mem.u32(0x80123B38) ~= 0x0C04A19D then
    error("this script is for another build of the game", 0)
end

--- The page of `stretch` that begins last at or before `at` (an offset into it) and has anything in it; with
--- `at` outside the stretch, its last page that has. No byte of a Shift-JIS character is 0x0C, so the breaks
--- are found as they stand.
local function page_at(stretch, at)
    local inside = at >= 0 and at <= #stretch
    local found
    local start = 1
    while true do
        local stop = stretch:find(NEXT_PAGE, start, true)
        local last = (stop or #stretch + 1) - 1
        if last >= start and (start - 1 <= at or not inside) then
            found = stretch:sub(start, last)
        end
        if not stop then
            return found
        end
        start = stop + 1
    end
end

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    if (mem.u16(STATE) or 0) == 0 then
        reader.forget("page")
        return
    end
    local reading = mem.u32(READING)
    if not reading then
        return
    end
    local page = page_at(mem.cstr(TEXT, LONGEST), reading - TEXT)
    if page and reader.changed("page", page) then
        reader.emit{ text = text.sjis(page), id = text.hash(page) }
    end
end)
