-- reader: title やるドラシリーズVol.1 ダブルキャスト (Yarudora Series Vol. 1: Double Cast)
-- reader: reads each line under the film as the game starts to type it
-- reader: version SCPS-10053 and SCPS-10054 (the two discs share one program)
-- reader: build 9a0ac19e2456f8caa981a151b3c6010ff0886f69
-- reader: no hooks
-- reader: no layout where the lines are drawn isn't looked into

-- How the game keeps what it shows.
--
-- A scene is loaded as a list of strings, one after another with a zero byte between: the names of the films,
-- pictures and sounds to play, one-letter commands ("#" waits for a press), and the lines, in Shift-JIS with
-- their hiragana as single bytes (lib/halfkana.lua). The scene runs on a stack of its own, and one word of
-- that stack (TOKEN) is the string the scene is at: a line while the game types it, then whatever comes next.
-- Nothing in memory says which line is on screen once it is typed: the game draws a line once and keeps the
-- picture. So a line is read while it is being typed, which takes a frame or so for each character.
--
-- A line is a string that doesn't start with a plain letter or sign (those are names of files and commands).
-- The list also has the lines that are only spoken, which the scene passes over without typing them: a line
-- is said here only when a second word (TYPING, on the game's main stack) points into it, as it does from
-- the first character typed. Both words are places on a stack: they held in every scene looked at, and a
-- scene that calls the same things from somewhere deeper would move them.
--
-- Read: the narration and the protagonist's lines, which the game types under the film. Not read: the other
-- characters' lines, which are in the list too but were only ever spoken in what was played, whatever the
-- game's lines setting (各種設定, 表示, 台詞設定); and choices, of which none came up.
--
-- Checked through the opening and the first scene of disc 1.

local halfkana = require("lib.halfkana")

local TOKEN = 0x8011EFE4  -- word: the string the scene is at
local TYPING = 0x801FFE00 -- word: how far into a line the typing has got, while it types
local RAM, RAM_END = 0x80010000, 0x80200000
local LONGEST = 400 -- bytes of a line

-- Two calls of the program that is there from power-on.
if mem.u32(0x80011EE0) ~= 0x0C016C42 or mem.u32(0x80033708) ~= 0x0C006201 then
    error("this script is for another build of the game", 0)
end

local said -- the line last said, by where it is

reader.on_state_loaded(function()
    reader.forget()
    said = nil
end)

reader.on_frame(function()
    local at = mem.u32(TOKEN)
    if not at or at < RAM or at >= RAM_END then
        said = nil
        return
    end
    if at == said then
        return
    end
    local bytes = mem.cstr(at, LONGEST)
    if not bytes or bytes == "" or bytes:byte(1) < 0x80 then
        said = nil
        return
    end
    -- Only a line the game is typing: the scene also passes over the lines that are only spoken.
    local typed = mem.u32(TYPING)
    if not typed or typed <= at or typed > at + #bytes then
        return
    end
    said = at
    local line = halfkana.fold(text.sjis(bytes)):gsub("^\u{3000}+", "")
    if line ~= "" then
        reader.emit{ text = line, id = text.hash(line) }
    end
end)
