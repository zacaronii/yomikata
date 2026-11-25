-- reader: title Ever17 -the out of infinity- (Ever17: The Out of Infinity)
-- reader: reads the message window's line and its speaker
-- reader: version SLPS-25149
-- reader: build SLPS-25149-6e86bb5a
-- reader: no hooks
-- reader: no layout where the window's rows are drawn isn't looked into

-- How the game keeps what it shows.
--
-- A scene's script is loaded whole, Shift-JIS strings each ended by a zero byte, and the line being shown is
-- copied out of it to LINE as it starts to type: whole from its first frame, without the marks it ends with
-- ("%K" waits for a button, "%P" ends the page). The word before LINE points into the script, past that line.
-- Marks inside a line: "%N" starts a new row; "%p", "%O" and "%T" with a number are pauses and the like.
--
-- A spoken line has its speaker's name before its opening bracket, and the game shows the name in a box of
-- its own above the window. NAME is how many characters of the line are the name, 0 for a line nobody
-- speaks: the name is given as the speaker, and the line begins after it.
--
-- LINE keeps a line after the window has gone (a change of scene, a film): a line is said once, as it comes.
--
-- Not covered: choices; the backlog, the menus and the memory card's messages.

local LINE = 0x00EC4C54 -- the line shown, a Shift-JIS string
local NAME = 0x00EDA430 -- word: the characters of it that are the speaker's name
local MOST = 512 -- bytes of a line

if mem.u32(0x00100198) ~= 0x0C06F99E or mem.u32(0x00168A34) ~= 0x0C06B16E then
    error("this script is for another build of the game", 0)
end

--- A line without the engine's marks: its text, and its first `named` characters apart as the speaker.
local function parts(line, named)
    line = line:gsub("%%N", "\n"):gsub("%%[A-Za-z]+%d*", "")
    if named > 0 and named < text.len(line) then
        local cut = utf8.offset(line, named + 1)
        return line:sub(cut), line:sub(1, cut - 1)
    end
    return line, nil
end

local waiting -- the line of the tick before

reader.on_state_loaded(function()
    reader.forget()
    waiting = nil
end)

reader.on_frame(function()
    local bytes = mem.cstr(LINE, MOST)
    if not bytes or bytes == "" then
        reader.forget("line")
        waiting = nil
        return
    end
    -- A line is taken on the second tick it is there: the name's length is then the line's own.
    if bytes ~= waiting then
        waiting = bytes
        return
    end
    if reader.changed("line", bytes) then
        local named = mem.u32(NAME) or 0
        local shown, speaker = parts(text.sjis(bytes), named <= 16 and named or 0)
        if shown:find("%S") and not shown:find("\u{FFFD}", 1, true) then
            reader.emit{ text = shown, speaker = speaker, channel = "dialogue", id = text.hash(bytes) }
        end
    end
end)
