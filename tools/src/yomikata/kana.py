"""Hiragana kept as single bytes: some Shift-JIS games store their hiragana in the half-width katakana codes
(0xA1-0xDF, one byte each, the voiced marks as bytes of their own) and keep katakana as ordinary two-byte codes.
Decoded as Shift-JIS that reads as half-width katakana; `HIRAGANA_RULES` is the `cleanup` that turns it back, so an
entry can carry the rules as data and every client reads the same text (docs/format.md, Cleanup)."""

from __future__ import annotations

import unicodedata

from .cleanup import apply

HALFWIDTH = "ｦｧｨｩｪｫｬｭｮｯｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ"
PUNCTUATION = {"｡": "。", "｢": "「", "｣": "」", "､": "、", "･": "・", "ｰ": "ー", "ﾞ": "゛", "ﾟ": "゜"}


def _hiragana(halfwidth: str) -> str:
    """One half-width katakana (with its voiced mark, if any) as one hiragana."""
    full = unicodedata.normalize("NFKC", halfwidth)
    return chr(ord(full) - 0x60) if len(full) == 1 and "ァ" <= full <= "ヶ" else ""


def _rules() -> list[dict]:
    voiced = [{"regex": c + mark, "replace": _hiragana(c + mark)}
              for mark in "ﾞﾟ" for c in HALFWIDTH if _hiragana(c + mark)]
    return voiced + [{"remap": {**{c: _hiragana(c) for c in HALFWIDTH}, **PUNCTUATION}}]


# Voiced pairs first (ｶﾞ -> が), then the single characters; a mark left over stands alone (゛).
HIRAGANA_RULES = _rules()


VOICED = set("ｳｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾊﾋﾌﾍﾎ")      # what a ﾞ can follow
SEMI_VOICED = set("ﾊﾋﾌﾍﾎ")                      # ... a ﾟ
PALATAL = set("ｷｼﾁﾆﾋﾐﾘ")                        # ... a small ｬ ｭ ｮ (after the kana's own mark)


def is_halfwidth_kana(c: str) -> bool:
    return "ｦ" <= c <= "ﾝ" and c != "ｰ"


def spelling(text: str) -> tuple[int, int, int]:
    """Half-width kana in `text`, the voiced marks among them, and the marks and small ｬ ｭ ｮ that sit where
    Japanese doesn't put them. Hiragana written this way has a mark on about a tenth of its kana and none
    misplaced; bytes that only happen to fall in the range have them anywhere."""
    kana = marks = misplaced = 0
    for i, c in enumerate(text):
        prev = text[i - 1] if i else ""
        if c in "ﾞﾟ":
            marks += 1
            misplaced += prev not in (VOICED if c == "ﾞ" else SEMI_VOICED)
        elif c in "ｬｭｮ":
            misplaced += (text[i - 2] if prev in "ﾞﾟ" and i > 1 else prev) not in PALATAL
        kana += is_halfwidth_kana(c)
    return kana, marks, misplaced


def fold(text: str) -> str:
    return apply(text, HIRAGANA_RULES)
