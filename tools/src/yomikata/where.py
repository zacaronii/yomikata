"""Where a game keeps the text on screen, from snapshots with the lines a reader saw: one report.

`locate` finds the lines by their pattern of repeated characters. This sorts what it found into **buffers** and says
what each one is, which is what an entry needs:

- the same place holding the line in several snapshots is a buffer of the moment, not a stretch of loaded script;
- a second row a fixed number of cells on, every cell the first row's plus one constant, means characters are drawn
  from two tiles, one above the other: the game keeps the window as tiles, and the distance is the window's width;
- the same line elsewhere with one constant OR'd into every cell is a copy with attribute bits (the whole screen's
  tile map beside a window's own buffer). The copy without the bits is the one to read.

A place that fits one line's pattern can be chance: memory is large and a short stretch has few repeats. So each
place's codes are put to the test: with the code -> character pairs it gives, the *other* snapshots' lines are
looked for. Real text is found again, wherever the game put it; a chance fit predicts nothing.

Nothing here knows the table beforehand: the pairs come out of it."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from .locate import JP, VARIANTS, find_standard, locate

DTYPE = {name: dtype for name, dtype, _ in VARIANTS}
NEAR = 0x100            # bytes between two hits that still belong to one buffer of what is on screen
CHANCE = 0.001          # how often a stretch's known codes may line up by chance for it to count as found
KNOWN = {1: 4, 2: 3}    # by cell size: characters of another line that must stand where the table says
FAR = 0x4000            # the same for text kept loaded: a script's lines lie further apart


@dataclass
class Buffer:
    encoding: str                   # u8, u16le or u16be
    offset: int                     # bytes into the snapshot, of the earliest cell found
    lines: set[tuple[int, str]] = field(default_factory=set)    # (snapshot, line) found there by its pattern
    cells: int = 0                  # different cells found holding a character of a line
    agree: int = 0                  # lines found with the table the other lines' hits here give
    loaded: bool = False            # a snapshot showing other text holds the same cells: text kept loaded
    lower_row: tuple[int, int] | None = None        # (cells to the lower-half row, what is added to each cell)
    copy_of: tuple[int, int] | None = None          # (offset of the plain buffer, the bits this one adds)
    table: dict[str, int] = field(default_factory=dict)         # character -> code

    @property
    def snapshots(self) -> set[int]:
        return {n for n, _ in self.lines}

    @property
    def size(self) -> int:
        return 1 if self.encoding == "u8" else 2


def believed(b: Buffer) -> bool:
    return b.agree >= 1


def _cells(ram: bytes, encoding: str, offset: int, n: int) -> np.ndarray:
    if encoding == "u8":
        return np.frombuffer(ram, dtype=np.uint8, count=max(0, min(n, len(ram) - offset)), offset=offset).astype(np.int64)
    n = max(0, min(n, (len(ram) - offset) // 2))
    return np.frombuffer(ram, dtype=DTYPE[encoding], count=n, offset=offset).astype(np.int64)


def lower_row(ram: bytes, encoding: str, offset: int, found: list[int], reach: int = 64) -> tuple[int, int] | None:
    """(d, k) when the cells d cells after the found ones are each the found cell plus the same nonzero k."""
    if encoding == "u8" or len(set(found)) < 3:
        return None
    for d in range(len(found), reach + 1):
        below = _cells(ram, encoding, offset + 2 * d, len(found))
        if len(below) == len(found):
            diff = set((below - np.array(found)).tolist())
            if len(diff) == 1 and 0 not in diff:
                return d, diff.pop()
    return None


def _hits(snapshots: list[tuple[bytes, list[str]]], absent: list[bytes]) -> list[dict]:
    """locate's hits, one for each snapshot that shows the hit's line and holds its codes there. Left out: what
    the `absent` snapshots hold too; all but one of the readings that fit the same bytes."""
    out = {}
    for res in locate(snapshots, max_hits_per_window=16):       # a tile window alone holds a line four times over
        for hit in res.hits:
            raw = np.array(hit["codes"], dtype=DTYPE[res.encoding]).tobytes()
            at = hit["offset"]
            if any(a[at:at + len(raw)] == raw for a in absent):
                continue
            holds = [ram[at:at + len(raw)] == raw for ram, _ in snapshots]
            shows = [[line for line in lines if hit["segment"] in line] for _, lines in snapshots]
            for n in range(len(snapshots)):
                if not (holds[n] and shows[n]):
                    continue
                # Two-byte cells with one byte the same in all of them read just as well a byte earlier or in
                # the other byte order. Keep the reading at an even place whose codes are the smaller numbers.
                key = (n, hit["segment"], at if res.encoding == "u8" else (at + 1) // 2 * 2, res.encoding == "u8")
                mine = {**hit, "encoding": res.encoding, "snapshot": n, "line": shows[n][0],
                        "loaded": any(h and not s for h, s in zip(holds, shows))}
                rank = (at % 2, max(hit["codes"]))
                if key not in out or rank < out[key][0]:
                    out[key] = (rank, mine)
    return [h for _, h in out.values()]


def predicted(snapshots: list[tuple[bytes, list[str]]], b: Buffer, kept: list[dict], span: int = 12) -> set[str]:
    """The lines that stand somewhere in their snapshot as this place's table says, the table being made each time
    without the hits of the line looked for (a line can't confirm itself): within some stretch of `span` characters,
    KNOWN different characters the table has, each at its place with its code. (Only a stretch, because a reader's
    line may miss a character or have one too many.)"""
    need, found, arrays = KNOWN[b.size], set(), {}
    for n, (ram, lines) in enumerate(snapshots):
        for line in lines:
            if line in found:
                continue
            table = {}
            for hit in kept:
                if hit["segment"] not in line:
                    table.update(zip(hit["segment"], hit["codes"]))
            if len(table) < need:
                continue
            tries = []
            for run in JP.finditer(line):
                text = run.group()
                for start in range(max(1, len(text) - span + 1)):
                    known = {}
                    for i, ch in enumerate(text[start:start + span]):
                        if ch in table:
                            known.setdefault(ch, i)
                    if len(known) >= need:
                        tries.append((known, text[start:start + span]))
            if tries and n not in arrays:
                a = _cells(ram, b.encoding, b.offset % b.size, len(ram))
                arrays[n] = (a, np.bincount(a, minlength=256 ** b.size) / max(1, len(a)))
            # Memory is full of zeros, small numbers and tables: codes that are common there are found by chance.
            # Each stretch is worth how often its known codes would line up by chance in memory this size; only
            # stretches that wouldn't once in a thousand snapshots count, the least likely first.
            def chance(t):
                return len(arrays[n][0]) * math.prod(float(arrays[n][1][table[ch]]) for ch in t[0])
            for known, text in sorted((t for t in tries if chance(t) < CHANCE), key=chance)[:4]:
                a = arrays[n][0]
                size = len(a) - span
                at = np.ones(size, dtype=bool)
                for ch, i in known.items():
                    at &= a[i:i + size] == table[ch]
                for hit in kept:                # not where the table's own text still stands: a line that differs
                    if hit["segment"] in line:
                        continue
                    raw = np.array(hit["codes"], dtype=DTYPE[b.encoding]).tobytes()     # a little from it is no news
                    if ram[hit["offset"]:hit["offset"] + len(raw)] == raw:
                        first = (hit["offset"] - b.offset % b.size) // b.size
                        at[max(0, first - span):first + len(hit["codes"])] = False
                # and the rest of the stretch must read as text with the same table: one code a character
                if any(_fits(a[int(p):int(p) + len(text)].tolist(), text, table) for p in np.nonzero(at)[0][:64]):
                    found.add(line)
                    break
    return found


def _fits(cells: list[int], text: str, table: dict[str, int]) -> bool:
    code_of, char_of = dict(table), {c: ch for ch, c in table.items()}
    for ch, c in zip(text, cells):
        if code_of.setdefault(ch, c) != c or char_of.setdefault(c, ch) != ch:
            return False
    return len(cells) == len(text)


def where(snapshots: list[tuple[bytes, list[str]]], absent: list[bytes] = ()) -> tuple[list[Buffer], dict]:
    """The buffers the lines were found in, best first, and per encoding the code -> characters seen for it
    (from the believed buffers that aren't copies; from all, when none is believed)."""
    hits = _hits(snapshots, list(absent))
    for hit in hits:
        hit["lower"] = lower_row(snapshots[hit["snapshot"]][0], hit["encoding"], hit["offset"], hit["codes"])
    # the lower halves of a window's characters are found as a line of their own: leave them out
    lower = {(h["snapshot"], h["segment"], h["encoding"], h["offset"] + 2 * h["lower"][0]) for h in hits if h["lower"]}
    hits = [h for h in hits if (h["snapshot"], h["segment"], h["encoding"], h["offset"]) not in lower]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    last: dict[tuple, tuple[int, int]] = {}                     # per kind: where the last hit ended, its group
    for hit in sorted(hits, key=lambda h: h["offset"]):
        kind = (hit["encoding"], hit["loaded"])
        end, key = last.get(kind, (-FAR, 0))
        if hit["offset"] - end >= (FAR if hit["loaded"] else NEAR):     # hits close together stand in one buffer
            key = hit["offset"]
        size = 1 if hit["encoding"] == "u8" else 2
        last[kind] = (max(end, hit["offset"] + size * len(hit["codes"])), key)
        groups[(*kind, key)].append(hit)
    found, kept = [], []
    for (encoding, loaded, _), group in groups.items():
        # One table fits all of a real buffer's lines. Take the longest hit, then every hit that gives no character
        # a second code and no code a second character; chance hits beside real text, or beside each other, fall out.
        b, char_of, cells = Buffer(encoding, min(h["offset"] for h in group), loaded=loaded), {}, set()
        for hit in sorted(group, key=lambda h: -len(h["codes"])):
            pairs = list(zip(hit["segment"], hit["codes"]))
            if any(b.table.get(ch, c) != c or char_of.get(c, ch) != ch for ch, c in pairs):
                continue
            for ch, c in pairs:
                b.table[ch], char_of[c] = c, ch
            b.lines.add((hit["snapshot"], hit["line"]))
            cells.update(range(hit["offset"], hit["offset"] + b.size * len(pairs), b.size))
            b.lower_row = b.lower_row or hit["lower"]
            hit["buffer"] = b
            kept.append(hit)
        b.offset, b.cells = min(cells), len(cells)
        b.agree = len(predicted(snapshots, b, [h for h in kept if h["buffer"] is b]))
        found.append(b)
    # A copy with attribute bits holds the same line with one constant set in every cell.
    by_line = defaultdict(list)
    for hit in kept:
        by_line[hit["snapshot"], hit["segment"], hit["encoding"]].append(hit)
    for group in by_line.values():
        for h in group:
            for plain in group:
                bits = {c ^ p for c, p in zip(h["codes"], plain["codes"])}
                if plain["buffer"] is not h["buffer"] and len(bits) == 1 and 0 not in bits \
                        and all(c & p == p for c, p in zip(h["codes"], plain["codes"])):
                    h["buffer"].copy_of = (plain["buffer"].offset, bits.pop())
    mapping: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    sure = any(believed(b) for b in found)
    for hit in kept:
        if hit["buffer"].copy_of is None and (believed(hit["buffer"]) or not sure):
            for code, ch in zip(hit["codes"], hit["segment"]):
                mapping[hit["encoding"]][code][ch] += 1
    found.sort(key=lambda b: (b.copy_of is not None, -b.agree, -len(b.lines), -b.cells))
    return found, mapping


def report(buffers: list[Buffer], mapping: dict, base: int, snapshots: int, table: bool = False) -> str:
    if not buffers:
        return ("The lines weren't found by their pattern of repeated characters in 1- or 2-byte cells. Longer lines, "
                "or lines with more repeated characters, give the search more to hold on to.")
    # A place whose codes find no other line is as likely chance as text: counted, not listed, when better exist.
    shown = [b for b in buffers if believed(b)]
    out = []
    if not shown:
        shown = buffers[:5]
        out.append("No place's codes found another snapshot's line, so these may all be chance (or the lines share too "
                   "few characters). The best:")
    for b in shown:
        size = "1-byte" if b.encoding == "u8" else "2-byte " + ("little" if b.encoding == "u16le" else "big") + "-endian"
        line = (f"0x{base + b.offset:06X}  {size} cells: {len(b.lines)} line(s) of {len(b.snapshots)} snapshot(s) "
                f"(of {snapshots}), {b.cells} cells; {b.agree} line(s) found again with the codes of the others. ")
        line += ("Kept loaded: other snapshots hold the same cells while showing other text (a script; look for the "
                 "cell that says where the game is reading)" if b.loaded else
                 "Only there while on screen" + (", a different line each time: a buffer of what is shown"
                                                 if len(b.snapshots) > 1 else ""))
        out.append(line)
        if b.lower_row:
            d, k = b.lower_row
            out.append(f"    characters are two tiles tall: the lower halves are {d} cells on, each +0x{k & 0xFFFF:X}. "
                       f"As `rows`: max_bytes {2 * d}, stride {4 * d}")
        if b.copy_of:
            out.append(f"    a copy of 0x{base + b.copy_of[0]:06X} with 0x{b.copy_of[1]:04X} set in every cell "
                       "(attribute bits): read the other one")
    if len(shown) < len(buffers):
        out.append(f"{len(buffers) - len(shown)} more place(s) whose codes found no other line: likely chance, left out")
    if table:
        for encoding, seen in mapping.items():
            out.append(f"== {encoding}: {len(seen)} codes (memory byte order)")
            for code, chars in sorted(seen.items()):
                raw = np.array([code], dtype=DTYPE[encoding]).tobytes().hex().upper()
                out.append(f"{raw}={chars.most_common(1)[0][0]}" + ("" if len(chars) == 1 else
                           "    conflict: also " + " ".join(c for c, _ in chars.most_common()[1:])))
    return "\n".join(out)


def standard(snapshots: list[tuple[bytes, list[str]]], base: int) -> str | None:
    """The report for a game that keeps its lines in a standard encoding: where, and in how many snapshots. None
    when less than half of the lines' characters are found that way."""
    found = find_standard(snapshots)
    wanted = sum(len(run) for _, lines in snapshots for line in lines for run in JP.findall(line))
    if not found:
        return None
    encoding, hits = next(iter(found.items()))
    best = defaultdict(int)                                     # per snapshot and run, a line may stand in several places
    places: dict[int, dict] = {}
    last = (-NEAR, 0)
    for hit in sorted(hits, key=lambda h: h["offset"]):
        key = hit["offset"] if hit["offset"] - last[0] >= NEAR else last[1]
        last = (hit["offset"] + 2 * len(hit["segment"]), key)
        place = places.setdefault(key, {"snapshots": set(), "characters": 0})
        place["snapshots"].add(hit["snapshot"])
        place["characters"] += len(hit["segment"])
    for hit in hits:
        best[hit["snapshot"], hit["segment"]] = len(hit["segment"])
    if 2 * sum(best.values()) < wanted:
        return None
    out = [f"The lines stand in memory as plain {encoding}. Where:"]
    for at, place in sorted(places.items(), key=lambda kv: (-len(kv[1]["snapshots"]), -kv[1]["characters"]))[:12]:
        out.append(f"0x{base + at:06X}  {place['characters']} characters in {len(place['snapshots'])} of {len(snapshots)} "
                   "snapshot(s)" + ("; the same place each time: a buffer of what is shown" if len(place["snapshots"]) > 1 else ""))
    out.append("`yomikata locate` on the same snapshots proposes a layout's `message` from these.")
    return "\n".join(out)


# --- tile consoles: from the frame itself, with no reader's lines at all

def _shape(cell: np.ndarray) -> bytes:
    """A cell's picture whatever its colours: each pixel numbered by the order its colour first appears."""
    flat = cell.reshape(-1)
    _, first, inverse = np.unique(flat, return_index=True, return_inverse=True)
    return np.argsort(np.argsort(first))[inverse].astype(np.uint8).tobytes()


def screen_tiles(vram: bytes, screen: np.ndarray, bpp: int = 2, step: int = 0x1000) -> list[tuple[int, tuple[int, int], dict]]:
    """Which tiles the frame shows where. `screen` is the frame as (height, width) of colours packed into one
    number each. Tried: every tile base, and for each the eight by eight ways the layer can be shifted, of which
    the one explaining the most cells is kept. One entry per base that explains six cells or more, the most
    first: (base, shift, {(row, column): [tile numbers with that picture]}) for cells that aren't one flat colour.
    Which base is the text's is for memory to tell: a scene's own tiles fit by chance at other bases, and the same
    tiles count from lower bases under other numbers."""
    from .tiles import tiles
    cells = {}                                                  # shift -> {(row, column): picture}
    for oy in range(8):
        for ox in range(8):
            here = cells[ox, oy] = {}
            for row in range((screen.shape[0] - oy) // 8):
                for col in range((screen.shape[1] - ox) // 8):
                    cell = screen[oy + 8 * row:oy + 8 * row + 8, ox + 8 * col:ox + 8 * col + 8]
                    if cell.min() != cell.max():
                        here[row, col] = _shape(cell)
    out = []
    for base in range(0, len(vram), step):
        shapes: dict[bytes, list[int]] = defaultdict(list)
        for n, t in enumerate(tiles(vram, base, bpp, 1024)):
            if t.min() != t.max():
                shapes[_shape(t)].append(n)
        best: tuple[tuple[int, int], dict] = ((0, 0), {})
        for shift, here in cells.items():
            found = {at: shapes[shape] for at, shape in here.items() if shape in shapes}
            if len(found) > len(best[1]):
                best = (shift, found)
        if len(best[1]) >= 6:
            out.append((base, *best))
    return sorted(out, key=lambda t: -len(t[2]))


def tile_runs(found: dict, least: int = 4) -> list[list[list[int]]]:
    """The rows of tiles on screen as runs of `least` or more cells side by side, each cell its candidate tiles."""
    runs, by_row = [], defaultdict(dict)
    for (row, col), hit in found.items():
        by_row[row][col] = hit
    for row in sorted(by_row):
        cols, run = sorted(by_row[row]), []
        for col in cols + [None]:
            if run and (col is None or col != run[-1] + 1):
                if len(run) >= least:
                    runs.append([by_row[row][c] for c in run])
                run = []
            if col is not None:
                run.append(col)
    return runs


def find_tiles(ram: bytes, runs: list[list[list[int]]], mask: int = 0x3FF) -> dict[int, dict]:
    """Where memory holds the screen's runs as 2-byte cells whose low bits (`mask`) are the tile numbers:
    {offset: {"cells": how many, "bits": the other bits of the first cell}} for each run found, each place once."""
    out = {}
    for align in (0, 1):
        a = _cells(ram, "u16le", align, len(ram))
        low = a & mask
        for run in runs:
            n = len(a) - len(run)
            at = np.ones(n, dtype=bool)
            for i, candidates in enumerate(run):
                at &= np.isin(low[i:i + n], candidates)
            for p in np.nonzero(at)[0][:32]:
                out[align + 2 * int(p)] = {"cells": len(run), "bits": int(a[int(p)]) & ~mask & 0xFFFF}
    return out


def tile_report(ram: bytes, vram: bytes, screen: np.ndarray, base: int, bpp: int = 2, mask: int = 0x3FF) -> str:
    tried = []
    for tile_base, shift, found in screen_tiles(vram, screen, bpp):
        runs = tile_runs(found)
        tried.append((find_tiles(ram, runs, mask) if runs else {}, tile_base, shift, found, runs))
    if not tried or not tried[0][4]:
        return "The frame's cells don't match the tiles in video memory (another bit depth, or text drawn as pixels)."
    places, tile_base, shift, found, runs = max(tried, key=lambda t: len(t[0]))
    out = [f"The frame shows {len(found)} cells of tiles from 0x{tile_base:X} in video memory (layer shifted by {shift[0]},"
           f"{shift[1]}), in {len(runs)} runs of four or more. In memory as 2-byte cells (tile number in 0x{mask:X}):"]
    groups: dict[int, list[int]] = {}
    for at in sorted(places):
        key = next((k for k in groups if at - groups[k][-1] < 0x400), at)
        groups.setdefault(key, []).append(at)
    for key, ats in groups.items():
        bits = Counter(places[a]["bits"] for a in ats).most_common(1)[0][0]
        cells = sum(places[a]["cells"] for a in ats)
        line = (f"0x{base + key:06X}  runs found in {len(ats)} places from there, {cells} cells, the other bits "
                + (f"0x{bits:04X} (attribute bits: a copy of the whole screen, or a highlighted line)" if bits else
                   "clear: a window's own buffer"))
        if len(ats) > 1:
            gaps = Counter(b - a for a, b in zip(ats, ats[1:])).most_common(1)[0][0]
            line += f"; rows {gaps} bytes apart ({gaps // 2} cells: a row of the buffer, lower halves counted as rows)"
        out.append(line)
    if not places:
        out.append("nowhere: the game doesn't keep the screen's tiles in work RAM in this form")
    return "\n".join(out)
