-- reader: title ペルソナ2 罪 (Persona 2: Tsumi)
-- reader: reads the message window, a page at a time; a choice's answers as part of its message
-- reader: version SLPS-02100
-- reader: build f477d5135dc515489f5caeb86ff0d4a9a6f97ce4
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- How the game keeps its text.
--
-- A scene's messages are loaded as one stretch of 16-bit codes, one message after another, each ended by the
-- code 0x1103. SCENE holds that stretch's address. A code is the number of a glyph in the game's own 12x12 font
-- (the table beside this script says which character each is), or a control: 0x11xx without an argument, 0x12xx
-- with one argument word after it. Three of the 0x11xx only leave a gap in the line, and are in the table as
-- a space.
--
-- WINDOW points at the message window's object: its kind at +0x04, and at +0x0C the address the game is
-- reading the stretch at. The message on screen is the one that address stands in: it rests on the message's
-- end code while the window waits. UP is nonzero while the window is on screen.
--
-- The reading itself is lib/persona2.lua, which Persona 2: Batsu shares.
--
-- Names the game puts in by a code (a speaker's by number, the hero's) read as nothing. 33 codes the script
-- uses aren't in the table and read as U+FFFD. Choices, shops and battles weren't checked.

local persona2 = require("lib.persona2")

if mem.u32(0x80020000) ~= 0x02603821 or mem.u32(0x80030000) ~= 0x8FB3002C or mem.u32(0x80018000) ~= 0xAC4A0010 then
    error("this script is for another build of the game", 0)
end

local UP = 0x8007B708 -- word: nonzero while the window is on screen

persona2.read{
    scene = 0x800DE014,  -- word: the loaded scene's text
    window = 0x8007B6E0, -- word: the window's object
    kind = 0x2408,
    up = function()
        return (mem.u32(UP) or 0) ~= 0
    end,
    -- Controls that draw nothing: a wait, a choice's wait, the hero's names, and one with no known effect.
    silent = persona2.codes{ 0x1106, 0x1107, 0x1109, 0x1112, 0x1113, 0x1114 },
    -- Controls with an argument word after them: a pause, a choice, a speaker's name by number, a colour, others.
    sizes = persona2.codes{ [0x1205] = 4, [0x1208] = 4, [0x120B] = 4, [0x120C] = 4, [0x120E] = 4, [0x1210] = 4,
                            [0x1211] = 4, [0x1215] = 4, [0x121C] = 4, [0x121D] = 4, [0x121E] = 4 },
}
