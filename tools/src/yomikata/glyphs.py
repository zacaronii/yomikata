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
