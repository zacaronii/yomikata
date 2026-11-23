"""A first draft of a layout from snapshots, for text kept as tiles: the last step after `where` and `font`.

For each snapshot with its video memory and frame, the frame's tiles are found in memory (`where`'s frame path)
and sorted into **windows**: cells of one buffer lie a fixed number of bytes apart per frame row, which gives the
window's width; the window reaches as far as the frame and the buffer agree cell by cell (beyond it the frame
shows the border or the game); where characters are two tiles tall, a lower row's cells are the cells above
plus one constant, and the rows of text are every other row. Over several
snapshots the same window may stand at different addresses a fixed step apart (a slot per open window): then a
cell that holds the slot's number each time is looked for, and the draft gets a layout per slot with a `when`.

The draft is read back from every snapshot with `check`. It is a draft: which window is the message, and what
the other windows are for, is for a person to say."""

from __future__ import annotations

import math
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from .where import _cells, screen_tiles


@dataclass
class Window:
    snapshot: int
    origin: int         # where frame cell (0, 0) would be if the buffer went on that far
    row_bytes: int      # bytes from one frame row to the next: twice the window's width in cells
    left: int           # frame column of the window's first cell
    top: int            # frame row of its first row of text
    count: int          # rows of text the buffer has
    tall: int           # what a lower-half cell adds to the cell above; 0 when characters are one tile
    sure: bool          # left, top and count are where the frame and the buffer stop agreeing
    cells: int          # frame cells found in it

    @property
    def offset(self) -> int:
        return self.origin + self.row_bytes * self.top + 2 * self.left

    @property
    def width(self) -> int:
        return self.row_bytes // 2

    @property
    def kind(self) -> tuple:
        return (self.width, self.count, bool(self.tall))


def placed_runs(found: dict, least: int = 3) -> list[tuple[int, int, list[list[int]]]]:
    """The frame's tiles as (row, first column, cells) for each run of `least` or more cells side by side."""
    by_row = defaultdict(dict)
    for (row, col), hit in found.items():
        by_row[row][col] = hit
    runs = []
    for row in sorted(by_row):
        run = []
        for col in sorted(by_row[row]) + [None]:
            if run and (col is None or col != run[-1] + 1):
                if len(run) >= least:
                    runs.append((row, run[0], [by_row[row][c] for c in run]))
                run = []
            if col is not None:
                run.append(col)
    return runs


def _places(a: np.ndarray, low: np.ndarray, run: list[list[int]], mask: int) -> list[int]:
    """Cell indexes where the run stands with no other bits set (a window's own buffer)."""
    n = len(a) - len(run)
    at = (a[:n] & ~mask) == 0
    for i, candidates in enumerate(run):
        at &= np.isin(low[i:i + n], candidates)
    return [int(p) for p in np.nonzero(at)[0][:16]]


def windows(ram: bytes, vram: bytes, screen: np.ndarray, snapshot: int = 0, bpp: int = 2, mask: int = 0x3FF) -> list[Window]:
    from .tiles import tiles
    best: tuple[list, int, tuple[int, int], dict] = ([], 0, (0, 0), {})
    for tile_base, shift, found in screen_tiles(vram, screen, bpp):
        hits = []                                       # (frame row, frame column, byte offset, cells)
        for align in (0, 1):
            a = _cells(ram, "u16le", align, len(ram))
            low = a & mask
            for row, col, run in placed_runs(found):
                hits += [(row, col, align + 2 * p, len(run)) for p in _places(a, low, run, mask)]
        if len(hits) > len(best[0]):
            best = (hits, tile_base, shift, found)
    hits, tile_base, (ox, oy), found = best
    flat_tiles = {n for n, t in enumerate(tiles(vram, tile_base, bpp, mask + 1)) if t.min() == t.max()}

    def cell(at: int) -> int:
        return struct.unpack_from("<H", ram, at)[0] if 0 <= at <= len(ram) - 2 else -1

    def flat(row: int, col: int) -> bool:
        y, x = oy + 8 * row, ox + 8 * col
        part = screen[y:y + 8, x:x + 8] if y >= 0 and x >= 0 else screen[:0]
        return part.shape == (8, 8) and part.min() == part.max()

    out, used = [], set()
    for seed in sorted(hits, key=lambda h: h[2]):
        if seed in used:
            continue
        near = [h for h in hits if h not in used and abs(h[2] - seed[2]) < 0x800]
        steps = Counter()
        for r1, c1, o1, _ in near:
            for r2, c2, o2, _ in near:
                if r2 > r1 and (o2 - o1 - 2 * (c2 - c1)) % (r2 - r1) == 0:
                    g = (o2 - o1 - 2 * (c2 - c1)) // (r2 - r1)
                    if g > 0 and g % 2 == 0:
                        steps[g] += 1
        fits = [g for g, _ in steps.most_common() if any(o2 - o1 == g * (r2 - r1) + 2 * (c2 - c1) and r2 > r1
                                                         for r1, c1, o1, _ in [seed] for r2, c2, o2, _ in near)]
        if not fits:
            used.add(seed)                              # one row alone: its window's width can't be told
            continue
        g = fits[0]
        origin = seed[2] - g * seed[0] - 2 * seed[1]
        mine = [h for h in near if h[2] - g * h[0] - 2 * h[1] == origin]
        used.update(mine)
        width = g // 2

        def shows(row: int, col: int) -> bool:
            """Whether the frame shows, at that cell, what the buffer holds for it."""
            value = cell(origin + g * row + 2 * col)
            if value < 0:
                return False
            seen = found.get((row, col))
            return (value & mask) in seen if seen else flat(row, col) and (value & mask) in flat_tiles

        # the window is as far as frame and buffer agree: to the left of its first cell and above and below its
        # rows the frame shows the border or the game, and the buffer something else
        row, left = mine[0][0], mine[0][1]
        while left - 1 > mine[0][1] - width and shows(row, left - 1):
            left -= 1
        whole = lambda r: all(shows(r, left + i) for i in range(width))                         # noqa: E731
        if not whole(row):
            continue
        top = row
        while whole(top - 1):
            top -= 1
        bottom = row
        while whole(bottom + 1):
            bottom += 1
        # two-tile characters: every found cell of a lower row is the cell above plus one constant
        adds = Counter()
        for r, c, _, n in mine:
            diffs = {cell(origin + g * (r + 1) + 2 * (c + i)) - cell(origin + g * r + 2 * (c + i)) for i in range(n)}
            if len(diffs) == 1 and r + 1 <= bottom:
                adds[diffs.pop()] += 1
        tall = adds.most_common(1)[0][0] if adds and adds.most_common(1)[0][0] else 0
        rows = bottom - top + 1
        if tall and rows % 2 == 0:
            if not any(h[0] == top + 2 * k for h in mine for k in range(rows // 2)) and any(h[0] == top + 1 for h in mine):
                continue                                # upper halves on odd rows: the rows were cut wrongly
            rows //= 2
        elif tall:
            tall = 0
        out.append(Window(snapshot, origin, g, left, top, rows, tall, True, sum(h[3] for h in mine)))
    return out


def family(found: list[Window], rams: list[bytes], others: list[Window] = (),
           looked: set[int] | None = None) -> tuple[int, dict[int, int], list[int]] | None:
    """For windows of one kind seen at different addresses: (step, {snapshot: slot number}, offsets of 16-bit
    cells that hold the slot number in every one of those snapshots, and something else where the window is
    absent). The step is the largest that divides the distances between all the windows seen, of this kind and
    of the `others` (a game's windows share their slots), unless a smaller one (a half, a third, a quarter:
    slots in between weren't seen) is the one a cell's numbers fit. None when this kind's addresses are all the
    same."""
    places = {w.snapshot: w.offset for w in found}
    lowest = min(places.values())
    if len(set(places.values())) < 2:
        return None
    largest = math.gcd(*[w.offset - lowest for w in [*found, *others]])
    # only snapshots whose frame could be looked at say anything about the window being absent
    arrays = {n: np.frombuffer(rams[n][:len(rams[n]) // 2 * 2], dtype="<u2") for n in range(len(rams))
              if looked is None or n in looked or n in places}
    first = None
    for part in (1, 2, 3, 4):
        if largest % part:
            continue
        step = largest // part
        slot = {n: (v - lowest) // step for n, v in places.items()}
        ok = np.ones(len(next(iter(arrays.values()))), dtype=bool)
        for n, a in arrays.items():
            ok &= (a == slot[n]) if n in slot else ~np.isin(a, list(set(slot.values())))
        cells = [2 * int(i) for i in np.nonzero(ok)[0][:32]]
        first = first or (step, slot, cells)
        if cells:
            return step, slot, cells
    return first


def draft(found: list[Window], base: int, tables: list[str], rams: list[bytes],
          others: list[Window] = (), looked: set[int] | None = None) -> tuple[list[dict], list[str]]:
    """Layouts for the windows of one kind, and remarks about what was and wasn't measured."""
    w = max(found, key=lambda x: x.cells)
    notes = []
    def message(offset: int) -> dict:
        return {"address": f"0x{base + offset:06X}", "charset": "table", "max_bytes": w.row_bytes,
                "rows": {"count": w.count, "stride": w.row_bytes * (2 if w.tall else 1)},
                "cleanup": [{"regex": "(?m)　+$", "replace": ""}, {"regex": "\\n+$", "replace": ""}]}
    fam = family(found, rams, others, looked)
    if not fam:
        absent = len(rams) - len({x.snapshot for x in found})
        notes.append("the same address in every snapshot" + ("; no `when` drafted for the snapshots without it: "
                     "compare them with `yomikata contrast`" if absent else ""))
        return [{"poll": "frame", "message": message(w.offset)}], notes
    step, slot, cells = fam
    lowest = min(x.offset for x in found)
    notes.append(f"seen in slots {sorted(set(slot.values()))}, 0x{step:X} bytes apart; "
                 f"{len(cells)} cell(s) hold the slot's number in every snapshot"
                 + (": no `when` drafted" if not cells else f": 0x{base + cells[0]:06X}" if len(cells) == 1 else
                    " (" + ", ".join(f"0x{base + c:06X}" for c in cells[:6]) + "): the first is used; snapshots with "
                    "other windows open and this one closed would tell them apart"))
    layouts = []
    for k in range(max(slot.values()) + 1):
        lay = {"poll": "frame", "message": message(lowest + step * k)}
        if cells:
            lay["when"] = {"address": f"0x{base + cells[0]:06X}", "type": "u16", "equals": k}
        layouts.append(lay)
    return layouts, notes
