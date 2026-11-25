-- reader: title ダンガンロンパ　希望の学園と絶望の高校生 (Danganronpa: Kibou no Gakuen to Zetsubou no Koukousei)
-- reader: reads the message window's rows
-- reader: version NPJH50372, disc version 1.03 (the original release, not the PSP the Best reissue)
-- reader: build e259164dd103d5d0306b8cd7e9dc2be0cb282804
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- How the game keeps its text.
--
-- The message window's rows as the game lays them out: five rows of UTF-16, 108 bytes apart (ROWS), without the
-- script's tags, whole from the moment the line starts and all zero when no message is up. A line is whole on
-- screen within about three frames.
--
-- The speaker isn't read: its name isn't in memory as text. The prompt about trial save data is the PSP's own
-- dialog, not this buffer. The backlog and auto-advance weren't checked.

local ROWS, ROW_SIZE, ROW_COUNT = 0x08C9A264, 108, 5

-- Three calls in the game's code.
if mem.u32(0x088411DC) ~= 0x0E2047EF or mem.u32(0x08873528) ~= 0x0E21C67C or mem.u32(0x0889F514) ~= 0x0E20126D then
    error("this script is for another build of the game", 0)
end

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    local rows, raw = {}, {}
    for row = 0, ROW_COUNT - 1 do
        raw[#raw + 1] = mem.wstr(ROWS + row * ROW_SIZE, ROW_SIZE)
        rows[#rows + 1] = text.utf16le(raw[#raw])
    end
    while #rows > 0 and rows[#rows] == "" do
        rows[#rows] = nil
    end
    if #rows == 0 then
        reader.forget("line")
    elseif reader.changed("line", table.concat(raw, "\0\0")) then
        reader.emit{ text = table.concat(rows, "\n"), id = text.hash(table.concat(raw, "\0\0")) }
    end
end)
