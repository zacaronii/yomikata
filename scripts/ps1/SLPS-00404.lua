-- reader: title 学校であった怖い話S (Gakkou de Atta Kowai Hanashi S)
-- reader: reads the text as it types, a part at a time, and a choice's question and answers with its cursor
-- reader: version SLPS-00404
-- reader: build 0fbbbf73b02ad1c3409c431ccf9be2c7f83c62c3
-- reader: no hooks
-- reader: no layout where the columns are drawn isn't looked into

-- How the game keeps what it shows.
--
-- The stretch of script being shown is laid out into a buffer of 16-bit cells (TYPED): a Shift-JIS code as one
-- number, 0x0D0A for a line break, 0xFF01 at a page's end, 0xFF02 a wait inside a row, 0xFF03 where the game
-- wraps a column by itself (13 characters), 0 at the end. COUNT says how many cells are typed so far, so the
-- text is what is on screen and grows as it types; it is said when the game waits for a press, or has stood
-- still. The text is drawn in columns, top to bottom and right to left: a column the game wrapped isn't a line
-- break here.
--
-- A choice: from its first answer until one is picked, ANSWERS says how many answers are listed. Ten records
-- (LIST) each start with a pointer to an answer where it lies in the script, as plain Shift-JIS; a record not
-- in use keeps an old pointer. The question, when the choice has one, is in the cells before the buffer
-- (QUESTION). The answers are typed through the buffer too, so it isn't read while a choice is up. The number
-- and dot drawn before each answer aren't in memory.
--
-- Checked from power-on through the first story's opening pages and choices.

local TYPED, COUNT = 0x80061858, 0x80060D18 -- cells; u32
local SHOWN, WAITING = 0x80060CFC, 0x80060D00 -- u32: not 0 while a stretch is up; 2 at a line's end, 3 at a page's
local QUESTION = 0x80061058                 -- cells
local ANSWERS, READY, CURSOR = 0x80060E94, 0x80060DDC, 0x80060DE8 -- u32 each
local LIST, RECORD, MOST_ANSWERS = 0x8015B5E0, 20, 10
local MOST = 1024 -- cells
local END, LINE, PAGE = 0x0000, 0x0D0A, 0xFF01
local STILL = 30 -- frames

if mem.u32(0x80013DD4) ~= 0x0C004FBB or mem.u32(0x800249E4) ~= 0x0C010910 or mem.u32(0x8003933C) ~= 0x0C00E4E9 then
    error("this script is for another build of the game", 0)
end

--- The page `cells` (a string of 16-bit cells) ends on, as text: what follows its last page end, or the page
--- that one ended when nothing follows it.
local function page_of(cells)
    local parts, after = {}, false
    for i = 1, #cells - 1, 2 do
        local cell = string.unpack("<I2", cells, i)
        if cell == END then
            break
        elseif cell == PAGE then
            after = true
        elseif cell < 0xFF00 or cell > 0xFF04 then
            if after then
                parts, after = {}, false
            end
            if cell == LINE then
                parts[#parts + 1] = "\n"
            elseif cell >= 0x100 then
                parts[#parts + 1] = text.sjis(string.char(cell >> 8, cell & 0xFF))
            end
        end
    end
    return table.concat(parts)
end

local last, since, said = nil, 0, ""
local page = "" -- the last page said, kept when the window empties

local function nothing()
    reader.forget()
    reader.cursor(nil)
    last, since, said, page = nil, 0, "", ""
end

reader.on_state_loaded(nothing)

--- Says what `shown` has gained since it was last said; the whole of it when it isn't the same page.
local function say(shown)
    local new = shown
    if said ~= "" and shown:sub(1, #said) == said then
        new = shown:sub(#said + 1):gsub("^\n+", "")
    end
    said, page = shown, shown
    if new:find("%S") then
        reader.emit{ text = new, id = text.hash(new) }
    end
end

reader.on_frame(function()
    local answers = mem.u32(ANSWERS) or 0
    if answers ~= 0 then
        -- A choice. Its question, unless that is how the page just said ends: it was typed there first.
        local question = page_of(mem.read(QUESTION, 2 * MOST)):gsub("\n+$", "")
        if question ~= "" and reader.changed("question", question) and page:sub(-#question) ~= question then
            page = question
            reader.emit{ text = question, id = text.hash(question) }
        end
        if mem.u32(READY) ~= 0 then
            local rows = {}
            for i = 0, math.min(answers, MOST_ANSWERS) - 1 do
                local at = mem.u32(LIST + i * RECORD)
                rows[#rows + 1] = at and text.sjis(mem.cstr(at, 96) or "") or ""
            end
            if reader.changed("answers", table.concat(rows, "\n")) then
                reader.emit{ text = table.concat(rows, "\n"), channel = "choice" }
            end
            reader.cursor(mem.u32(CURSOR) or -1)
        end
        last, since = nil, 0
        return
    end
    reader.forget("answers")
    reader.forget("question")
    reader.cursor(nil)

    local shown = ""
    if (mem.u32(SHOWN) or 0) ~= 0 then
        local count = math.min(mem.u32(COUNT) or 0, MOST)
        shown = page_of(mem.read(TYPED, 2 * count)):gsub("\n+$", "")
    end
    if shown ~= last then
        last, since = shown, 0
    else
        since = since + 1
    end
    if shown == "" then
        said = ""
    elseif shown ~= said and ((mem.u32(WAITING) or 0) ~= 0 or since >= STILL) then
        say(shown)
    end
end)
