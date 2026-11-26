-- A Game Boy's (or Game Boy Color's) picture as its tile maps lay it out, read from video memory (the script's
-- `vram` region: bank 0 from 0x0000, a Color's bank 1 from 0x2000): what a script reads where a game draws a
-- menu or a box from tiles and no text of it is anywhere else. A cell's picture comes out in the form
-- lib/glyphs.lua hashes, so that a dictionary says which character it is.

local gbtiles = {}

--- The picture of the cell at column `x`, row `y` (from 0, 32 a row) of the tile map at `map` (0x1800 or
--- 0x1C00 in video memory): its 8 rows from the top, 2 bits a pixel, the leftmost pixel in a byte's high bits
--- (16 bytes), with the cell's flips done. `signed` for a game whose tile numbers count from 0x1000 as signed
--- bytes (0x8800 addressing), else from 0. On a Game Boy Color the cell's attributes (at map + 0x2000) choose
--- the tile's bank. Nil when the region can't be read there. The second result is true for a blank cell: every
--- pixel the same.
function gbtiles.cell(vram, map, x, y, signed)
    local at = map + y * 32 + x
    local number, attributes = vram.u8(at), vram.u8(0x2000 + at) or 0
    if not number then
        return nil
    end
    local index = signed and (number < 128 and number or number - 256) or number
    local bytes = vram.read(0x2000 * ((attributes >> 3) & 1) + (signed and 0x1000 or 0) + 16 * index, 16)
    if not bytes or #bytes < 16 then
        return nil
    end
    local rows, blank, first = {}, true, nil
    for row = 0, 7 do
        local source = (attributes & 0x40 ~= 0) and 7 - row or row  -- flipped top to bottom
        local low, high = bytes:byte(2 * source + 1), bytes:byte(2 * source + 2)
        local value = 0
        for px = 0, 7 do
            local bit = (attributes & 0x20 ~= 0) and px or 7 - px      -- flipped left to right
            local pixel = (((high >> bit) & 1) << 1) | ((low >> bit) & 1)
            first = first or pixel
            blank = blank and pixel == first
            value = value << 2 | pixel
        end
        rows[#rows + 1] = string.char(value >> 8, value & 0xFF)
    end
    return table.concat(rows), blank
end

return gbtiles
