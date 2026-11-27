-- Glyph cells out of a picture a game draws its text into: tiles of 1, 2 or 4 bits a pixel (as a Super Famicom or
-- a Game Boy keeps them, or packed rows of a bit a pixel), laid out some tiles a row. For a game that keeps no code
-- of what it shows, only the pixels: each cell is cut out and known by its picture, in the dictionary that
-- lib/glyphs.lua reads (`<id>.glyphs` beside the script, made from the game's own font with `yomikata rom-font
-- --glyphs`, or read off what was logged with `yomikata unread`).
--
-- A cell's picture is what lib/glyphs.lua hashes: its rows from the top, a bit a pixel (ink or not), each row a
-- whole number of bytes, the leftmost pixel in a byte's high bits. Cells are cut on a fixed grid, or, for a
-- proportional font, one after another along a row: from the first column with ink, the widest picture of the
-- dictionary's widths that it has is taken; where none fits, the cell runs to the next blank column and is logged.

local cells = {}

-- Bytes a tile, and how its planes lie: two bits a pixel as a pair of bytes a row (Super Famicom, Game Boy); four
-- as two such halves, planes 0-1 then 2-3; a bit a pixel as a byte a row.
local FORMS = {
    snes2 = { size = 16, bits = 2 }, gb2 = { size = 16, bits = 2 },
    snes4 = { size = 32, bits = 4 }, bits1 = { size = 8, bits = 1 },
}

--- A picture over `bytes`: tiles of `form` (snes2, snes4, gb2 or bits1), `across` tiles a row. Gives an object with
--- width, height and value(x, y), the pixel's value (0 to 3, or 15); nil where `bytes` ends.
function cells.picture(bytes, form, across)
    local f = FORMS[form]
    if not f then
        error("cells.picture: form is snes2, snes4, gb2 or bits1", 2)
    end
    local tiles = #bytes // f.size
    local pic = { width = across * 8, height = (tiles + across - 1) // across * 8 }

    function pic.value(x, y)
        if x < 0 or y < 0 or x >= pic.width or y >= pic.height then
            return nil
        end
        local tile = (y // 8) * across + x // 8
        if tile >= tiles then
            return nil
        end
        local at, row, bit = tile * f.size, y % 8, 7 - x % 8
        if f.bits == 1 then
            return (bytes:byte(at + row + 1) >> bit) & 1
        end
        local value = ((bytes:byte(at + 2 * row + 1) >> bit) & 1) | (((bytes:byte(at + 2 * row + 2) >> bit) & 1) << 1)
        if f.bits == 4 then
            value = value | (((bytes:byte(at + 16 + 2 * row + 1) >> bit) & 1) << 2)
                          | (((bytes:byte(at + 16 + 2 * row + 2) >> bit) & 1) << 3)
        end
        return value
    end

    return pic
end

--- Ink, by default: any value but 0. A game's own (its window's colour, a shadow) is given as a function of the
--- value, e.g. function(v) return v >= 2 end.
local function default_ink(value)
    return value ~= nil and value ~= 0
end

--- Whether column `x` has ink in rows y to y + h - 1.
function cells.inked(pic, x, y, h, ink)
    ink = ink or default_ink
    for row = y, y + h - 1 do
        if ink(pic.value(x, row)) then
            return true
        end
    end
    return false
end

--- The picture of the cell at x, y of w by h pixels, as lib/glyphs.lua hashes one (a bit a pixel), and whether it
--- is blank.
function cells.cell(pic, x, y, w, h, ink)
    ink = ink or default_ink
    local rows, blank = {}, true
    for row = y, y + h - 1 do
        local bytes = {}
        for from = 0, w - 1, 8 do
            local byte = 0
            for px = 0, 7 do
                local on = from + px < w and ink(pic.value(x + from + px, row))
                byte = byte << 1 | (on and 1 or 0)
                blank = blank and not on
            end
            bytes[#bytes + 1] = string.char(byte)
        end
        rows[#rows + 1] = table.concat(bytes)
    end
    return table.concat(rows), blank
end

--- `count` cells of w by h on a fixed grid from x, y, one after another along the row: each { picture, blank }.
function cells.fixed(pic, x, y, w, h, count, ink)
    local out = {}
    for k = 0, count - 1 do
        local picture, blank = cells.cell(pic, x + k * w, y, w, h, ink)
        out[#out + 1] = { picture = picture, blank = blank, x = x + k * w, w = w }
    end
    return out
end

--- The widths a dictionary's pictures have, from its `# widths 12 11 7 ...` line, widest first.
function cells.widths(source)
    local out = {}
    local line = (source or ""):match("#%s*widths%s+([%d%s]+)")
    for n in (line or ""):gmatch("%d+") do
        out[#out + 1] = tonumber(n)
    end
    table.sort(out, function(a, b) return a > b end)
    return out
end

--- The characters of one row of a proportional font: the strip of `h` rows from `y`, columns x0 to x1 - 1.
--- `dictionary` is lib/glyphs.lua's (its `known` hashes and `read`, which logs a picture it hasn't got), `widths`
--- the widths to try, widest first. A gap of at least `space` blank columns (default: none are counted) is a
--- wide space. Gives the row's text (a picture the dictionary hasn't got as U+FFFD) and its cells, each
--- { x, w, hash, character }.
function cells.proportional(pic, y, h, x0, x1, dictionary, widths, space, ink)
    local out, found, x, gap = {}, {}, x0, 0
    while x < x1 do
        if not cells.inked(pic, x, y, h, ink) then
            x, gap = x + 1, gap + 1
        else
            if space and #found > 0 and gap >= space then
                for _ = 1, gap // space do
                    out[#out + 1] = "\u{3000}"
                end
            end
            gap = 0
            local taken
            for _, w in ipairs(widths) do
                if x + w <= x1 then
                    local picture = cells.cell(pic, x, y, w, h, ink)
                    local hash = text.hash(picture):upper()
                    local character = dictionary.known[hash]
                    -- A dictionary's pictures end in a column with ink: a slice that ends in a blank one is the
                    -- narrower glyph's picture padded out to the same bytes, and is left for that width.
                    if character and cells.inked(pic, x + w - 1, y, h, ink) then
                        taken = { x = x, w = w, hash = hash, character = character }
                        break
                    end
                end
            end
            if not taken then
                local w = 1
                while x + w < x1 and cells.inked(pic, x + w, y, h, ink) do
                    w = w + 1
                end
                local picture = cells.cell(pic, x, y, w, h, ink)
                local character, hash = dictionary:read(picture, w, h, 1)
                taken = { x = x, w = w, hash = hash, character = character }
            end
            found[#found + 1] = taken
            out[#out + 1] = taken.character or "\u{FFFD}"
            x = x + taken.w
        end
    end
    return table.concat(out), found
end

return cells
