-- reader: title トワイライトシンドローム 究明編 (Twilight Syndrome: Kyuumei-hen)
-- reader: reads the message window as it fills, a part at a time, and a choice's answers with its cursor
-- reader: version SLPS-00405
-- reader: build 70ea5b39b714061de93240c882d594c50879863a
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- lib/twilight.lua says how the game keeps its text; here are this game's seven chapters. Each is a program of
-- its own (CAP5.EXE and so on), told apart by a word of its code at WORD, with its own cells and its own
-- table of kanji: codes up to 0x00DB are the same throughout (the table beside this script), kanji from
-- 0x00DC are numbered anew by each chapter. The codes aren't Tansaku-hen's. The game stands up to five words
-- past a text's end code while it draws a choice's letters, and an answer's colon may have wide spaces before it.
--
-- Checked in the first chapter only (CAP5.EXE). The other chapters' cells are from the disc's programs.

local twilight = require("lib.twilight")

-- Three words of the small program that stays in memory under every chapter.
if mem.u32(0x80010634) ~= 0x0C0042F5 or mem.u32(0x8001065C) ~= 0x0C0050A7 or mem.u32(0x80010664) ~= 0x0C004DCD then
    error("this script is for another build of the game", 0)
end

twilight.read{
    word = 0x80041C78,
    rest = 5,
    spaces = true,
    tight = false,
    chapters = {
        [0xAFB200B8] = { table = function() return reader.data("ch5") end,
                         offset = 0x800AAABE, shown = 0x800AAAC2, state = 0x800AAAB8, cursor = 0x800973D4 },
        [0x8CA20004] = { table = function() return reader.data("ch6") end,
                         offset = 0x800A80E2, shown = 0x800A80E6, state = 0x800A80DC, cursor = 0x80094608 },
        [0xAFB10014] = { table = function() return reader.data("ch7") end,
                         offset = 0x800A5B02, shown = 0x800A5B06, state = 0x800A5AFC, cursor = 0x80093BBC },
        [0x10400013] = { table = function() return reader.data("ch8") end,
                         offset = 0x800AF726, shown = 0x800AF72A, state = 0x800AF720, cursor = 0x8009B210 },
        [0x30E200FF] = { table = function() return reader.data("ch9") end,
                         offset = 0x800A5D96, shown = 0x800A5D9A, state = 0x800A5D90, cursor = 0x80091EF4 },
        [0x8FB1001C] = { table = function() return reader.data("cha") end,
                         offset = 0x800A86A2, shown = 0x800A86A6, state = 0x800A869C, cursor = 0x80093FAC },
        [0x2402FFFE] = { table = function() return reader.data("chh") end,
                         offset = 0x80096742, shown = 0x80096746, state = 0x8009673C, cursor = 0x80084618 },
    },
}
