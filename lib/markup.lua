-- Text with tags in angle brackets, as many games write their messages: "<speed 2>text<k>".
--
-- Works on bytes, so it serves UTF-8 and Shift-JIS alike: neither has "<" or ">" inside a character.

local markup = {}

--- Walks a text piece by piece. Each step gives "text" and a stretch between tags, or "tag" and what stands
--- between its brackets ("speed 2"). A "<" that is never closed ends the walk: the rest isn't a text yet.
function markup.tokens(s)
    local pos = 1
    return function()
        if pos > #s then
            return nil
        end
        local open = s:find("<", pos, true)
        if open == pos then
            local close = s:find(">", pos, true)
            if not close then
                pos = #s + 1
                return nil
            end
            local body = s:sub(pos + 1, close - 1)
            pos = close + 1
            return "tag", body
        end
        local stop = (open or #s + 1) - 1
        local piece = s:sub(pos, stop)
        pos = stop + 1
        return "text", piece
    end
end

--- A tag's name and the rest: "speed 2" gives "speed", "2".
function markup.name(body)
    local name, rest = body:match("^(%S+)%s*(.*)$")
    return name or "", rest or ""
end

return markup
