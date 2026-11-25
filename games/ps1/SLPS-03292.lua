-- reader: title メモリーズオフ セカンド (Memories Off 2nd)
-- reader: reads the message window's three rows, the speaker's name, and a choice's answers with its cursor
-- reader: version SLPS-03292 (disc 1) and SLPS-03293 (disc 2): the same program
-- reader: build 8ff227df40eccefd0a9e772f4930d693b89fc117
-- reader: no hooks
-- reader: no layout where the window draws its characters isn't looked into

-- How the game keeps its text.
--
-- The window has three rows of Shift-JIS as the game wraps them, 44 bytes apart (ROWS); a full row has 22
-- characters and no ending zero. The speaker's name (NAME) is empty for narration. Rows and name are cleared
-- while the window is away, and a line is whole there as soon as it starts to type.
--
-- At a choice CHOOSING is nonzero. The answers are rows of their own (ANSWERS) that keep older answers, so
-- COUNT says how many are this choice's; CURSOR is the answer the cursor is on, -1 until a direction is pressed.
--
-- Movies write over all of this, and it stays dirty on the title screen: MODE is 1 only in the game itself.
--
-- Auto and skip weren't checked.

local MODE = 0x8009115C -- word: 1 in game (2 boot, 3 title, 4 movie)
local ROWS, ROW_SIZE, ROW_COUNT = 0x8010B5EE, 44, 3
local NAME = 0x8010B5C2
local CHOOSING, COUNT, CURSOR = 0x8010BC22, 0x8010BCA0, 0x8010BC23
local ANSWERS, MOST_ANSWERS = 0x8010BCAC, 10

if mem.u32(BUILD_A) ~= WORD_A or mem.u32(BUILD_B) ~= WORD_B or mem.u32(BUILD_C) ~= WORD_C then
    error("this script is for another build of the game", 0)
end

--- Rows of text `ROW_SIZE` apart: each up to its zero or its end, without the empty ones at the end.
local function rows_at(address, count)
    local rows, raw = {}, {}
    for row = 0, count - 1 do
        raw[#raw + 1] = mem.cstr(address + row * ROW_SIZE, ROW_SIZE)
        rows[#rows + 1] = text.sjis(raw[#raw])
    end
    while #rows > 0 and rows[#rows] == "" do
        rows[#rows] = nil
    end
    return rows, table.concat(raw, "\n")
end

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    if mem.u32(MODE) ~= 1 then
        reader.forget()
        reader.cursor(nil)
        return
    end
    local rows, raw = rows_at(ROWS, ROW_COUNT)
    local name = mem.cstr(NAME, ROW_SIZE)
    if #rows == 0 then
        reader.forget("page")
    elseif reader.changed("page", name .. "\n" .. raw) then
        reader.emit{ text = table.concat(rows, "\n"), speaker = text.sjis(name), id = text.hash(raw) }
    end
    if (mem.u8(CHOOSING) or 0) == 0 then
        reader.forget("answers")
        reader.cursor(nil)
        return
    end
    local answers, answers_raw = rows_at(ANSWERS, MOST_ANSWERS)
    for i = (mem.u8(COUNT) or 0) + 1, #answers do
        answers[i] = nil
    end
    if #answers > 0 and reader.changed("answers", answers_raw) then
        reader.emit{ text = table.concat(answers, "\n"), channel = "choice", id = text.hash(answers_raw) }
    end
    reader.cursor(mem.s8(CURSOR) or -1)
end)
