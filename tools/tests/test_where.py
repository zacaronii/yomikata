"""`where` on made-up memory: a window of two-tile characters, the screen's copy with attribute bits, loaded script."""

import random
import struct

from yomikata.where import report, where

LINE = "ここにはつくりもののぎょうがここにありますここにも"          # made up; repeats give the search its hold
CODE = {ch: 0xC0 + 2 * i for i, ch in enumerate(dict.fromkeys(LINE))}


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
    other = LINE[11:] + LINE[:11]
    snaps = [(memory(LINE, 1), [LINE]), (memory(other, 2), [other])]
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
    assert "C000=こ" in report(buffers, results, 0x7E0000, 2, table=True)


def test_nothing_found_says_so():
    buffers, results = where([(bytes(0x4000), [LINE])])
    assert not buffers and "weren't found" in report(buffers, results, 0, 1)
