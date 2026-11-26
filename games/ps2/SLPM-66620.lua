-- reader: title ひぐらしのなく頃に祭 (Higurashi no Naku Koro ni Matsuri)
-- reader: reads the story's text as it is shown, up to each wait for a button
-- reader: version SLPM-66620
-- reader: build SLPM-66620-cda67466
-- reader: no hooks
-- reader: no layout where the rows are drawn isn't looked into

-- How the game keeps what it shows.
--
-- The story's text is unpacked a page at a time. LINE has the page being shown: Shift-JIS, ended by a zero
-- byte, with the game's marks in it as single ASCII letters (digits may follow one): "k" waits for a button
-- and the page goes on after it, "p" ends the page, "r" starts a new row, and "v00/992600001." names the
-- voice that speaks what follows. The page is there whole from its first frame, so LINE
-- alone doesn't say how much of it is on screen.
--
-- That is said by AT, which points into another copy of the page (the one the game reads from), just past
-- the last mark it has come to. So the part shown is the beginning of LINE that the bytes before AT are the
-- same as, cut at a mark; and what a press adds is said as a text of its own, when AT has come past its mark.
--
-- Not covered: who speaks (the game shows no name; a spoken part is marked `voiced`); choices; the menus and the tips.

local LINE = 0x01E0697C -- the page shown, a Shift-JIS string with marks
local AT = 0x01E0615C -- word: just past the last mark reached, in the copy the game reads
local MOST = 1024 -- bytes of a page

if mem.u32(0x00100018) ~= 0x0C04B2A8 or mem.u32(0x0017041C) ~= 0x0C05B7CC then
    error("this script is for another build of the game", 0)
end

--- A page's parts: each the text up to a wait ("k") or the page's end ("p"), without the marks in it, and
--- where in the page's bytes it ends (past the mark). "r" starts a new row; "v", digits, "/", digits and a
--- full stop name a voice; any other letter with its digits is left out.
local function parts(bytes)
    local out, piece, i = {}, {}, 1
    while i <= #bytes do
        local b = bytes:byte(i)
        if (b >= 0x81 and b <= 0x9F) or (b >= 0xE0 and b <= 0xFC) then
            piece[#piece + 1] = bytes:sub(i, i + 1)
            i = i + 2
        elseif b >= 0x61 and b <= 0x7A then
            i = i + 1
            while i <= #bytes and bytes:byte(i) >= 0x30 and bytes:byte(i) <= 0x39 do
                i = i + 1
            end
            if b == 0x76 and bytes:byte(i) == 0x2F then -- "v00/992600001."
                local stop = bytes:find(".", i, true)
                i = stop and stop - i <= 12 and stop + 1 or i
            elseif b == 0x72 then
                piece[#piece + 1] = "\n"
            elseif b == 0x6B or b == 0x70 then
                out[#out + 1] = { text = table.concat(piece), ends = i - 1, voiced = piece.voiced }
                piece = {}
            end
            if b == 0x76 then
                piece.voiced = true
            end
        else
            piece[#piece + 1] = bytes:sub(i, i)
            i = i + 1
        end
    end
    return out
end

reader.on_state_loaded(function()
    reader.forget()
end)

local page, said = nil, 0 -- the page being followed, and how many of its parts were said

reader.on_frame(function()
    local bytes, at = mem.cstr(LINE, MOST), mem.u32(AT)
    if not bytes or bytes == "" or not at then
        return
    end
    local found = parts(bytes)
    -- How far the game has come: the longest beginning of the page, cut at a mark, that stands before AT.
    local reached = 0
    for i = #found, 1, -1 do
        if mem.read(at - found[i].ends, found[i].ends) == bytes:sub(1, found[i].ends) then
            reached = i
            break
        end
    end
    if reached == 0 then
        return
    end
    if bytes ~= page then
        page, said = bytes, 0
    end
    while said < reached do
        said = said + 1
        local shown = text.sjis(found[said].text)
        if shown:find("%S") and not shown:find("\u{FFFD}", 1, true) then
            reader.emit{ text = shown, channel = "dialogue", id = text.hash(bytes .. said),
                         meta = { part = said, voiced = found[said].voiced or false } }
        end
    end
end)
