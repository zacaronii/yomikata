"""`where` on made-up memory: a window of two-tile characters, the screen's copy with attribute bits, loaded script."""

import random
import struct

from yomikata.where import report, where

LINE = "ここにはつくりもののぎょうがここにありますここにも"          # made up; repeats give the search its hold
OTHER = "もうひとつのつくりものがここにはありますよもうひとつ"
CODE = {ch: 0x1C0 + 2 * i for i, ch in enumerate(dict.fromkeys(LINE + OTHER))}


def memory(line: str, seed: int, script: bool = True) -> bytes:
    rnd = random.Random(seed)
    ram = bytearray(rnd.randbytes(0x8000))
    cells = [CODE[ch] for ch in line]
    width = 30
    struct.pack_into(f"<{len(cells)}H", ram, 0x1000, *cells)                                # the window's own rows
    struct.pack_into(f"<{len(cells)}H", ram, 0x1000 + 2 * width, *[c + 0x10 for c in cells])
    struct.pack_into(f"<{len(cells)}H", ram, 0x3000, *[c | 0x2000 for c in cells])           # the screen's copy
    if script:
        struct.pack_into(f"<{len(LINE)}H", ram, 0x6000, *[CODE[ch] for ch in LINE])          # always loaded
    return bytes(ram)


def test_a_tile_window_its_copy_and_the_script():
    snaps = [(memory(LINE, 1), [LINE]), (memory(OTHER, 2), [OTHER])]
    nothing = memory("ここ", 3)                                    # no line on screen; the script is still loaded
    buffers, results = where(snaps, absent=[nothing])
    best = buffers[0]
    assert (best.encoding, best.offset, best.snapshots) == ("u16le", 0x1000, {0, 1})
    assert best.lower_row == (30, 0x10) and best.copy_of is None
    copy = next(b for b in buffers if b.offset == 0x3000)
    assert copy.copy_of == (0x1000, 0x2000)
    assert not any(b.offset == 0x6000 for b in buffers)
    text = report(buffers, results, 0x7E0000, 2)
    assert "0x7E1000" in text and "stride 120" in text and "ここ" not in text
    assert "C001=こ" in report(buffers, results, 0x7E0000, 2, table=True)


def test_nothing_found_says_so():
    buffers, results = where([(bytes(0x4000), [LINE])])
    assert not buffers and "weren't found" in report(buffers, results, 0, 1)


def test_chance_hits_are_counted_not_listed():
    """A place that fits one line's pattern, the same in every snapshot, isn't confirmed by lines sharing that stretch."""
    stray = {ch: 0x31 + 3 * i for i, ch in enumerate(dict.fromkeys(LINE[:16]))}
    snaps = []
    for seed, line in ((1, LINE), (2, OTHER)):
        ram = bytearray(memory(line, seed, script=False))
        ram[0x7000:0x7000 + 16] = bytes(stray[ch] for ch in LINE[:16])
        snaps.append((bytes(ram), [line]))
    buffers, mapping = where(snaps)
    assert buffers[0].offset == 0x1000 and buffers[0].agree == 2
    assert all(b.agree == 0 for b in buffers if b.encoding == "u8")
    text = report(buffers, mapping, 0, 2)
    assert "0x007000" not in text and "left out" in text


def test_a_standard_encoding_is_answered_first():
    from yomikata.where import standard
    snaps = []
    for seed, line in ((1, LINE), (2, OTHER)):
        ram = bytearray(random.Random(seed).randbytes(0x4000))
        raw = line.encode("cp932")
        ram[0x2000:0x2000 + len(raw)] = raw
        snaps.append((bytes(ram), [line]))
    text = standard(snaps, 0x80000000)
    assert "plain shift_jis" in text and "0x80002000" in text and "2 of 2" in text and LINE[:4] not in text
    assert standard([(bytes(0x4000), [LINE])], 0) is None


def test_tiles_on_the_frame_are_found_in_memory_without_any_lines():
    import numpy as np
    from yomikata.where import find_tiles, screen_tiles, tile_report, tile_runs
    rng = np.random.default_rng(3)
    made = {n: rng.integers(0, 4, (8, 8)).astype(np.uint8) for n in range(0x40, 0x80)}
    made[0x40][:] = 3                                               # the blank
    vram = bytearray(0x8000)
    for n, t in made.items():                                       # two bit planes, row by row, from 0x2000
        for plane in range(2):
            for row in range(8):
                vram[0x2000 + 16 * n + 2 * row + plane] = int(np.packbits((t[row] >> plane) & 1)[0])
    palette = np.array([0x101010, 0x808080, 0xC0C0C0, 0x000000])
    shown = [0x45, 0x52, 0x41, 0x45, 0x58, 0x5F, 0x61]
    screen = np.full((64, 128), palette[3])
    for i, n in enumerate(shown):                                   # drawn at row 2, from column 3, layer shifted by 5,2
        screen[2 + 16:2 + 24, 5 + 8 * (3 + i):5 + 8 * (4 + i)] = palette[made[n]]
    ram = bytearray(rng.integers(0, 256, 0x4000).astype(np.uint8).tobytes())
    struct.pack_into("<7H", ram, 0x1200, *shown)
    struct.pack_into("<7H", ram, 0x3000, *[n | 0x2000 for n in shown])
    fits = screen_tiles(bytes(vram), screen)                         # the same pictures count from lower bases too
    base, shift, found = next(f for f in fits if f[0] == 0x2000)
    assert shift == (5, 2) and len(found) == 7 and {f[0] for f in fits} == {0, 0x1000, 0x2000}
    runs = tile_runs(found)
    assert [cell[0] for cell in runs[0]] == shown
    places = find_tiles(bytes(ram), runs)
    assert places[0x1200] == {"cells": 7, "bits": 0} and places[0x3000]["bits"] == 0x2000
    text = tile_report(bytes(ram), bytes(vram), screen, 0x7E0000)
    assert "0x7E1200" in text and "a window's own buffer" in text and "0x7E3000" in text and "from 0x2000" in text
