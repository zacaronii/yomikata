-- reader: title Fate/stay night [Réalta Nua] (Fate/stay night: Realta Nua)
-- reader: reads the story's text as the page gains it, a text for each press, with its furigana
-- reader: version SLPM-66513
-- reader: build SLPM-66513-f6c5993b
-- reader: no hooks
-- reader: no layout the text fills the picture, and where its rows are drawn isn't looked into

-- How the game keeps what it shows.
--
-- The story is a script of text lines and commands. What is on the page is kept as one string at PAGE, made
-- ready for drawing: Shift-JIS, ended by a zero byte, with marks that begin with a percent sign among the
-- text ("%ch[ff,ff,ff,0,0,0]" a colour, "%s0.9" a size, "%i0" an indent, "%n" a new row, "%l3,0" a dash
-- three characters long, "%[" a word "%:" its reading "%]"). Each line the script reads is added to its end, a press's worth at a time (a
-- sentence may be several of the script's lines), and a new page begins the string anew.
--
-- So what the page has gained since the last look is what the player was just given: it is said once the
-- string has stood still, a text for each press.
--
-- Not covered: who speaks (the game shows no name); choices; the menus, the backlog and the memory card's
-- messages.

local PAGE = 0x002BC7D8 -- the page so far, with its marks
local MOST = 8192
local STILL = 3 -- ticks the page stands before what it gained is said

if mem.u32(0x00100060) ~= 0x0C09342E or mem.u32(0x001C6290) ~= 0x0C07170C then
    error("this script is for another build of the game", 0)
end

--- Text without its marks. A mark is a percent sign, letters, then a list in square brackets or numbers
--- with commas; "%n" is a new row and "%l" with a length a dash.
local function plain(line)
    line = line:gsub("%%l(%d+),%d+", function(n)
        return string.rep("\u{2015}", math.min(tonumber(n), 16))
    end)
    line = line:gsub("%%n", "\n")
    line = line:gsub("%%%a+%b[]", "")
    line = line:gsub("%%%a+[%d.,%-]*", "") -- "%[", "%:" and "%]" have no letter and are left for the readings
    return (line:gsub("^[\n ]+", ""):gsub("[\n ]+$", ""))
end

local waiting, stood = nil, 0 -- the page of the tick before, and for how many ticks it has been the same
local said = "" -- the page as far as it was said

reader.on_state_loaded(function()
    reader.forget()
    waiting, stood, said = nil, 0, ""
end)

reader.on_frame(function()
    local bytes = mem.cstr(PAGE, MOST)
    if not bytes then
        return
    end
    if bytes ~= waiting then
        waiting, stood = bytes, 0
        return
    end
    stood = stood + 1
    if stood ~= STILL or bytes == said then
        return
    end
    -- What was added to the page; a page that doesn't begin as the one before did is a new one.
    local gained = bytes:sub(1, #said) == said and bytes:sub(#said + 1) or bytes
    said = bytes
    -- "%[" word "%:" reading "%]" is a word with its reading over it.
    local built, rest = reader.builder(), plain(text.sjis(gained))
    while true do
        local from, to, word, reading = rest:find("%%%[(.-)%%:(.-)%%%]")
        if not from then
            break
        end
        built:add(rest:sub(1, from - 1))
        built:add_ruby(word, reading)
        rest = rest:sub(to + 1)
    end
    built:add(rest)
    local shown = built:text()
    if shown:find("[^%s\u{3000}]") and not shown:find("\u{FFFD}", 1, true) then
        reader.emit{ text = shown, ruby = built:ruby(), channel = "dialogue", id = text.hash(gained) }
    end
end)
