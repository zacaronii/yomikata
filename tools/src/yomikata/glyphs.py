"""A game's own font, drawn from what the game keeps: a list of glyphs in memory and their pictures in video memory.

For a game whose character codes are its own and which draws from a font of its own: where memory has a record
for each glyph saying where its picture is, the pictures can be drawn in code order and read, by a person or an
agent, once each. A fingerprint of a glyph's picture then stands for that character wherever the same picture
turns up again (another place's font, another code), so a dictionary of fingerprint to character grows as it
is read and each new font only asks for the glyphs not yet in it.

The shape read here is one seen on the PlayStation (Final Fantasy IX): records of four bytes, the glyph's place
in a texture page (u, v), its width in the low six bits of the third with a plane in bit 6, and a byte of flags;
the page 4 bits a pixel, holding two glyphs in one cell as two planes of 2 bits (1 the stroke, 2 its soft edge,
3 a shadow).

Pictures of a game's font are game data: sheets go to a scratch folder, never into the catalog. What the catalog
keeps is the reading: code to character, fingerprint to character."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

VRAM_WIDTH, VRAM_HEIGHT = 1024, 512     # a PlayStation's video memory, in 16-bit words


@dataclass(frozen=True)
class Record:
    u: int
    v: int
    width: int
    plane: int


def records(memory: bytes, count: int) -> list[Record]:
    """`count` glyph records from the start of `memory`."""
    out = []
    for i in range(count):
        u, v, w, _ = memory[4 * i:4 * i + 4]
        out.append(Record(u, v, w & 0x3F, (w >> 6) & 1))
    return out


def page(vram: bytes, x: int, y: int):
    """A 256x256 texture page of 4-bit pixels whose corner is at word (x, y) of video memory."""
    import numpy as np
    words = np.frombuffer(vram, dtype="<u2").reshape(VRAM_HEIGHT, VRAM_WIDTH)[y:y + 256, x:x + 64]
    return np.stack([(words >> (4 * i)) & 15 for i in range(4)], axis=2).reshape(words.shape[0], -1)


def picture(pixels, record: Record, height: int):
    """One glyph, as its plane's 2-bit values."""
    cell = pixels[record.v:record.v + height, record.u:record.u + record.width]
    return ((cell >> (2 * record.plane)) & 3).astype("uint8")


def fingerprint(glyph) -> str:
    """What stands for a glyph's picture: a hash of its strokes (the value 1) with its size, the empty rows and
    columns around them cut off. The same character drawn the same way has the same one wherever it is kept."""
    import numpy as np
    ink = np.asarray(glyph) == 1
    rows, columns = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
    if len(rows) == 0:
        return "0" * 16
    ink = ink[rows[0]:rows[-1] + 1, columns[0]:columns[-1] + 1]
    return hashlib.sha1(bytes(ink.shape) + np.packbits(ink).tobytes()).hexdigest()[:16]


def sheet(glyphs: list, labels: list[str], columns: int = 20, scale: int = 6):
    """The glyphs side by side, strokes white and soft edges grey, each row with its first glyph's label."""
    import numpy as np
    from PIL import Image, ImageDraw
    shade = np.array([0, 255, 150, 0], dtype=np.uint8)
    height = max((g.shape[0] for g in glyphs), default=1)
    width = max((g.shape[1] for g in glyphs), default=1)
    cell_w, cell_h, left = (width + 3) * scale, (height + 1) * scale, 60
    rows = (len(glyphs) + columns - 1) // columns
    image = Image.new("L", (left + columns * cell_w, max(1, rows) * cell_h), 0)
    draw = ImageDraw.Draw(image)
    for i, glyph in enumerate(glyphs):
        if glyph.size:
            small = Image.fromarray(shade[glyph])
            image.paste(small.resize((glyph.shape[1] * scale, glyph.shape[0] * scale), Image.NEAREST),
                        (left + (i % columns) * cell_w, (i // columns) * cell_h))
        if i % columns == 0:
            draw.text((2, (i // columns) * cell_h + cell_h // 3), labels[i], fill=200)
    return image


def read_dictionary(text: str) -> dict[str, str]:
    """A dictionary's lines, `<fingerprint>=<character>`; lines starting with # are notes."""
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value
    return out


def sort_out(glyphs: list, known: dict[str, str]) -> tuple[list[tuple[int, str, str | None]], list[int]]:
    """Each glyph's number, fingerprint and character where the dictionary has it; and the numbers of those to
    read: the first of each fingerprint it hasn't got."""
    listed, unread, seen = [], [], set()
    for number, glyph in enumerate(glyphs):
        mark = fingerprint(glyph)
        listed.append((number, mark, known.get(mark)))
        if mark not in known and mark not in seen and mark != "0" * 16:
            seen.add(mark)
            unread.append(number)
    return listed, unread


def twice(known: dict[str, str]) -> dict[str, list[str]]:
    """The characters a dictionary gives to more than one picture, with those pictures' fingerprints. A font
    draws a character one way: two pictures read as the same character mean one of the readings is wrong
    (or, seldom, that a game does keep two drawings)."""
    by: dict[str, list[str]] = {}
    for mark, character in known.items():
        by.setdefault(character, []).append(mark)
    return {character: marks for character, marks in by.items() if len(marks) > 1}


def _canvas(glyph, size: int = 16):
    import numpy as np
    ink = np.asarray(glyph) == 1
    out = np.zeros((size, size), dtype=bool)
    rows, columns = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
    if len(rows):
        ink = ink[rows[0]:rows[-1] + 1, columns[0]:columns[-1] + 1][:size, :size]
        y, x = (size - ink.shape[0]) // 2, (size - ink.shape[1]) // 2
        out[y:y + ink.shape[0], x:x + ink.shape[1]] = ink
    return out


def nearest(glyph, read: list[tuple[str, object]], count: int = 3) -> list[tuple[str, float]]:
    """The pictures already read that look most like `glyph`: (character, likeness from 0 to 1), the likest
    first. `read` is (character, picture) pairs. Likeness is how much of the two pictures' strokes overlap,
    at the best of a pixel's shift either way. For setting a new glyph beside what it could be taken for."""
    import numpy as np
    if not read:
        return []
    target = _canvas(glyph)
    shifted = [np.roll(np.roll(target, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    scored = []
    for character, picture in read:
        other = _canvas(picture)
        best = max(float((s & other).sum()) / max(1.0, float((s | other).sum())) for s in shifted)
        scored.append((character, best))
    return sorted(scored, key=lambda pair: -pair[1])[:count]



# ---- a font in a known order ----

ORDERS = ("jis", "sjis", "unicode")


def _number(order: str):
    if order == "unicode":
        return ord

    def number(ch: str):
        try:
            raw = ch.encode("euc_jp" if order == "jis" else "cp932")
        except UnicodeEncodeError:
            return None
        return int.from_bytes(raw, "big") if len(raw) == 2 else None
    return number


def _all(order: str) -> list[tuple[int, str]]:
    """Every two-byte character of the order's set, by its number."""
    out = []
    if order == "unicode":
        return [(n, chr(n)) for n in range(0x3000, 0xA000)]
    codec, leads = ("euc_jp", range(0xA1, 0xFF)) if order == "jis" else ("cp932", [*range(0x81, 0xA0), *range(0xE0, 0xF0)])
    for lead in leads:
        for second in (range(0xA1, 0xFF) if order == "jis" else range(0x40, 0xFD)):
            try:
                ch = bytes([lead, second]).decode(codec)
            except UnicodeDecodeError:
                continue
            if len(ch) == 1:
                out.append((lead << 8 | second, ch))
    return out


def in_order(readings: list[str | None], order: str = "jis") -> list[dict]:
    """A font whose glyphs stand in a known order (JIS, Shift-JIS, Unicode), checked by that order: a reading
    for each glyph (None where there is none) against the rule that the numbers must rise.

    The longest run of readings that does rise is kept (`ok`). For every other glyph the row gives what the
    order allows there: the characters between its kept neighbours (`candidates`), and when there are exactly
    as many of those as glyphs to fill, which one it must be (`must_be`). A reading left out of the run is
    `breaks`: it, or a neighbour, is wrong."""
    if order not in ORDERS:
        raise ValueError(f"order is one of: {', '.join(ORDERS)}")
    number = _number(order)
    numbers = [number(r) if r and len(r) == 1 else None for r in readings]
    # The longest rising run (patience sorting, with the way back).
    import bisect
    tails, tail_at, back = [], [], [None] * len(numbers)
    for i, n in enumerate(numbers):
        if n is None:
            continue
        k = bisect.bisect_left(tails, n)
        if k == len(tails):
            tails.append(n)
            tail_at.append(i)
        else:
            tails[k], tail_at[k] = n, i
        back[i] = tail_at[k - 1] if k else None
    kept, i = set(), tail_at[-1] if tail_at else None
    while i is not None:
        kept.add(i)
        i = back[i]
    everything = _all(order)
    keys = [n for n, _ in everything]
    rows, i = [], 0
    while i < len(readings):
        if i in kept:
            rows.append({"glyph": i, "reading": readings[i], "status": "ok"})
            i += 1
            continue
        j = i
        while j < len(readings) and j not in kept:
            j += 1
        low = numbers[i - 1] if i else -1
        high = numbers[j] if j < len(readings) else 1 << 32
        between = [ch for _, ch in everything[bisect.bisect_right(keys, low):bisect.bisect_left(keys, high)]]
        for k in range(i, j):
            row = {"glyph": k, "reading": readings[k], "status": "breaks" if numbers[k] is not None else "unread",
                   "candidates": "".join(between) if len(between) <= 40 else "".join(between[:20]) + "…" + "".join(between[-19:]),
                   "allowed": len(between)}
            if len(between) == j - i:
                row["must_be"] = between[k - i]
            rows.append(row)
        i = j
    return rows
