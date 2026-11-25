-- Text a game keeps as the tiles of a window: a buffer of cells, each a tile's number (with its colour bits),
-- row after row, read through a table of cell to character (lib/tbl.lua).

local tbl = require("lib.tbl")

local tiles = {}

--- The rows of a window as lines of text. `read` gives the bytes of one row (its number from 0); a row is
--- `cells` cells of `unit` bytes. Wide spaces at a row's end are left out, and empty rows at the window's end.
--- A cell the table hasn't got is U+FFFD.
function tiles.rows(read, count, parsed, unit)
    local lines = {}
    for row = 0, count - 1 do
        local bytes = read(row)
        lines[#lines + 1] = bytes and tbl.trim_end(tbl.decode(bytes, parsed, unit)) or ""
    end
    while #lines > 0 and lines[#lines] == "" do
        lines[#lines] = nil
    end
    return lines
end

--- What a window that types and scrolls has gained: `now` and `before` are its lines at two moments. The
--- lines of `now` after those it shares with the end of `before`, the last shared line carried on from where
--- it stood; all of `now` when they share nothing.
function tiles.gained(before, now)
    for skip = 0, #before - 1 do
        local kept = #before - skip
        local fits = kept <= #now
        for i = 1, fits and kept or 0 do
            local old, new = before[skip + i], now[i]
            if i < kept and old ~= new or i == kept and new:sub(1, #old) ~= old then
                fits = false
                break
            end
        end
        if fits then
            local out = {}
            local rest = now[kept]:sub(#before[#before] + 1)
            if rest ~= "" then
                out[1] = rest
            end
            for i = kept + 1, #now do
                out[#out + 1] = now[i]
            end
            return out
        end
    end
    return now
end

return tiles
