"""Where a game keeps the page it shows, found from saved states (`yomikata state-text`), offline: no call of the
harness.

A game may show text by more than one way: its talk window by one, a cutscene's or an event's pages by another.
A script that reads one way hears nothing of the other. To find the other: save two states with it on screen, on
two different pages, and give each state the opening of its page (as the game encodes it). In each state the
page is found in RAM; then every word that points into the page (or a little before it, where its message
starts) is listed; the words that do so in every state, though the pages differ, are what moves with the page.
One level up, the words that point near those, at the same distance in every state, are a root a script can
read from (a fixed address, then an offset, then the text).

A state is the core's whole saved blob (gzip or not); RAM is found in it by an anchor, bytes RAM begins with (a
GameCube's disc ID at 0x80000000, say), and taken from its first appearance. Only addresses and counts are
printed: nothing of the text.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass


class StateTextError(ValueError):
    pass


def blob(data: bytes) -> bytes:
    """A state's bytes, unpacked when gzip."""
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


@dataclass
class Ram:
    data: bytes
    address: int                     # where data[0] sits in the game's address space
    order: str = "big"

    def word(self, at: int) -> int:
        return int.from_bytes(self.data[at:at + 4], self.order)

    def has(self, address: int) -> bool:
        return self.address <= address < self.address + len(self.data)


def ram(state: bytes, anchor: bytes, address: int, size: int, order: str = "big") -> Ram:
    """The RAM in a state: `size` bytes from the first place `anchor` appears, at `address`."""
    at = state.find(anchor)
    if not anchor or at < 0:
        raise StateTextError("the anchor isn't in the state")
    return Ram(state[at:at + size], address, order)


def found(r: Ram, needle: bytes) -> list[int]:
    """Every address the needle is at."""
    if not needle:
        raise StateTextError("no text to look for")
    out, at = [], r.data.find(needle)
    while at >= 0:
        out.append(r.address + at)
        at = r.data.find(needle, at + 1)
    return out


def words(r: Ram, lo: int, hi: int, align: int = 4) -> dict[int, int]:
    """Every word (on `align`) whose value lies in [lo, hi): its address -> its value."""
    import numpy as np
    usable = len(r.data) - len(r.data) % 4
    out = {}
    for start in range(0, min(align, 4)):
        n = (usable - start) // 4
        if n <= 0:
            continue
        view = np.frombuffer(r.data, dtype=(">u4" if r.order == "big" else "<u4"), count=n, offset=start)
        for k in np.nonzero((view >= lo) & (view < hi))[0]:
            out[r.address + start + 4 * int(k)] = int(view[k])
        if align >= 4:
            break
    return out


def follow(pages: list[tuple[Ram, bytes]], back: int = 64, ahead: int = 256, reach: int = 0x400,
           most: int = 64) -> dict:
    """The words that point into each state's page (from `back` before where it is found to `ahead` after its
    opening) in every state, with how far into the page each points; and one level up, the words that point at
    most `reach` before those, at the same distance in every state."""
    if len(pages) < 2:
        raise StateTextError("give two states or more, each on its own page")
    per = []
    for r, needle in pages:
        hits = found(r, needle)
        if not hits:
            raise StateTextError("a page's opening isn't in its state's RAM (the encoding, or the anchor?)")
        into: dict[int, int] = {}
        for h in hits:
            for slot, value in words(r, h - back, h + len(needle) + ahead).items():
                into.setdefault(slot, value - h)
        per.append((hits, into))
    common = sorted(set.intersection(*(set(into) for _, into in per)))
    slots = [{"slot": f"0x{s:08X}", "into": [into[s] for _, into in per]} for s in common[:most]]
    roots = []
    for s in common[:most]:
        held: dict[int, list[int]] = {}
        for r, _ in pages:
            for root, value in words(r, s - reach, s + 1).items():
                held.setdefault(root, []).append(s - value)
        for root, offs in sorted(held.items()):
            if len(offs) == len(pages) and len(set(offs)) == 1 and root != s:
                roots.append({"root": f"0x{root:08X}", "offset": f"0x{offs[0]:X}", "slot": f"0x{s:08X}"})
    return {"found": [len(h) for h, _ in per], "slots": slots, "roots": roots[:most]}
