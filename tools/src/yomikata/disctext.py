"""The stretches of a disc that read as text in a game's own codes (docs/harness.md): every place the bytes go
through the table for a few characters on end. For a game whose script lies on the disc as it does in memory,
this is the whole script, to read sentences from when its kanji are being read off a font.

What comes out is the game's text: for a scratch folder, never for the catalog."""

from __future__ import annotations

import re
from pathlib import Path

from .disc import open_data_track
from .tbl import Table

SECTOR = 2048
STEP = 1024                                  # sectors looked at together


def pattern(table: Table, least: int, end: bytes = b"") -> re.Pattern:
    """Bytes that are `least` of the table's codes or more on end, the longer codes tried first."""
    by_length: dict[int, dict[bytes, set[int]]] = {}
    for code in table.chars:
        by_length.setdefault(len(code), {}).setdefault(code[:-1], set()).add(code[-1])
    parts = []
    for length in sorted(by_length, reverse=True):
        for lead, lasts in sorted(by_length[length].items()):
            parts.append(re.escape(lead) + b"[" + b"".join(re.escape(bytes([b])) for b in sorted(lasts)) + b"]")
    one = b"(?:" + b"|".join(parts) + b")"
    return re.compile(one + b"{%d,}" % least + (b"(?:" + re.escape(end) + b")?" if end else b""))


def decode(data: bytes, table: Table) -> str:
    out, at, longest = [], 0, max(len(code) for code in table.chars)
    while at < len(data):
        for length in range(longest, 0, -1):
            ch = table.chars.get(data[at:at + length])
            if ch is not None:
                out.append(ch)
                at += length
                break
        else:
            break
    return "".join(out)


def scan(disc: Path, table: Table, least: int = 8, scratch: Path | None = None):
    """(offset into the disc's data, text) for each stretch, each text once."""
    found, seen = [], set()
    look = pattern(table, least)
    with open_data_track(disc, scratch) as image:
        image.f.seek(0, 2)
        sectors = image.f.tell() // (2352 if image.raw else SECTOR)
        for first in range(0, sectors, STEP):
            chunk = b"".join(image.sector(lba) for lba in range(first, min(sectors, first + STEP + 4)))
            for m in look.finditer(chunk):
                if m.start() >= STEP * SECTOR or m.group() in seen:
                    continue
                seen.add(m.group())
                found.append((first * SECTOR + m.start(), decode(m.group(), table)))
    return found
