-- reader: title トワイライトシンドローム 探索編 (Twilight Syndrome: Tansaku-hen)
-- reader: reads the message window as it fills, a part at a time, and a choice's answers with its cursor
-- reader: version SLPS-00102
-- reader: build 23eb26992df8396bac6e65ac0a61897cbac1c715
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- lib/twilight.lua says how the game keeps its text; here are this game's six chapters. Each is a program of
-- its own (CAP0.EXE and so on), told apart by a word of its code at WORD, with its own cells and its own
-- table of kanji: codes up to 0x0108 are the same throughout (the table beside this script), kanji from
-- 0x010C are numbered anew by each chapter. The game stands up to four words past a text's end code while it
-- draws a choice's letters.
--
-- Checked in the first chapter only. The other chapters' cells are from the discs' programs.

local twilight = require("lib.twilight")

-- Three words of the small program that stays in memory under every chapter.
if mem.u32(0x80011A9C) ~= 0x0C004333 or mem.u32(0x80011A0C) ~= 0x34020004 or mem.u32(0x80011934) ~= 0x34020005 then
    error("this script is for another build of the game", 0)
end

twilight.read{
    word = 0x8004AA1C,
    rest = 4,
    spaces = false,
    chapters = {
        [0xAFBF0020] = { table = function() return reader.data("ch0") end,
                         offset = 0x800A4CEA, shown = 0x800A4CEE, state = 0x800A4CE4, cursor = 0x80094D28 },
        [0xAFB20028] = { table = function() return reader.data("ch1") end,
                         offset = 0x800A39EE, shown = 0x800A39F2, state = 0x800A39E8, cursor = 0x800926EC },
        [0x30C21000] = { table = function() return reader.data("ch2") end,
                         offset = 0x8009F8E6, shown = 0x8009F8EA, state = 0x8009F8E0, cursor = 0x8008E3FC },
        [0xA4820032] = { table = function() return reader.data("ch3") end,
                         offset = 0x800A47FE, shown = 0x800A4802, state = 0x800A47F8, cursor = 0x80093878 },
        [0xAC2239F4] = { table = function() return reader.data("ch4") end,
                         offset = 0x800A4796, shown = 0x800A479A, state = 0x800A4790, cursor = 0x80092758 },
        [0xA062000F] = { table = function() return reader.data("chx") end,
                         offset = 0x8009444E, shown = 0x80094452, state = 0x80094448, cursor = 0x80084DCC },
    },
}
