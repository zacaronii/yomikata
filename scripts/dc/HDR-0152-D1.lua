-- reader: title サクラ大戦3 ～巴里は燃えているか～ (Sakura Taisen 3: Paris wa Moeteiru ka, Dreamcast)
-- reader: reads the message window's page once it has typed out
-- reader: version V1.003 (disc 1)
-- reader: build HDR-0152-D1-V1.003
-- reader: no hooks
-- reader: no layout the window's place on screen isn't looked into

-- How the game keeps what it shows.
--
-- A scene's lines are Shift-JIS strings, each ending with a zero byte; "//" in one starts a new row. A line
-- that is spoken is kept whole in the scene's voice table (the line, then the mouth's shapes for it); one that
-- isn't is copied into a buffer in the heap before it is shown. Either way the message window's object, which
-- the word at OBJECT_AT points at, holds where the page starts (START bytes in) and where the typing has got
-- to (TYPED bytes in): TYPED walks the page as it types, and drops to 0 when the whole page is shown, whether it
-- typed out or a press cut it short. So a page is said when TYPED goes from a place in the page to 0.
--
-- Not covered: who speaks (the window shows a face, not a name), a choice's answers (another box), the menus,
-- the screens before the story, and the other discs (not looked at). A state loaded while a page stands
-- doesn't say that page: it is said once a page has been seen typing.

local OBJECT_AT = 0x8C2B392C
local START, TYPED = 0x44, 0x48
local LOW, HIGH = 0x8C000000, 0x8D000000 -- main memory
local MOST = 512

-- Two codes the game's font draws otherwise than Shift-JIS has them: 0x81B8 (∈) is "!!" in one place, 0x81AC
-- (〓) is "!?". Each is given as the one character Unicode has for it.
local GLYPHS = { ["\x81\xB8"] = "‼", ["\x81\xAC"] = "⁉" }

local function inside(a)
    return a and a >= LOW and a < HIGH
end

-- A page as shown: "//" is a new row, and the two glyphs above are put in. A two-byte character's second
-- byte is never "/", so the page is walked a character at a time.
local function shown_of(raw)
    local out, run, i = {}, {}, 1
    local function flush()
        if #run > 0 then
            out[#out + 1] = text.sjis(table.concat(run))
            run = {}
        end
    end
    while i <= #raw do
        local b = raw:byte(i)
        if (b >= 0x81 and b <= 0x9F) or (b >= 0xE0 and b <= 0xFC) then
            local two = raw:sub(i, i + 1)
            if GLYPHS[two] then
                flush()
                out[#out + 1] = GLYPHS[two]
            else
                run[#run + 1] = two
            end
            i = i + 2
        elseif raw:sub(i, i + 1) == "//" then
            flush()
            out[#out + 1] = "\n"
            i = i + 2
        else
            run[#run + 1] = raw:sub(i, i)
            i = i + 1
        end
    end
    flush()
    return table.concat(out)
end

-- The build: the disc's product number and version, from its boot header, which the console loads at
-- 0x8C008000.
if mem.read(0x8C008040, 16) ~= "HDR-0152  V1.003" then
    error("this script is for another build of the game", 0)
end

local typing = false

reader.on_state_loaded(function()
    reader.forget()
    typing = false
end)

reader.on_frame(function()
    local object = mem.u32(OBJECT_AT)
    if not inside(object) then
        typing = false
        return
    end
    local start, typed = mem.u32(object + START), mem.u32(object + TYPED)
    if not typed then
        return
    end
    if typed ~= 0 then
        typing = inside(typed) and inside(start) and typed >= start
        return
    end
    if not typing then
        return
    end
    typing = false
    local raw = mem.cstr(start, MOST)
    if not raw or raw == "" then
        return
    end
    local shown = shown_of(raw)
    if shown:find("%S") then
        reader.emit{ text = shown, channel = "dialogue", id = text.hash(raw) }
    end
end)
