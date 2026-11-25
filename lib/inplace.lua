-- A script the game reads in place: it keeps no copy of the text it shows, only how far into its script it
-- has read, with instructions between the texts. What is on screen is the page that position is on, as far
-- as it has been read (docs/format.md says the same of a layout's `script`).
--
-- A text starts after a mark and stops at an end code. A mark right after an end code that has no page break
-- before it carries the page on. A page break inside a text starts the page anew, unless only end codes
-- follow it: the page it ended is still up.

local inplace = {}

-- Bytes i (from 0) up to j, as Python slices them: nothing before the start, short at the end.
local function cut(s, i, j)
    if i < 0 then
        return ""
    end
    return s:sub(i + 1, j)
end

--- What `data` holds from `start` to its end, less the pages that are off the screen: one piece, or with
--- `rows` a piece for each text.
local function pages(data, start, game, mark, rows)
    local unit = game.unit
    local tail = #data
    while tail - unit >= start and game.ends[cut(data, tail - unit, tail)] do
        tail = tail - unit
    end
    local out, piece, i = {}, {}, start
    while i + unit <= #data do
        local code = cut(data, i, i + unit)
        local size = game.sizes[code]
        if not size and mark and game.ends[code] and cut(data, i + unit, i + unit + #mark) == mark then
            i = i + unit + #mark -- where two texts of the page join
            if rows then
                out[#out + 1] = table.concat(piece)
                piece = {}
            end
        elseif not size and game.breaks[code] and i + unit < tail then
            out, piece, i = {}, {}, i + unit -- a new page: what came before is off the screen
        else
            piece[#piece + 1] = cut(data, i, i + (size or unit))
            i = i + (size or unit)
        end
    end
    out[#out + 1] = table.concat(piece)
    return out
end

--- The page the reading position is on. `both` is `size` bytes of script up to the position, then as many
--- bytes after it as `mark` has. `game` has unit, ends, breaks (sets of codes) and sizes (codes with
--- arguments -> their bytes). Gives a list of pieces; an empty list when the position isn't in a text, or a
--- text is about to start a new page.
function inplace.page(both, size, game, mark, rows)
    local unit = game.unit
    local data, ahead = both:sub(1, size), both:sub(size + 1)
    local function code(i)
        return cut(data, i, i + unit)
    end
    local function joins(q) -- a mark at q carries the page on
        return game.ends[code(q - unit)] and not game.breaks[code(q - 2 * unit)]
    end
    local p, start = #data, nil
    if ahead == mark and not joins(p) then
        return {}
    end
    if game.ends[code(p - unit)] then -- the text has just ended
        p = p - unit
    end
    while true do
        while p >= #mark and cut(data, p - #mark, p) ~= mark and not game.ends[code(p - unit)] do
            p = p - unit
        end
        if p < #mark or cut(data, p - #mark, p) ~= mark then
            break -- out of what was read, or a second end code: not a text
        end
        start = p
        local q = p - #mark
        if not joins(q) then
            break
        end
        p = q - unit -- on through the text before
    end
    if not start then
        return {}
    end
    return pages(data, start, game, mark, rows)
end

--- A piece as text: an end code, a page break or a clear is a blank line, `game.newlines` a line break,
--- `game.silent` nothing, a code with arguments nothing, a code the table hasn't got U+FFFD. Line breaks at
--- the end are dropped. `characters` is code -> character.
function inplace.decode(piece, game, characters)
    local out, i = {}, 1
    while i + game.unit - 1 <= #piece do
        local code = piece:sub(i, i + game.unit - 1)
        local size = game.sizes[code]
        if size then
            i = i + size
        else
            if game.ends[code] or game.breaks[code] then
                out[#out + 1] = "\n\n"
            elseif game.newlines[code] then
                out[#out + 1] = "\n"
            elseif not game.silent[code] then
                out[#out + 1] = characters[code] or "\u{FFFD}"
            end
            i = i + game.unit
        end
    end
    return (table.concat(out):gsub("\n+$", ""))
end

return inplace
