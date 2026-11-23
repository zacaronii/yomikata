"""A game's font out of video memory, as a labelled sheet to read the table off, and the table's skeleton.

A game with its own codes usually has its font in video memory while text is on screen, and its codes are the
glyphs' places in it. Two shapes are cut:

- **tiles** (cartridge consoles): 8x8 tiles at a base address, 2 or 4 bits a pixel, bit planes (Super Famicom, Game
  Boy) or packed nibbles (Game Boy Advance). A code is the tile's number; a character may be two tiles tall.
- **a grid** in a PS1 texture page: cells of any size in the 4-bit view `yomikata vram` writes (`-nibbles.png`),
  read left to right, top to bottom. A code is the cell's place, counted from `first`.

The sheet shows every glyph with its code. Nothing names the glyphs: matching them against a reference font by
machine proved unreliable, so a person reads the sheet, helped by the pairs `yomikata where --table` already found.
The sheet is game data (the font): it belongs in a scratch folder. The table that comes of it holds only codes
and characters, and that is what goes in the catalog."""

from __future__ import annotations

import numpy as np

PS1_WIDTH = 4096            # the 4-bit view's width: four pixels to each of the 1024 words in a row


def tiles(vram: bytes, base: int, bpp: int = 2, count: int = 1024, packed: bool = False) -> np.ndarray:
    """`count` 8x8 tiles from `base` as colour numbers, shape (count, 8, 8). Planes: the Super Famicom's order (two
    planes row by row, then the next two), which with 2 bits is the Game Boy's too. Packed: a nibble a pixel, the
    left one in the low bits (Game Boy Advance, 4 bits only)."""
    size = 8 * bpp
    count = max(0, min(count, (len(vram) - base) // size))
    raw = np.frombuffer(vram, dtype=np.uint8, count=count * size, offset=base).reshape(count, size)
    if packed:
        if bpp != 4:
            raise ValueError("packed tiles are 4 bits a pixel")
        rows = raw.reshape(count, 8, 4)
        out = np.empty((count, 8, 8), dtype=np.uint8)
        out[:, :, 0::2], out[:, :, 1::2] = rows & 15, rows >> 4
        return out
    out = np.zeros((count, 8, 8), dtype=np.uint8)
    for plane in range(bpp):
        rows = raw[:, 16 * (plane // 2) + (plane % 2):16 * (plane // 2) + 16:2]      # this plane's byte of each row
        out |= (np.unpackbits(rows[:, :, None], axis=2) << plane).astype(np.uint8)
    return out


def grid(vram: bytes, x: int, y: int, width: int, height: int, columns: int, rows: int) -> np.ndarray:
    """Cells of `width` x `height` from a PS1 4-bit texture, shape (columns * rows, height, width); `x` and `y` as
    in the 4-bit view (0..4095, 0..511)."""
    by = np.frombuffer(vram, dtype=np.uint8).reshape(512, 2048)
    nib = np.empty((512, PS1_WIDTH), dtype=np.uint8)
    nib[:, 0::2], nib[:, 1::2] = by & 15, by >> 4
    if x < 0 or y < 0 or x + width * columns > PS1_WIDTH or y + height * rows > 512:
        raise ValueError("the grid runs out of video memory")
    area = nib[y:y + height * rows, x:x + width * columns]
    return area.reshape(rows, height, columns, width).swapaxes(1, 2).reshape(rows * columns, height, width)


def find_base(vram: bytes, used: list[int], bpp: int = 2, packed: bool = False, step: int = 0x800) -> list[tuple[int, int]]:
    """Where the tiles are, given tile numbers a text buffer holds: (base, score) for each base tried, best first.
    The cell that fills most of the buffer is the blank; at the right base its tile is one flat colour and the
    others are each a different picture. The score is how many of the others are pictures no other one has."""
    if not used:
        return []
    numbers, counts = np.unique(np.array(used), return_counts=True)
    blank = int(numbers[np.argmax(counts)])
    out = []
    for base in range(0, len(vram), step):
        t = tiles(vram, base, bpp, int(numbers.max()) + 1, packed)
        if len(t) <= numbers.max() or len(np.unique(t[blank])) != 1:
            continue
        others = [t[int(n)].tobytes() for n in numbers if n != blank]
        score = len({o for o in others if len(set(o)) > 1})
        if score:
            out.append((base, score))
    return sorted(out, key=lambda b: -b[1])


def pair(glyphs: np.ndarray, below: int) -> dict[int, np.ndarray]:
    """Characters two tiles tall: tile T above tile T + `below`, for every T that is the upper half (its own
    `below`-th predecessor isn't one). Keys are T."""
    out, lower = {}, set()
    for t in range(len(glyphs) - below):
        if t in lower:
            continue
        out[t] = np.concatenate([glyphs[t], glyphs[t + below]])
        lower.add(t + below)
    return out


def blank(glyph: np.ndarray) -> bool:
    return len(np.unique(glyph)) == 1


def sheet(glyphs: dict[int, np.ndarray], columns: int = 16, scale: int = 4):
    """The glyphs in rows of `columns` with their codes in hex: the row's first code at the left, the last digit
    above each column. The most common colour is white (the background), the others dark greys."""
    from PIL import Image, ImageDraw
    codes = sorted(glyphs)
    if not codes:
        raise ValueError("no glyphs")
    h, w = glyphs[codes[0]].shape
    top = max(int(g.max()) for g in glyphs.values()) or 1
    values, counts = np.unique(np.concatenate([g.ravel() for g in glyphs.values()]), return_counts=True)
    background = int(values[np.argmax(counts)])
    cw, ch, left, gap = w * scale + 6, h * scale + 16, 56, 14
    place = {r: n for n, r in enumerate(sorted({c // columns for c in codes}))}     # rows with nothing in them
    rows = len(place)                                                              # (lower halves) take no room
    img = Image.new("RGB", (left + columns * cw, gap + rows * ch), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for c in range(columns):
        draw.text((left + c * cw + cw // 2 - 3, 1), f"{c:X}", fill=(0, 0, 200))
    for code in codes:
        row, col = place[code // columns], code % columns
        g = glyphs[code].astype(np.int32)
        grey = np.where(g == background, 255, g * 120 // top).astype(np.uint8)      # every other colour dark
        cell = Image.fromarray(grey).resize((w * scale, h * scale), Image.NEAREST)
        img.paste(cell, (left + col * cw, gap + row * ch))
        if col == 0 or code == codes[0]:
            draw.text((2, gap + row * ch + h * scale // 2 - 5), f"{code - col:04X}", fill=(200, 0, 0))
    return img


def skeleton(codes: list[int], unit: int, known: dict[int, str] | None = None, blanks: set[int] = frozenset(),
             attributes: list[int] = (0,), big_endian: bool = False) -> str:
    """A table with a line for every code: `HEX=character` where `known` has one, a commented `;HEX=` to fill in
    where not (a table with guesses in it is worse than one with gaps). Hex in memory byte order. With
    `attributes`, the whole list once for each value OR'd into the code (a tile's palette and priority bits)."""
    known = known or {}
    lines = []
    for attribute in attributes:
        if attribute:
            lines.append(f"; the same with 0x{attribute:X} set")
        for code in codes:
            key = (code | attribute).to_bytes(unit, "big" if big_endian else "little").hex().upper()
            if code in known:
                lines.append(f"{key}={known[code]}")
            else:
                lines.append(f";{key}=" + ("    (blank)" if code in blanks else ""))
    return "\n".join(lines) + "\n"


def known_pairs(text: str, unit: int, big_endian: bool = False, mask: int | None = None) -> dict[int, str]:
    """code -> character from `HEX=character` lines (a table, or what `yomikata where --table` printed); other
    lines are passed over. With `mask`, only those bits of a code count (to drop attribute bits)."""
    out = {}
    for line in text.splitlines():
        key, eq, value = line.strip().partition("=")
        value = value.split("    ")[0]
        if eq and len(key) == 2 * unit and value and all(c in "0123456789ABCDEFabcdef" for c in key):
            code = int.from_bytes(bytes.fromhex(key), "big" if big_endian else "little")
            out[code & mask if mask is not None else code] = value
    return out
