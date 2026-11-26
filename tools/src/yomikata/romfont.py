"""A game's whole font read out of its cartridge, so that its table is filled once and not a kanji at a time.

A game with codes of its own has a font of its own, and a table read in play has only the characters the
stretch played showed. Where the font lies in the cartridge as it is (not packed) and a code is a glyph's
number in it, every glyph can be drawn in code order on numbered sheets (scratch: a font is game data), read
a row at a time, and written into the table. What was read in play serves as the check: the same code read
twice, once in a sentence and once off the sheet.

Three steps, each a function here and a part of `yomikata rom-font`:
  - `find`: where the font is, from a picture of the game's text (a snap's PNG): pieces of the picture are
    looked for in the cartridge in the forms a font is kept in;
  - `glyphs` and font.sheet: the glyphs from a place, a form and a first code;
  - `readings` and `table`: rows read off a sheet, held against a table read in play, written as a table."""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np

# How a glyph lies in the cartridge. tiles4: four 8x8 tiles of 4 bits a pixel (the low nibble the left pixel),
# top left, top right, bottom left, bottom right: 128 bytes, a 16x16 glyph (Game Boy Advance).
# rows1: rows of a bit a pixel, the high bit left, `width` pixels in whole bytes, `height` rows.
FORMS = ("tiles4", "rows1")


def cartridge(path: Path) -> bytes:
    """A cartridge's bytes: the file, or the largest file of a .zip."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            return z.read(max(z.infolist(), key=lambda i: i.file_size))
    return path.read_bytes()


def size(form: str, width: int = 16, height: int = 16) -> int:
    """Bytes a glyph takes."""
    if form == "tiles4":
        return 128
    if form == "rows1":
        return (width + 7) // 8 * height
    raise ValueError(f"form is one of {', '.join(FORMS)}")


def glyph(data: bytes, form: str, width: int = 16, height: int = 16) -> np.ndarray:
    """One glyph's pixels, 1 where there is ink."""
    raw = np.frombuffer(data, dtype=np.uint8)
    if form == "tiles4":
        out = np.zeros((16, 16), dtype=np.uint8)
        for i, (row, column) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
            tile = np.empty(64, dtype=np.uint8)
            tile[0::2], tile[1::2] = raw[32 * i:32 * i + 32] & 15, raw[32 * i:32 * i + 32] >> 4
            out[8 * row:8 * row + 8, 8 * column:8 * column + 8] = tile.reshape(8, 8) != 0
        return out
    return np.unpackbits(raw).reshape(height, -1)[:, :width]


def glyphs(rom: bytes, at: int, first: int, count: int, form: str = "tiles4", width: int = 16,
           height: int = 16) -> dict[int, np.ndarray]:
    """code -> pixels for `count` glyphs from `at`, the first of them the code `first`. Stops at the cartridge's end."""
    each, out = size(form, width, height), {}
    for n in range(count):
        data = rom[at + n * each:at + (n + 1) * each]
        if len(data) < each:
            break
        out[first + n] = glyph(data, form, width, height)
    return out


def rows_hex(pixels: np.ndarray) -> str:
    """A glyph as the harness's glyph_pairs writes one, for `yomikata glyph-read`: its rows as bits, in hex."""
    return np.packbits(pixels.astype(np.uint8), axis=1).tobytes().hex()


def find(rom: bytes, ink: np.ndarray, most: int = 12) -> list[dict]:
    """Where pieces of a picture of text lie in the cartridge: `ink` is the picture as true where there is ink.
    8x8 pieces at every place are looked for as tiles of 4 bits a pixel in any one colour, and as a bit a pixel
    with the high bit left or right. A font shows as many pieces found near each other; a piece found in many
    places says nothing. Gives, for each form, how many pieces were found and some of them."""
    found: dict[str, list] = {}
    for y in range(ink.shape[0] - 7):
        for x in range(ink.shape[1] - 7):
            piece = ink[y:y + 8, x:x + 8]
            if not 12 <= int(piece.sum()) <= 40 or not piece[0].any() or not piece[:, 0].any():
                continue                         # too plain to be told from anything, or not at an edge of ink
            bits = np.packbits(piece.astype(np.uint8), axis=1).tobytes()
            flipped = np.packbits(piece[:, ::-1].astype(np.uint8), axis=1).tobytes()
            forms = [("a bit a pixel, high bit left", bits), ("a bit a pixel, high bit right", flipped)]
            for colour in range(1, 16):
                packed = bytes((colour if row[k] else 0) | ((colour if row[k + 1] else 0) << 4)
                               for row in piece for k in range(0, 8, 2))
                forms.append((f"tiles of 4 bits, colour {colour}", packed))
            for name, needle in forms:
                at = rom.find(needle)
                if at >= 0 and rom.find(needle, at + 1) < 0:              # once only: a place, not a pattern
                    found.setdefault(name, []).append({"x": x, "y": y, "at": at})
    out = []
    for name, places in sorted(found.items(), key=lambda kv: -len(kv[1])):
        ats = sorted(p["at"] for p in places)
        out.append({"form": name, "pieces": len(places), "from": f"0x{ats[0]:X}", "to": f"0x{ats[-1]:X}",
                    "some": [{**p, "at": f"0x{p['at']:X}"} for p in places[:most]]})
    return out


def readings(text: str, blanks: set[int] = frozenset()) -> dict[int, str]:
    """code -> character from rows read off a sheet: a line is a code in hex (the row's first glyph, or wherever
    the reading starts) and then the characters as they stand, one for each glyph that isn't blank; `?` for one
    not read. Spaces between characters don't count; a line beginning with # is a note."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        code = int(head, 16)
        for ch in "".join(rest.split()):
            while code in blanks:
                code += 1
            if ch != "?":
                if code in out and out[code] != ch:
                    raise ValueError(f"{code:X} is read twice: {out[code]} and {ch}")
                out[code] = ch
            code += 1
    return out


def held(read: dict[int, str], known: dict[int, str]) -> dict:
    """The sheet's readings against a table read in play: the codes both have, how many agree, and those that
    don't (each is one of the two read wrong: read it again in a sentence)."""
    both = sorted(set(read) & set(known))
    differ = [{"code": f"{c:X}", "sheet": read[c], "play": known[c]} for c in both if read[c] != known[c]]
    twice: dict[str, list[int]] = {}
    for code, ch in read.items():
        twice.setdefault(ch, []).append(code)
    return {"read": len(read), "also read in play": len(both), "agreeing": len(both) - len(differ), "differ": differ,
            "one character for two glyphs": {ch: [f"{c:X}" for c in codes] for ch, codes in sorted(twice.items()) if len(codes) > 1}}
