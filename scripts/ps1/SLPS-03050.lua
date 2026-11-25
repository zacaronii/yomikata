-- reader: title テイルズ オブ エターニア (Tales of Eternia)
-- reader: reads the message window's line and the speaker's name
-- reader: version SLPS-03050, SLPS-03051 and SLPS-03052 (the three discs: the same program)
-- reader: build 0e461086b49d4bed0a8648d7ab4b841abfae9336
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- How the game keeps its text.
--
-- The line is a Shift-JIS string in one buffer (TEXT), whole from the moment its window opens, and left there
-- after it closes. Inside it are codes of two bytes: 0x03 and a colour, 0x07 and a style. The second byte may
-- be zero, so the line's end is the first zero byte that isn't a code's.
--
-- The window has a record: its state (STATE: 0 closed, 1 opening, 2 closing, 3 open) and a pointer to the
-- speaker's name (NAME).
--
-- Talk on a map, shops, battles, menus and choices weren't reached.

local TEXT, LONGEST = 0x801F7878, 512
local STATE, OPEN = 0x800D3686, 3 -- half word
local NAME = 0x800D367C           -- word: the speaker's name

-- Three calls in the game's code.
if mem.u32(0x8002BA74) ~= 0x0C02131F or mem.u32(0x8003D020) ~= 0x0C00CE26 or mem.u32(0x8004A060) ~= 0x0C0126DF then
    error("this script is for another build of the game", 0)
end

--- The line's bytes up to its end, and the same without its codes.
local function line_of(bytes)
    local kept, i, from = {}, 1, 1
    while i <= #bytes do
        local byte = bytes:byte(i)
        if byte == 0 then
            break
        elseif byte == 0x03 or byte == 0x07 then
            kept[#kept + 1] = bytes:sub(from, i - 1)
            i = i + 2
            from = i
        elseif (byte >= 0x81 and byte <= 0x9F) or (byte >= 0xE0 and byte <= 0xFC) then
            if i + 1 > #bytes then
                break
            end
            i = i + 2
        else
            i = i + 1
        end
    end
    kept[#kept + 1] = bytes:sub(from, math.min(i, #bytes + 1) - 1)
    return bytes:sub(1, i - 1), table.concat(kept)
end

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    if mem.u16(STATE) ~= OPEN then
        reader.forget("line")
        return
    end
    local raw, shown = line_of(mem.read(TEXT, LONGEST))
    local name = mem.u32(NAME)
    local speaker = name and mem.cstr(name, 32) or ""
    if shown ~= "" and reader.changed("line", speaker .. "\n" .. raw) then
        reader.emit{ text = text.sjis(shown), speaker = text.sjis(speaker), id = text.hash(raw) }
    end
end)
