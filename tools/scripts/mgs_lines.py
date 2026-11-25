#!/usr/bin/env python3
"""Metal Gear Solid (Japan): every line on a disc with the pictures of its kanji, for `yomikata unread`.

A scene's captions and a radio call's lines come as lists that bring the pictures of their own kanji and number
them anew (games/ps1/SLPM-86111.lua has the form), so a kanji is known by its picture, and the script's
dictionary (SLPM-86111.glyphs) has only those of the lines played. The lists lie on the disc as they are loaded:
this finds them all (DEMO.DAT and VOX.DAT: lists of lines; RADIO.DAT: calls), and writes what the script would
have logged had every line been played: each picture (`glyph <hash> 12x12x2 <hex>`, the most used first) and,
for each, the two shortest lines it stands in (`glyph-line …`, kana and the fixed font's characters as text).

  mgs_lines.py DISC [DISC …] --table tables/ps1/SLPM-86111.tbl --out /scratch/work/mgs-lines.log

Then `yomikata unread /scratch/work/mgs-lines.log --dictionary games/ps1/SLPM-86111.glyphs --font <a font>`
draws the lines for reading. The log is the game's text and pictures: for a scratch folder only.
DISC is a disc image the tools can open (cue/bin, CHD with chdman)."""

from __future__ import annotations

import argparse
import re
import struct
import sys
from collections import Counter
from pathlib import Path

GLYPH = 36                              # bytes of a picture: 12 by 12, two bits a pixel
OWN, CALLS = 0x1C00, 0x1600             # codes above them: a list's own kanji, a radio call's
HASH, OPEN, CLOSE, BAR, COMMA = 0x23, 0x7B, 0x7D, 0x7C, 0x1002


def fnv(data: bytes) -> str:
    """text.hash as the script has it: 64-bit FNV-1a, in capitals."""
    h = 0xCBF29CE484222325
    for b in data:
        h = (h ^ b) * 0x100000001B3 & 0xFFFFFFFFFFFFFFFF
    return f"{h:016X}"


def tokens(d: bytes, at: int, end: int):
    """The codes of a text from `at` to its zero byte (low 13 bits each; ASCII as it is), and where that byte
    is. None if what is there isn't a text."""
    codes = []
    while at < end:
        b = d[at]
        if b == 0:
            return codes, at
        if b < 0x80:
            if b < 0x20:
                return None
            codes.append(b)
            at += 1
        else:
            if at + 1 >= end:
                return None
            c = (b << 8 | d[at + 1]) & 0x1FFF
            hi = c >> 8
            if not (hi in (0, 1, 2) or 0x10 <= hi <= 0x1F) or (hi == 0 and not 0x20 <= (c & 0xFF) < 0x80) \
                    or (hi in (1, 2) and not 1 <= (c & 0xFF) <= 0x56):
                return None
            codes.append(c)
            at += 2
    return None


def in_order(codes: list[int], low: int, high: int, own: int) -> bool:
    """A list numbers its own kanji from 1 in the order it first uses them."""
    seen = 0
    for c in codes:
        if low < c < high:
            if c - low == seen + 1:
                seen += 1
            elif c - low > seen:
                return False
    return seen == own


# A record's first sixteen bytes and the first of its text. All of it looked ahead at, so that a find doesn't
# use the bytes up: one that begins in the zeros before a list would otherwise hide the list's own beginning.
HEAD = re.compile(rb"(?s)(?=[\x00\x14-\xff][\x00\x01]\x00\x00..[\x00-\x0f]\x00.[\x00-\x1f]\x00\x00\x00\x00\x00\x00"
                  rb"[\x20-\x7e\x80-\x82\x90-\x9f\xc0-\xc2\xd0-\xdf])")


def lists(d: bytes) -> list[dict]:
    """The lists of lines in a file: records of {size, start, length, 0, text}, the last with size 0, then,
    from the next fourth byte on, the pictures of the list's own kanji. Each as its lines' codes and pictures."""
    done, found = set(), []
    for m in HEAD.finditer(d):
        at = m.start()
        if at in done:
            continue
        records, own, p, end = [], 0, at, None
        while p + 17 <= len(d) and len(records) <= 4000:
            size, start, length, zero = struct.unpack_from("<4I", d, p)
            if zero or size % 4 or (size and size < 0x14) or size > 0x400 or length > 0x2000 or start > 0xFFFFF:
                break
            text = tokens(d, p + 16, p + (size or 0x400))
            if not text or not text[0]:
                break
            records.append((p, start, text[0]))
            own = max([own] + [c - OWN for c in text[0] if c > OWN])
            if size == 0:
                end = text[1]
                break
            p += size
        if end is None or (len(records) < 2 and not own):
            continue
        if not in_order([c for _, _, text in records for c in text], OWN, 0x2000, own):
            continue
        if [start for _, start, _ in records] != sorted(start for _, start, _ in records):
            continue
        pictures = at + ((end + 1 - at + 3) // 4) * 4
        if any(d[end:pictures]) or pictures + GLYPH * own > len(d) \
                or not all(any(d[pictures + GLYPH * k:pictures + GLYPH * k + 33]) for k in range(own)):
            continue
        done.update(p for p, _, _ in records)
        found.append({"at": at, "low": OWN, "lines": [text for _, _, text in records],
                      "pictures": [d[pictures + GLYPH * k:pictures + GLYPH * (k + 1)] for k in range(own)]})
    return found


def calls(d: bytes) -> list[dict]:
    """The radio calls in a file: a frequency, six bytes, 0x80, the call's length (high byte first), the call,
    whose lines each follow 0xFF 0x01 and a length, then the pictures of the call's own kanji."""
    found = []
    for m in re.finditer(rb"(?s)[\x36\x37].{7}\x80(..)", d):
        at = m.start()
        if not 14000 <= (d[at] << 8 | d[at + 1]) <= 14199:
            continue
        end = at + 9 + (d[at + 9] << 8 | d[at + 10])
        if end - at < 17 or end + GLYPH > len(d):
            continue
        lines = []
        for s in re.finditer(rb"\xff\x01", d[at + 11:end]):
            p = at + 11 + s.start()
            text = tokens(d, p + 10, min(end, p + 2 + (d[p + 2] << 8 | d[p + 3]) + 1))
            if text and text[0]:
                lines.append(text[0])
        own = max([0] + [c - CALLS for text in lines for c in text if CALLS < c < OWN])
        pictures = [d[end + GLYPH * k:end + GLYPH * (k + 1)] for k in range(own)]
        if lines and all(any(p[:33]) for p in pictures) and sum(1 for p in pictures if p[33:] == b"\0\0\0") >= 0.9 * own:
            found.append({"at": at, "low": CALLS, "lines": lines, "pictures": pictures})
    return found


def marks(line: list[int], low: int, pictures: list[bytes], fixed: dict[int, str]) -> list[str]:
    """A line as `glyph-line` has it: a hash for each of its own kanji, "_" for a space or a new row, and "="
    before what is known by code (kana by Unicode's order, the fixed font by the table, ASCII). A word with a
    reading over it ("#{word、reading}#") is given as the word."""
    out, text, i = [], "", 0

    def flush():
        nonlocal text
        if text:
            out.append("=" + text)
        text = ""
    high = OWN if low == CALLS else 0x2000
    reading = False
    while i < len(line):
        c, after = line[i], line[i + 1] if i + 1 < len(line) else None
        i += 1
        if c == HASH and after == OPEN:
            i += 1
        elif c == HASH and after in (0x4E, ):                           # "#N": a new row in a radio call
            i += 1
            flush()
            out.append("_")
        elif c == CLOSE:
            reading = False
            if after == HASH:
                i += 1
        elif reading:
            continue
        elif c == COMMA and OPEN in line[:i] and CLOSE in line[i:] and line[:i].count(OPEN) > line[:i].count(CLOSE):
            reading = True
        elif c in (0x20, BAR):
            flush()
            out.append("_")
        elif low < c < high and c - low <= len(pictures):
            flush()
            out.append(fnv(pictures[c - low - 1]))
        elif c < 0x80:
            text += chr(c)
        elif 0x100 <= c < 0x200:
            text += chr(0x3040 + c - 0x100)
        elif 0x200 <= c < 0x300:
            text += chr(0x30A0 + c - 0x200)
        else:
            text += fixed.get(c, "〓")
    flush()
    return out


def log(found: list[dict], fixed: dict[int, str], rows_each: int = 2) -> list[str]:
    """What goes into the log: the pictures, the most used first, and for each the shortest lines it stands in."""
    used, pictures, lines = Counter(), {}, {}
    for item in found:
        for line in item["lines"]:
            row = marks(line, item["low"], item["pictures"], fixed)
            for mark in row:
                if len(mark) == 16 and mark[0] != "=":
                    used[mark] += 1
                    lines.setdefault(mark, []).append(row)
        for picture in item["pictures"]:
            pictures.setdefault(fnv(picture), picture)
    out = [f"glyph {mark} 12x12x2 {pictures[mark].hex()}" for mark, _ in used.most_common()]
    written = set()
    for mark, _ in used.most_common():
        rows = sorted({" ".join(row) for row in lines[mark] if len(row) >= 2}, key=lambda r: (len(r.split()), r))
        for row in rows[:rows_each]:
            if row not in written:
                written.add(row)
                out.append("glyph-line " + row)
    return out


def fixed_font(table: str) -> dict[int, str]:
    """The fixed font's characters by a code's low 13 bits, from the game's table (HEX=character lines)."""
    out = {}
    for line in table.splitlines():
        code, _, character = line.partition("=")
        if character and not line.startswith("#") and len(code) == 4:
            out[int(code, 16) & 0x1FFF] = character
    return out


def main(argv: list[str] | None = None) -> int:
    from yomikata.disc import ISO9660, open_data_track
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("discs", type=Path, nargs="+")
    ap.add_argument("--table", type=Path, required=True, help="tables/ps1/SLPM-86111.tbl")
    ap.add_argument("--out", type=Path, required=True, help="the log, in a scratch folder")
    ap.add_argument("--work", type=Path, default=Path("/scratch/work"), help="where a disc image may be unpacked")
    args = ap.parse_args(argv)
    found = []
    for disc in args.discs:
        with open_data_track(disc, args.work) as image:
            iso = ISO9660(image)
            for path, entry in iso.walk():
                if entry.size < 2048 or entry.size > 400e6 or path.upper().endswith((".STR", ".XA")):
                    continue
                data = iso.read(entry)
                here = lists(data) + (calls(data) if "RADIO" in path.upper() else [])
                if here:
                    print(f"{disc.name} {path}: {len(here)} lists and calls, {sum(len(x['lines']) for x in here)} lines", file=sys.stderr)
                found += here
    lines = log(found, fixed_font(args.table.read_text(encoding="utf-8")))
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{sum(1 for line in lines if line.startswith('glyph '))} pictures, "
          f"{sum(1 for line in lines if line.startswith('glyph-line'))} lines written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
