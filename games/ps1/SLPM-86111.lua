-- reader: title メタルギアソリッド (Metal Gear Solid)
-- reader: reads the captions of scenes and films and the lines of radio calls, each as it is shown, with their furigana
-- reader: version SLPM-86111 and SLPM-86112 (the two discs share one program)
-- reader: build 7831444653135263415259495f3976fb45ead923
-- reader: no hooks
-- reader: no layout where the text is drawn isn't looked into

-- How the game keeps what it shows.
--
-- A scene, a film or a tape of the briefing brings a list of its lines with its sound: for each, when it
-- starts and how long it stays (in frames), then its text, ended by a zero byte. While a line is shown, LINE
-- points at its text; between lines it is zero. Nothing is typed out: a line is there whole, for its time.
--
-- A radio call has such a list too, and LINE follows it, but it isn't what the radio's screen shows: the call
-- has lines of its own (the same words with their commas and full stops, broken into rows another way), and
-- RADIO points at the one shown, or is zero. So while the radio is up (RADIO_UP), RADIO is read and LINE is not.
--
-- A character is two bytes, the high one first, or one byte below 0x80 for ASCII as it is. Of the two bytes
-- the low 13 bits say which character; 0x4000 is set for one drawn narrow (small kana, signs):
--   0x00xx  ASCII. The game's own marks are written with it: "|" starts a new row (in a radio call "#N"
--           does), "#{" … "}#" is a word with a reading over it, the two parted by "、", and "#ー" is a
--           long dash; 0x7F is "・"
--   0x01xx  hiragana, 0x02xx katakana: the character's place in its Unicode row
--   0x10xx  signs and the kanji of the game's fixed font, numbered as that font has them: the table
--   0x16xx  and on: kanji that a radio call brings itself; 0x1Cxx and on: those a list brings. After a list's
--           last line come the pictures of its own kanji, 36 bytes each (12 by 12, two bits a pixel), numbered
--           from 1 in the order the list first uses them, and GLYPHS points at them (RADIO_GLYPHS at a
--           call's). So one code is another kanji in every list, and nothing in memory says which. A picture
--           is known here by a hash of its 36 bytes, and the file of glyphs beside this script says which
--           kanji each hash is: they were read off the pictures. A picture that file hasn't got comes out as
--           U+FFFD; it has those of the lines played through so far.
--
-- Not covered: who speaks (a radio call shows faces and a frequency, a scene nothing); the menus, the item
-- window and the radio's own screen; the memory card's messages; names and cards drawn over a scene as
-- pictures (the staff in the opening, a place's name).

local LINE = 0x8009EC4C -- word: the text of the line shown now, or 0
local GLYPHS = 0x8009F0F0 -- word: the pictures of the list's own kanji
local RADIO = 0x800AC3EC -- word: the text of the radio's line shown now, or 0
local RADIO_UP = 0x800ABCB0 -- word: 1 while the radio's screen is up, else 0 (0x800AC218 goes the same way)
local RADIO_GLYPHS = 0x8009F0E8 -- word: the pictures of the call's own kanji
local GLYPH = 36 -- bytes of one picture
local MOST = 512 -- bytes of one line

if mem.u32(0x80012568) ~= 0x0C010101 or mem.u32(0x80057888) ~= 0x0C013C30 then
    error("this script is for another build of the game", 0)
end

local tbl = require("lib.tbl")

local source = reader.data("tbl")
if not source then
    error("SLPM-86111.tbl is missing: the game's table of characters", 0)
end
-- By a code's low 13 bits: 0x4000 says how the character is set, not which it is.
local characters = {}
for code, character in pairs(tbl.parse(source).chars) do
    characters[string.unpack(">I2", code) & 0x1FFF] = character
end

local pictures = {} -- a picture's hash -> its kanji
for hash, character in (reader.data("glyphs") or ""):gmatch("(%x+)=([^\r\n]+)") do
    pictures[hash:lower()] = character
end

local HASH, OPEN, CLOSE, BAR, COMMA, LETTER_N, DASH = 0x23, 0x7B, 0x7D, 0x7C, 0x1002, 0x4E, 0x1006
local OWN, CALLS = 0x1C00, 0x1600 -- codes above them are a list's own kanji, and a radio call's

--- The character of one code's low 13 bits.
local function character(code)
    if code > CALLS then
        local own = code > OWN
        local glyphs = mem.u32(own and GLYPHS or RADIO_GLYPHS)
        local picture = glyphs and mem.read(glyphs + GLYPH * (code - (own and OWN or CALLS) - 1), GLYPH)
        return picture and #picture == GLYPH and pictures[text.hash(picture)] or "\u{FFFD}"
    elseif code >= 0x1000 then
        return characters[code] or "\u{FFFD}"
    elseif code >= 0x200 then
        return utf8.char(0x30A0 + code - 0x200)
    elseif code >= 0x100 then
        return utf8.char(0x3040 + code - 0x100)
    end
    return characters[code] or (code >= 0x20 and code < 0x7F and string.char(code)) or "\u{FFFD}"
end

--- The codes of the text at `address`, low 13 bits each, and its bytes; nil when it doesn't end.
local function codes_at(address)
    local bytes = mem.read(address, MOST)
    local codes, i = {}, 1
    while bytes and i <= #bytes do
        local first = bytes:byte(i)
        if first == 0 then
            return codes, bytes:sub(1, i - 1)
        elseif first < 0x80 then
            codes[#codes + 1] = { first, plain = true }
            i = i + 1
        elseif i < #bytes then
            local code = string.unpack(">I2", bytes, i)
            codes[#codes + 1] = { code & 0x1FFF }
            i = i + 2
        else
            break
        end
    end
end

--- Codes as text and furigana.
local function decode(codes)
    local out = reader.builder()
    local function one(code)
        return code.plain and string.char(code[1]) or character(code[1])
    end
    local i = 1
    while i <= #codes do
        local code = codes[i]
        if not code.plain and code[1] == HASH and codes[i + 1] and codes[i + 1][1] == OPEN then
            local base, reading, into = {}, {}, nil
            local at = i + 2
            while at <= #codes and not (codes[at][1] == CLOSE and not codes[at].plain) do
                if codes[at][1] == COMMA and not into then
                    into = reading
                else
                    local part = into or base
                    part[#part + 1] = one(codes[at])
                end
                at = at + 1
            end
            if into then
                out:add_ruby(table.concat(base), table.concat(reading))
            else
                out:add(table.concat(base))
            end
            i = at + ((codes[at + 1] and codes[at + 1][1] == HASH) and 2 or 1)
        elseif not code.plain and code[1] == BAR then
            out:add("\n")
            i = i + 1
        elseif not code.plain and code[1] == HASH and codes[i + 1] and codes[i + 1][1] == LETTER_N then
            out:add("\n")
            i = i + 2
        elseif not code.plain and code[1] == HASH and codes[i + 1] and codes[i + 1][1] == DASH then
            out:add("――")
            i = i + 2
        else
            out:add(one(code))
            i = i + 1
        end
    end
    return out
end

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    -- While the radio's screen is up its own line is what is shown; the list that comes with the voice is
    -- followed all the same, and isn't shown.
    local radio = mem.u32(RADIO_UP) == 1
    local line = mem.u32(radio and RADIO or LINE)
    if not line or line < 0x80010000 or line >= 0x80200000 then
        reader.forget("line")
        return
    end
    local codes, bytes = codes_at(line)
    if not codes or #codes == 0 then
        return
    end
    if reader.changed("line", line .. bytes) then
        local built = decode(codes)
        if built:text() ~= "" then
            reader.emit{ text = built:text(), ruby = built:ruby(), channel = "dialogue",
                         id = text.hash(bytes .. built:text()), meta = { radio = radio } }
        end
    end
end)
