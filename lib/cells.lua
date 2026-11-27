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

    --- The 8 pixels of tile column `tx` on row `y` as a byte, the leftmost in the high bit: set where any plane in
    --- `planes` (a bit for each, 1 the first) is set. 0 where `bytes` ends.
    function pic.byte(tx, y, planes)
        local tile = (y // 8) * across + tx
        if y < 0 or tx < 0 or tx >= across or tile >= tiles then
            return 0
        end
        local at, row, out = tile * f.size, y % 8, 0
        if f.bits == 1 then
            return planes & 1 ~= 0 and bytes:byte(at + row + 1) or 0
        end
        if planes & 1 ~= 0 then out = out | bytes:byte(at + 2 * row + 1) end
        if planes & 2 ~= 0 then out = out | bytes:byte(at + 2 * row + 2) end
        if f.bits == 4 then
            if planes & 4 ~= 0 then out = out | bytes:byte(at + 16 + 2 * row + 1) end
            if planes & 8 ~= 0 then out = out | bytes:byte(at + 16 + 2 * row + 2) end
        end
        return out
    end

    return pic
end

--- Ink, by default: any value but 0. A game's own (its window's colour, a shadow) is given as a function of the
--- value, e.g. function(v) return v >= 2 end, or, fastest, as the planes that make ink: a number with a bit for each
--- (2 for "values 2 and 3" at 2 bits a pixel).
local function default_ink(value)
    return value ~= nil and value ~= 0
end

--- The ink of columns x0 to x1 - 1 over rows y to y + h - 1 (h at most 62): for each column, a number with bit r set
--- where row y + r has ink. Read once and kept, so that cutting a row costs a pass over its pixels.
function cells.columns(pic, y, h, x0, x1, ink)
    local cols = {}
    for x = x0, x1 - 1 do
        cols[x] = 0
    end
    if type(ink) == "number" then
        for r = 0, h - 1 do
            for tx = x0 // 8, (x1 - 1) // 8 do
                local b = pic.byte(tx, y + r, ink)
                if b ~= 0 then
                    for px = 0, 7 do
                        local x = tx * 8 + px
                        if x >= x0 and x < x1 and (b >> (7 - px)) & 1 ~= 0 then
                            cols[x] = cols[x] | (1 << r)
                        end
                    end
                end
            end
        end
    else
        ink = ink or default_ink
        for x = x0, x1 - 1 do
            local m = 0
            for r = 0, h - 1 do
                if ink(pic.value(x, y + r)) then
                    m = m | (1 << r)
                end
            end
            cols[x] = m
        end
    end
    return cols
end

--- A cell's picture from column masks (cells.columns): w columns from x, h rows; and whether it is blank.
local function packed(cols, x, w, h)
    local rows, blank = {}, true
    for r = 0, h - 1 do
        local bytes = {}
        for from = 0, w - 1, 8 do
            local byte = 0
            for px = 0, 7 do
                local c = from + px < w and cols[x + from + px] or 0
                byte = byte << 1 | ((c >> r) & 1)
            end
            blank = blank and byte == 0
            bytes[#bytes + 1] = string.char(byte)
        end
        rows[#rows + 1] = table.concat(bytes)
    end
    return table.concat(rows), blank
end

--- Whether column `x` has ink in rows y to y + h - 1.
function cells.inked(pic, x, y, h, ink)
    return cells.columns(pic, y, h, x, x + 1, ink)[x] ~= 0
end

--- The picture of the cell at x, y of w by h pixels, as lib/glyphs.lua hashes one (a bit a pixel), and whether it
--- is blank.
function cells.cell(pic, x, y, w, h, ink)
    return packed(cells.columns(pic, y, h, x, x + w, ink), x, w, h)
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
    local cols = cells.columns(pic, y, h, x0, x1, ink)
    local out, found, x, gap = {}, {}, x0, 0
    while x < x1 do
        if cols[x] == 0 then
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
                -- A dictionary's pictures end in a column with ink: a slice that ends in a blank one is the
                -- narrower glyph's picture padded out to the same bytes, and is left for that width.
                if x + w <= x1 and cols[x + w - 1] ~= 0 then
                    local picture = packed(cols, x, w, h)
                    local hash = text.hash(picture):upper()
                    local character = dictionary.known[hash]
                    if character then
                        taken = { x = x, w = w, hash = hash, character = character }
                        break
                    end
                end
            end
            if not taken then
                local w = 1
                while x + w < x1 and cols[x + w] ~= 0 do
                    w = w + 1
                end
                local character, hash = dictionary:read(packed(cols, x, w, h), w, h, 1)
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
