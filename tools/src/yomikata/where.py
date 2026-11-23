"""Where a game keeps the text on screen, from snapshots with the lines a reader saw: one report.

`locate` finds the lines by their pattern of repeated characters. This sorts what it found into **buffers** and says
what each one is, which is what an entry needs:

- the same place holding the line in several snapshots is a buffer of the moment, not a stretch of loaded script;
- a second row a fixed number of cells on, every cell the first row's plus one constant, means characters are drawn
  from two tiles, one above the other: the game keeps the window as tiles, and the distance is the window's width;
- the same line elsewhere with one constant OR'd into every cell is a copy with attribute bits (the whole screen's
  tile map beside a window's own buffer). The copy without the bits is the one to read.

Nothing here knows the table: the code -> character pairs come out of it."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from .locate import VARIANTS, locate

DTYPE = {name: dtype for name, dtype, _ in VARIANTS}
NEAR = 0x100            # bytes between two hits that still belong to one buffer


@dataclass
class Buffer:
    encoding: str                   # u8, u16le or u16be
    offset: int                     # bytes into the snapshot, of the earliest cell found
    snapshots: set[int] = field(default_factory=set)
    cells: int = 0                  # characters found there, over all snapshots
    lower_row: tuple[int, int] | None = None        # (cells to the lower-half row, what is added to each cell)
    copy_of: tuple[int, int] | None = None          # (offset of the plain buffer, the bits this one adds)


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
    """locate's hits, each with its snapshot, without those the `absent` snapshots hold too, and with one reading
    kept of the several that fit the same bytes."""
    out = {}
    for res in locate(snapshots, max_hits_per_window=16):       # a tile window alone holds a line four times over
        for hit in res.hits:
            raw = np.array(hit["codes"], dtype=DTYPE[res.encoding]).tobytes()
            at = hit["offset"]
            if any(a[at:at + len(raw)] == raw for a in absent):
                continue                                        # there with other text on screen: loaded script
            for n, (ram, _) in enumerate(snapshots):
                if ram[at:at + len(raw)] != raw:
                    continue
                # Two-byte cells with one byte the same in all of them read just as well a byte earlier or in
                # the other byte order. Keep the reading at an even place whose codes are the smaller numbers.
                key = (n, hit["segment"], at if res.encoding == "u8" else (at + 1) // 2 * 2, res.encoding == "u8")
                mine = {**hit, "encoding": res.encoding, "snapshot": n}
                rank = (at % 2, max(hit["codes"]))
                if key not in out or rank < out[key][0]:
                    out[key] = (rank, mine)
    return [h for _, h in out.values()]


def where(snapshots: list[tuple[bytes, list[str]]], absent: list[bytes] = ()) -> tuple[list[Buffer], dict]:
    """The buffers the lines were found in, best first, and per encoding the code -> characters seen for it
    (from the buffers that aren't copies)."""
    hits = _hits(snapshots, list(absent))
    for hit in hits:
        hit["lower"] = lower_row(snapshots[hit["snapshot"]][0], hit["encoding"], hit["offset"], hit["codes"])
    # the lower halves of a window's characters are found as a line of their own: leave them out
    upper = {(h["snapshot"], h["segment"], h["encoding"], h["offset"] + 4 * h["lower"][0] // 2) for h in hits if h["lower"]}
    hits = [h for h in hits if (h["snapshot"], h["segment"], h["encoding"], h["offset"]) not in upper]
    buffers: dict[tuple[str, int], Buffer] = {}
    last: dict[str, tuple[int, int]] = {}                       # per encoding: where the last hit ended, its buffer
    for hit in sorted(hits, key=lambda h: h["offset"]):
        end, key = last.get(hit["encoding"], (-NEAR, 0))
        if hit["offset"] - end >= NEAR:                         # hits close together stand in one buffer
            key = hit["offset"]
        last[hit["encoding"]] = (max(end, hit["offset"] + len(hit["codes"]) * (1 if hit["encoding"] == "u8" else 2)), key)
        b = buffers.setdefault((hit["encoding"], key), Buffer(hit["encoding"], hit["offset"]))
        b.snapshots.add(hit["snapshot"])
        b.cells += len(hit["codes"])
        b.lower_row = b.lower_row or hit["lower"]
        hit["buffer"] = b
    # A copy with attribute bits holds the same line with one constant set in every cell.
    by_line = defaultdict(list)
    for hit in hits:
        by_line[hit["snapshot"], hit["segment"], hit["encoding"]].append(hit)
    for group in by_line.values():
        for h in group:
            for plain in group:
                bits = {c ^ p for c, p in zip(h["codes"], plain["codes"])}
                if plain["buffer"] is not h["buffer"] and len(bits) == 1 and 0 not in bits and all(c & p == p for c, p in zip(h["codes"], plain["codes"])):
                    h["buffer"].copy_of = (plain["buffer"].offset, bits.pop())
    found = list(buffers.values())
    mapping: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for hit in hits:
        if hit["buffer"] in found and hit["buffer"].copy_of is None:
            for code, ch in zip(hit["codes"], hit["segment"]):
                mapping[hit["encoding"]][code][ch] += 1
    found.sort(key=lambda b: (b.copy_of is not None, -len(b.snapshots), b.lower_row is None, -b.cells))
    return found, mapping


def report(buffers: list[Buffer], mapping: dict, base: int, snapshots: int, table: bool = False) -> str:
    if not buffers:
        return ("The lines weren't found by their pattern of repeated characters in 1- or 2-byte cells. Longer lines, "
                "or lines with more repeated characters, give the search more to hold on to.")
    out = []
    for b in buffers:
        size = "1-byte" if b.encoding == "u8" else "2-byte " + ("little" if b.encoding == "u16le" else "big") + "-endian"
        line = f"0x{base + b.offset:06X}  {size} cells, {b.cells} characters in {len(b.snapshots)} of {snapshots} snapshot(s)"
        if len(b.snapshots) > 1:
            line += "; the same place each time: a buffer of what is on screen"
        elif snapshots > 1:
            line += "; seen once: by chance, or a place the game doesn't reuse"
        out.append(line)
        if b.lower_row:
            d, k = b.lower_row
            out.append(f"    characters are two tiles tall: the lower halves are {d} cells on, each +0x{k & 0xFFFF:X}. "
                       f"As `rows`: max_bytes {2 * d}, stride {4 * d}")
        if b.copy_of:
            out.append(f"    a copy of 0x{base + b.copy_of[0]:06X} with 0x{b.copy_of[1]:04X} set in every cell "
                       "(attribute bits): read the other one")
    if table:
        for encoding, seen in mapping.items():
            out.append(f"== {encoding}: {len(seen)} codes (memory byte order)")
            for code, chars in sorted(seen.items()):
                raw = np.array([code], dtype=DTYPE[encoding]).tobytes().hex().upper()
                out.append(f"{raw}={chars.most_common(1)[0][0]}" + ("" if len(chars) == 1 else
                           "    conflict: also " + " ".join(c for c, _ in chars.most_common()[1:])))
    return "\n".join(out)
