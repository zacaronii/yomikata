-- reader: title ペルソナ2 罰 (Persona 2: Batsu)
-- reader: reads the message window, a message at a time; a choice's answers as part of its message
-- reader: version SLPS-02785 (disc 1)
-- reader: build 2052488cf17bd9cbb61bfc4039143aee6c7a0c4d
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- Persona 2: Tsumi's engine at other addresses (lib/persona2.lua reads it), with Tsumi's glyphs and its own
-- from 0x0A0F on: the table beside this script is Tsumi's with this game's after it.
--
-- A control word 0x1Nxx has N minus 0x11 argument words after it. The window's object has, at +0x14, its
-- state: bit 0x04 is set while a message is up.
--
-- A message of several screens reads whole on each. A choice reads as its question's last line, then an answer
-- a line. 43 codes the script uses aren't in the table and read as U+FFFD. Shops and battles weren't checked.

local persona2 = require("lib.persona2")

-- Three calls in the game's code.
if mem.u32(0x80022A90) ~= 0x0C008999 or mem.u32(0x8003236C) ~= 0x0C00CC16 or mem.u32(0x8004A498) ~= 0x0C011C00 then
    error("this script is for another build of the game", 0)
end

local STATE, MESSAGE_UP = 0x14, 0x04

persona2.read{
    scene = 0x800EC014,  -- word: the loaded scene's text
    window = 0x8007CEA0, -- word: the window's object
    kind = 0x5290,
    up = function(window)
        return (mem.u8(window + STATE) or 0) & MESSAGE_UP ~= 0
    end,
    -- Controls that draw nothing: a wait, a choice's wait, names put in by code, and one with no known effect.
    silent = persona2.codes{ 0x1106, 0x1107, 0x1109, 0x1120, 0x1121, 0x1122 },
    -- Controls with arguments, by their bytes in all.
    sizes = persona2.codes{ [0x1205] = 4, [0x1208] = 4, [0x120E] = 4, [0x1226] = 4, [0x122E] = 4, [0x122F] = 4,
                            [0x1235] = 4, [0x1428] = 8, [0x1429] = 8, [0x1528] = 10, [0x1828] = 16,
                            [0x182A] = 16, [0x1928] = 18 },
}
