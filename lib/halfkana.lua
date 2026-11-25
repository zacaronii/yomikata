-- Hiragana kept as single bytes: a game that writes its script in Shift-JIS with the half-width katakana codes
-- standing for hiragana (one byte each, voiced marks as bytes of their own), and draws them as hiragana.
-- Katakana and kanji are ordinary two-byte Shift-JIS in such a script.

local halfkana = {}

local PLAIN = {
    ["ｦ"] = "を", ["ｧ"] = "ぁ", ["ｨ"] = "ぃ", ["ｩ"] = "ぅ", ["ｪ"] = "ぇ", ["ｫ"] = "ぉ", ["ｬ"] = "ゃ", ["ｭ"] = "ゅ",
    ["ｮ"] = "ょ", ["ｯ"] = "っ", ["ｱ"] = "あ", ["ｲ"] = "い", ["ｳ"] = "う", ["ｴ"] = "え", ["ｵ"] = "お", ["ｶ"] = "か",
    ["ｷ"] = "き", ["ｸ"] = "く", ["ｹ"] = "け", ["ｺ"] = "こ", ["ｻ"] = "さ", ["ｼ"] = "し", ["ｽ"] = "す", ["ｾ"] = "せ",
    ["ｿ"] = "そ", ["ﾀ"] = "た", ["ﾁ"] = "ち", ["ﾂ"] = "つ", ["ﾃ"] = "て", ["ﾄ"] = "と", ["ﾅ"] = "な", ["ﾆ"] = "に",
    ["ﾇ"] = "ぬ", ["ﾈ"] = "ね", ["ﾉ"] = "の", ["ﾊ"] = "は", ["ﾋ"] = "ひ", ["ﾌ"] = "ふ", ["ﾍ"] = "へ", ["ﾎ"] = "ほ",
    ["ﾏ"] = "ま", ["ﾐ"] = "み", ["ﾑ"] = "む", ["ﾒ"] = "め", ["ﾓ"] = "も", ["ﾔ"] = "や", ["ﾕ"] = "ゆ", ["ﾖ"] = "よ",
    ["ﾗ"] = "ら", ["ﾘ"] = "り", ["ﾙ"] = "る", ["ﾚ"] = "れ", ["ﾛ"] = "ろ", ["ﾜ"] = "わ", ["ﾝ"] = "ん",
    ["｡"] = "。", ["｢"] = "「", ["｣"] = "」", ["､"] = "、", ["･"] = "・", ["ｰ"] = "ー",
}
local VOICED = {
    ["う"] = "ゔ", ["か"] = "が", ["き"] = "ぎ", ["く"] = "ぐ", ["け"] = "げ", ["こ"] = "ご", ["さ"] = "ざ",
    ["し"] = "じ", ["す"] = "ず", ["せ"] = "ぜ", ["そ"] = "ぞ", ["た"] = "だ", ["ち"] = "ぢ", ["つ"] = "づ",
    ["て"] = "で", ["と"] = "ど", ["は"] = "ば", ["ひ"] = "び", ["ふ"] = "ぶ", ["へ"] = "べ", ["ほ"] = "ぼ",
}
local HALF_VOICED = { ["は"] = "ぱ", ["ひ"] = "ぴ", ["ふ"] = "ぷ", ["へ"] = "ぺ", ["ほ"] = "ぽ" }

--- Text decoded from such a script (text.sjis of its bytes), with its half-width katakana as the hiragana they
--- stand for and each voiced mark joined to the kana before it. A mark with nothing to join stays, full-width.
function halfkana.fold(decoded)
    local out = {}
    for _, code in utf8.codes(decoded) do
        local ch = utf8.char(code)
        local last = out[#out]
        if ch == "ﾞ" then
            if last and VOICED[last] then
                out[#out] = VOICED[last]
            else
                out[#out + 1] = "゛"
            end
        elseif ch == "ﾟ" then
            if last and HALF_VOICED[last] then
                out[#out] = HALF_VOICED[last]
            else
                out[#out + 1] = "゜"
            end
        else
            out[#out + 1] = PLAIN[ch] or ch
        end
    end
    return table.concat(out)
end

return halfkana
