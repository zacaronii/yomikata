"""8x8 tiles out of a cartridge console's video memory, as colour numbers."""

from __future__ import annotations

import numpy as np


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
