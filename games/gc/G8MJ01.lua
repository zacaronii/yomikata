-- reader: title ペーパーマリオRPG (Paper Mario: The Thousand-Year Door)
-- reader: reads speech bubbles: every page of a message when the message comes up
-- reader: version Japan
-- reader: no hooks
-- reader: no layout where a bubble is drawn isn't found yet

-- A DRAFT, written from notes of one session before any snapshot was read. Not for merging as it is.
--
-- How the game keeps its text: a scene's messages are Shift-JIS strings with tags in angle brackets. While a
-- speech bubble is up, the word at MESSAGE points at the bubble's own buffer, and the message it shows stands
-- 0x20 bytes into it: every page, tags and all, up to a zero byte.
--
-- Tags: <p> starts a new page; <k> waits for a press; <r>漢字<Rよみ> is furigana (the base after <r>, the reading
-- inside the tag that starts with R); <speed n>, <wait n>, <dkey>, <clear>, <wpos …> and the rest draw nothing.
-- A line break is a 0x0A byte.
--
-- Not covered, and what a snapshot must settle first:
-- - which page is on screen: until that is found, every page is emitted at once when the message comes up, which
--   is earlier than the player sees the later ones;
-- - whether MESSAGE still points at a message after its bubble has closed;
-- - the disc version this was seen on (the byte at 0x80000007), for the build check, and a `build` line;
-- - narration (the opening book), which takes another path; names, signs and menus.

local markup = require("lib.markup")

local MESSAGE = 0x80550384
local TEXT_AT = 0x20
local MEM1, MEM1_END = 0x80000000, 0x81800000

if mem.read(0x80000000, 6) ~= "G8MJ01" then
    error("this script is for another build of the game", 0)
end

--- A message's pages as { {text = …, ruby = …}, … }, from its text with the tags still in it.
local function pages(source)
    local out, page, base = {}, reader.builder(), nil
    local function close()
        local shown = page:text():gsub("\n+$", "")
        if shown ~= "" then
            out[#out + 1] = { text = shown, ruby = page:ruby() }
        end
        page = reader.builder()
    end
    for kind, value in markup.tokens(source) do
        if kind == "text" then
            if base then
                base = base .. value
            else
                if page:length() == 0 then
                    value = value:gsub("^\n+", "")
                end
                page:add(value)
            end
        elseif value == "r" then
            base = ""
        elseif base and value:sub(1, 1) == "R" then
            page:add_ruby(base, value:sub(2))
            base = nil
        elseif value == "p" then
            close()
        end
    end
    if base then
        page:add(base)
    end
    close()
    return out
end

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    local buffer = mem.u32be(MESSAGE)
    if not buffer or buffer < MEM1 or buffer + TEXT_AT >= MEM1_END then
        reader.forget("message")
        return
    end
    local bytes = mem.cstr(buffer + TEXT_AT, 4096)
    if bytes == "" then
        reader.forget("message")
        return
    end
    if not reader.changed("message", bytes) then
        return
    end
    local list = pages(text.sjis(bytes))
    local id = text.hash(bytes)
    for number, page in ipairs(list) do
        reader.emit{
            text = page.text,
            ruby = page.ruby,
            id = id .. "-" .. number,
            meta = { page = number, pages = #list },
        }
    end
end)
