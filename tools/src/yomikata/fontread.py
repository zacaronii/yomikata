"""Reading a whole font, glyph by glyph (`yomikata font-read`, docs/harness.md "A game's own font").

A font of a thousand kanji or more is read in three passes, none of them a call of the harness:

1. A second reader (manga-ocr, glyphread.py) reads each glyph alone. Alone, a glyph often comes back with more
   than it is (a kanji and the kana that would follow it in a word, a mark taken for two), so a pick is made
   from each reading: its first kanji, else the reading when it is one character, else nothing.
2. Sheets of every glyph beside its reading, drawn in a real font: a reading that isn't the glyph stands out
   beside it, where reading a sheet of glyphs alone a character is easily put a column off. What is wrong goes
   into a file of corrections (`HEX=character`, `HEX=` for a glyph that is no character).
3. Sheets of the corrections alone, each beside its glyph: the corrections are read again, against the glyph
   and not the sheet they came from. (Gyakuten Saiban 4: 12 of 377 corrections were a column off; this pass
   found them.)

Then the table, and the characters it gives to more than one glyph, which a sentence of the game's text
settles (two glyphs alike, or one misread). Signs and kana, which a reader alone gets wrong more often than
kanji, may be read off rom-font's sheets as rows (`romfont.readings`) and given here first.
"""

from __future__ import annotations

import re
from pathlib import Path

KANJI = re.compile(r"[㐀-䶿一-鿿豈-﫿々〆ヶ]")


def pick(reading: str) -> str:
    """What to take a glyph for from the second reader's reading of it alone: its first kanji, else the reading
    when it is one character, else nothing."""
    kanji = KANJI.findall(reading)
    if kanji:
        return kanji[0]
    return reading if len(reading) == 1 else ""


def ocr(glyphs: dict, reader=None) -> dict[int, str]:
    """The second reader's reading of each glyph that has ink, as it read it."""
    from .glyphread import enlarged, second_reader
    reader = reader or second_reader()
    return {code: reader(enlarged(pixels)) for code, pixels in glyphs.items() if pixels.any()}


def write_readings(path: Path, readings: dict[int, str]) -> None:
    path.write_text("".join(f"{code:X}\t{text}\n" for code, text in sorted(readings.items())), encoding="utf-8")


def load_readings(path: Path) -> dict[int, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        head, tab, text = line.partition("\t")
        if tab:
            out[int(head, 16)] = text
    return out


def corrections(text: str) -> dict[int, str]:
    """`HEX=character` a line (`HEX=` for a glyph that is no character: an icon); `#` begins a note. Only ASCII
    spaces and tabs are trimmed, so a blank glyph that is the full-width space can be given as one."""
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip(" \t\r")
        if not line:
            continue
        head, eq, ch = line.partition("=")
        head, ch = head.strip(" \t"), ch.strip(" \t")
        if not eq or len(ch) > 1:
            raise ValueError(f"a correction is HEX=character: {line!r}")
        out[int(head, 16)] = ch
    return out


def resolved(readings: dict[int, str], rows: dict[int, str] | None = None,
             fixes: dict[int, str] | None = None) -> dict[int, str]:
    """code -> character: the pick from each reading, under the rows read off sheets, under the corrections; a
    glyph left with nothing isn't in it."""
    out = {code: pick(text) for code, text in readings.items()}
    out.update(rows or {})
    out.update(fixes or {})
    return {code: ch for code, ch in out.items() if ch}


def doubles(table: dict[int, str]) -> dict[str, list[int]]:
    """The characters the table gives to more than one glyph, with their codes."""
    by: dict[str, list[int]] = {}
    for code, ch in sorted(table.items()):
        by.setdefault(ch, []).append(code)
    return {ch: codes for ch, codes in by.items() if len(codes) > 1}


def sheet(glyphs: dict, codes: list[int], labels: dict[int, str], font: Path | None, columns: int = 12):
    """Each glyph (enlarged, black on white) with its code above it and its reading beside it, drawn in `font` (a
    font file with the characters; without one, Pillow's own, which has no kanji)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    small = ImageFont.load_default()
    try:
        big = ImageFont.truetype(str(font), 34) if font else ImageFont.load_default(size=34)
    except TypeError:                               # an older Pillow: no size for its own font
        big = small
    w, h = 92, 52
    rows = max(1, -(-len(codes) // columns))
    page = Image.new("L", (columns * w, rows * h), 255)
    draw = ImageDraw.Draw(page)
    for k, code in enumerate(codes):
        x, y = (k % columns) * w, (k // columns) * h
        pixels = np.asarray(glyphs[code]) != 0
        side = max(pixels.shape)
        picture = Image.fromarray(np.where(pixels, 0, 255).astype(np.uint8)).resize(
            (round(40 * pixels.shape[1] / side), round(40 * pixels.shape[0] / side)), Image.NEAREST)
        page.paste(picture, (x + 2, y + 10))
        draw.text((x + 2, y), f"{code:X}", fill=0, font=small)
        draw.text((x + 46, y + 8), labels.get(code, ""), fill=0, font=big)
        draw.line([(x + w - 1, y), (x + w - 1, y + h)], fill=180)
    return page


def sheets(glyphs: dict, codes: list[int], labels: dict[int, str], font: Path | None, folder: Path, name: str,
           each: int = 144) -> list[Path]:
    """`sheet` in pages of `each`, written into `folder` as <name>-NN.png."""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for n, at in enumerate(range(0, len(codes), each)):
        path = folder / f"{name}-{n:02d}.png"
        sheet(glyphs, codes[at:at + each], labels, font).save(path)
        out.append(path)
    return out
