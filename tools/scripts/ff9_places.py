#!/usr/bin/env python3
"""Final Fantasy IX (Japan): every place's own font, read off the disc, for games/ps1/SLPS-02000.places.

A place's messages and the font that comes with them lie on the disc as they are loaded into memory: a small
package (0x106, a word, 8, the font part's offset), the block of messages, then the font part: a list of
glyph records and the glyphs' picture as a TIM image that the game puts into its texture page. So the places
needn't be played to: this finds each package on a disc, works out the place's key as the script does (a
64-bit FNV-1a hash of the glyph list and the messages' records), and fingerprints each glyph (yomikata glyphs).

  ff9_places.py scan DISC --dictionary tables/ps1/SLPS-02000.glyphs --out /scratch/work/ff9
      writes ff9-places.json (keys and fingerprints: no text, no pictures) and sheets of the glyphs the
      dictionary hasn't got, ff9-unread-000.png … with ff9-unread.txt saying which fingerprint each cell is
      and which three characters already read its picture is most like.
      Both are for a scratch folder: the sheets are pictures of the game's font. With --snapshots, each place
      is also written as a snapshot the script can read whole (yomikata run --dump), to check the readings on.
  ff9_places.py places /scratch/work/ff9-places.json --dictionary tables/ps1/SLPS-02000.glyphs
      prints the .places file: for each place, its glyphs' characters as far as the dictionary has them.

DISC is a disc image the tools can open (cue/bin, CHD with chdman)."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from yomikata import glyphs
from yomikata.disc import open_data_track

PAGE = (896, 256)       # the texture page the fonts go into, in words of video memory
HEIGHT = 13
SECTOR = 2048


def fnv1a64(data: bytes) -> str:
    value = 0xCBF29CE484222325
    for byte in data:
        value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


class Stream:
    """A disc's user data as one run of bytes."""

    def __init__(self, image, sectors: int):
        self.image, self.sectors = image, sectors

    def read(self, at: int, size: int) -> bytes:
        first, last = at // SECTOR, (at + size - 1) // SECTOR
        data = b"".join(self.image.sector(lba) if lba < self.sectors else bytes(SECTOR) for lba in range(first, last + 1))
        return data[at - first * SECTOR:at - first * SECTOR + size]


def package(stream: Stream, at: int):
    """The place whose package starts at `at`: its key, how many messages, its glyphs' pictures. None if this
    isn't one."""
    head = stream.read(at, 20)
    kind, _, eight, font_at = struct.unpack_from("<4I", head)
    messages, zero = struct.unpack_from("<HH", head, 16)
    if kind != 0x106 or eight != 8 or not 16 < font_at < 0x40000 or zero or not 0 < messages <= 2000:
        return None
    font = at + 8 + font_at
    fhead = stream.read(font, 0x1C)
    fkind, _, _, tim_at, _, ones, count = struct.unpack("<7I", fhead)
    if fkind != 0x10D or ones != 0xFFFFFFFF or not 0 < count <= 1024:
        return None
    listed = stream.read(font + 0x1C, 4 * count)
    key = fnv1a64(listed + stream.read(at + 20, 8 * min(messages, 512)))
    tim = font + 8 + tim_at + 0x10
    magic, flags, clut = struct.unpack("<3I", stream.read(tim, 12))
    if magic != 0x10 or not flags & 8:
        return None
    image = tim + 8 + clut
    size, x, y, w, h = struct.unpack("<I4H", stream.read(image, 12))
    if not (PAGE[0] <= x and x + w <= PAGE[0] + 64 and PAGE[1] <= y and y + h <= PAGE[1] + 256) or size < 12 + 2 * w * h:
        return None
    import numpy as np
    words = np.zeros((256, 64), dtype="<u2")
    words[y - PAGE[1]:y - PAGE[1] + h, x - PAGE[0]:x - PAGE[0] + w] = np.frombuffer(
        stream.read(image + 12, 2 * w * h), dtype="<u2").reshape(h, w)
    pixels = np.stack([(words >> (4 * i)) & 15 for i in range(4)], axis=2).reshape(256, 256)
    whole = stream.read(at, font + 0x1C + 4 * count - at)        # the package as far as the glyph list's end
    return key, messages, [glyphs.picture(pixels, record, HEIGHT) for record in glyphs.records(listed, count)], whole


BLOCK_POINTER, PACKAGE_AT = 0x8007AE14, 0x800D3898
CHECKED = {0x80012614: 0x0C050003, 0x8003F4D0: 0x0C014B25}      # the words the script knows its build by


def snapshot(out: Path, key: str, whole: bytes) -> None:
    """A place's package set in memory as the game would have it, as a snapshot `yomikata run --dump` takes:
    the script then reads the place's whole text. It holds the game's text: for a scratch folder only."""
    import gzip
    ram = bytearray(0x200000)
    for at, word in CHECKED.items():
        struct.pack_into("<I", ram, at - 0x80000000, word)
    struct.pack_into("<I", ram, BLOCK_POINTER - 0x80000000, PACKAGE_AT + 16)
    ram[PACKAGE_AT - 0x80000000:PACKAGE_AT - 0x80000000 + len(whole)] = whole
    Path(f"{out}-place-{key}.ram.gz").write_bytes(gzip.compress(bytes(ram), 1))
    Path(f"{out}-place-{key}.json").write_text(json.dumps({"platform": "ps1", "base_address": "0x80000000"}))


def scan(disc: Path, dictionary: Path, out: Path, snapshots: bool = False) -> int:
    known = glyphs.read_dictionary(dictionary.read_text(encoding="utf-8")) if dictionary.is_file() else {}
    places, unread, read = {}, {}, {}
    with open_data_track(disc, out.parent) as image:
        image.f.seek(0, 2)
        sectors = image.f.tell() // (2352 if image.raw else 2048)
        stream = Stream(image, sectors)
        step = 512                                  # sectors read at a time
        for first in range(0, sectors, step):
            chunk = stream.read(first * SECTOR, min(step, sectors - first) * SECTOR + 16)
            at = chunk.find(b"\x06\x01\x00\x00")
            while at >= 0:
                if at % 4 == 0 and at < step * SECTOR:
                    found = package(stream, first * SECTOR + at)
                    if found and found[0] not in places:
                        key, messages, pictures, whole = found
                        if snapshots:
                            snapshot(out, key, whole)
                        marks = [glyphs.fingerprint(p) for p in pictures]
                        places[key] = {"at": first * SECTOR + at, "messages": messages, "glyphs": marks}
                        for mark, picture in zip(marks, pictures):
                            if mark in known:
                                read.setdefault(mark, picture)
                            elif mark != "0" * 16:
                                unread.setdefault(mark, picture)
                at = chunk.find(b"\x06\x01\x00\x00", at + 1)
    Path(f"{out}-places.json").write_text(json.dumps(places), encoding="utf-8")
    order = list(unread)
    for part in range(0, len(order), 100):
        marks = order[part:part + 100]
        glyphs.sheet([unread[m] for m in marks], [f"{part + i:04d}" for i in range(len(marks))]).save(
            f"{out}-unread-{part // 100:03d}.png")
    # Each unread glyph's fingerprint, and the three pictures already read that look most like it: what it
    # might be taken for, and so what to tell it from.
    pairs = [(known[mark], picture) for mark, picture in read.items()]
    lines = [f"{mark} " + " ".join(f"{c} {score:.2f}" for c, score in glyphs.nearest(unread[mark], pairs)) for mark in order]
    Path(f"{out}-unread.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    total = sum(len(p["glyphs"]) for p in places.values())
    distinct = {m for p in places.values() for m in p["glyphs"]} - {"0" * 16}
    print(f"{len(places)} places, {total} glyphs, {len(distinct)} distinct pictures, "
          f"{len(distinct) - len(order)} in the dictionary, {len(order)} to read on {(len(order) + 99) // 100} sheets")
    return 0


def places(found: Path, dictionary: Path) -> int:
    known = glyphs.read_dictionary(dictionary.read_text(encoding="utf-8"))
    for character, marks in glyphs.twice(known).items():
        print(f"{character} is given to {len(marks)} pictures: {' '.join(marks)}", file=sys.stderr)
    whole = 0
    for key, place in sorted(json.loads(found.read_text(encoding="utf-8")).items(), key=lambda kv: kv[1]["at"]):
        print(f"@{key}")
        have = [(i, known[m]) for i, m in enumerate(place["glyphs"]) if m in known]
        whole += len(have) == sum(1 for m in place["glyphs"] if m != "0" * 16)
        for i, character in have:
            print(f"{i:02X}={character}")
    print(f"{whole} places read whole", file=sys.stderr)
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan")
    p.add_argument("disc", type=Path)
    p.add_argument("--dictionary", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True, help="a path and name in a scratch folder to write under")
    p.add_argument("--snapshots", action="store_true",
                   help="also write each place as a snapshot for `yomikata run --dump` (the game's text: scratch only)")
    p = sub.add_parser("places")
    p.add_argument("found", type=Path)
    p.add_argument("--dictionary", type=Path, required=True)
    args = ap.parse_args()
    sys.exit(scan(args.disc, args.dictionary, args.out, args.snapshots) if args.cmd == "scan"
             else places(args.found, args.dictionary))
