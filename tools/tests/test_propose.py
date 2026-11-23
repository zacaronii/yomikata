"""`propose` on made-up snapshots: a window of two-tile characters drawn on a frame, in two slots, with a slot cell."""

import struct

import numpy as np

from yomikata import propose as pr

WIDTH, ROWS, STEP, FIRST, SLOT_CELL = 9, 2, 0x200, 0x1000, 0x3000
PALETTE = np.array([0x101010, 0x808080, 0xC0C0C0, 0x000000])
RNG = np.random.default_rng(11)
TILES = {n: RNG.integers(0, 3, (8, 8)).astype(np.uint8) for n in range(0x40, 0xA0)}
for blank in (0x40, 0x50):
    TILES[blank][:] = 3
BORDER = RNG.integers(0, 3, (8, 8)).astype(np.uint8)            # a picture no tile has


def video_memory() -> bytes:
    vram = bytearray(0x4000)
    for n, t in TILES.items():
        for plane in range(2):
            for row in range(8):
                vram[0x2000 + 16 * n + 2 * row + plane] = int(np.packbits((t[row] >> plane) & 1)[0])
    return bytes(vram)


def snapshot(slot: int | None, lines: list[list[int]], seed: int):
    """RAM with the window's buffer in that slot (upper halves, then lower halves, per row of text) and a frame
    showing it at row 3, column 4, inside a border; or neither when `slot` is None."""
    ram = bytearray(np.random.default_rng(seed).integers(0, 256, 0x4000).astype(np.uint8).tobytes())
    ram[FIRST - 0x40:FIRST + 3 * STEP] = bytes(0x40 + 3 * STEP)
    struct.pack_into("<H", ram, SLOT_CELL, 0xFFFF if slot is None else slot)
    screen = np.full((112, 256), 0x204060)
    if slot is not None:
        screen[16:16 + 8 * (2 * ROWS + 2), 24:24 + 8 * (WIDTH + 2)] = PALETTE[np.tile(BORDER, (2 * ROWS + 2, WIDTH + 2))]
        for r, line in enumerate(lines):
            cells = line + [0x40] * (WIDTH - len(line))
            for half, add in ((0, 0), (1, 0x10)):
                row = [c + add if c != 0x40 or i < len(line) else 0x40 for i, c in enumerate(cells)]
                struct.pack_into(f"<{WIDTH}H", ram, FIRST + STEP * slot + 2 * WIDTH * (2 * r + half), *row)
                for i, tile in enumerate(row):
                    y, x = 8 * (3 + 2 * r + half), 8 * (4 + i)
                    screen[y:y + 8, x:x + 8] = PALETTE[TILES[tile]]
    return bytes(ram), screen


def test_a_window_its_slots_and_the_cell_that_names_them():
    vram = video_memory()
    shots = [snapshot(0, [[0x61, 0x62, 0x63, 0x64, 0x65], [0x66, 0x67, 0x68, 0x61, 0x69, 0x6A]], 1),
             snapshot(2, [[0x6B, 0x62, 0x6C, 0x6D], [0x6E, 0x6F, 0x70, 0x71, 0x72]], 2),
             snapshot(None, [], 3)]
    found = []
    for n, (ram, screen) in enumerate(shots):
        found += pr.windows(ram, vram, screen, n)
    assert [(w.snapshot, w.offset, w.width, w.count, w.tall) for w in found] == \
        [(0, FIRST, WIDTH, ROWS, 0x10), (1, FIRST + 2 * STEP, WIDTH, ROWS, 0x10)]
    layouts, notes = pr.draft(found, 0x7E0000, [], [ram for ram, _ in shots])
    assert [lay["message"]["address"] for lay in layouts] == ["0x7E1000", "0x7E1200", "0x7E1400"]
    assert layouts[2]["when"] == {"address": f"0x{0x7E0000 + SLOT_CELL:06X}", "type": "u16", "equals": 2}
    assert layouts[0]["message"]["rows"] == {"count": ROWS, "stride": 4 * WIDTH} and layouts[0]["message"]["max_bytes"] == 2 * WIDTH
    assert "0x200 bytes apart" in notes[0]


def test_one_address_gets_one_layout_and_no_when():
    vram = video_memory()
    shots = [snapshot(1, [[0x61, 0x62, 0x63, 0x64], [0x66, 0x67, 0x68]], 1), snapshot(1, [[0x6B, 0x62, 0x6C], [0x6E, 0x6F, 0x70]], 2)]
    found = [w for n, (ram, screen) in enumerate(shots) for w in pr.windows(ram, vram, screen, n)]
    layouts, notes = pr.draft(found, 0, [], [ram for ram, _ in shots])
    assert len(layouts) == 1 and "when" not in layouts[0] and layouts[0]["message"]["address"] == f"0x{FIRST + STEP:06X}"
