"""A DS card's files (`yomikata nitro`): its file system (NitroFS) listed and taken out, and Nintendo's LZ77
packing (0x10 and 0x11) undone.

A DS game keeps its fonts and its script in files on the card, not at an address a script can read: the
program loads them into main memory, often packed. This lists them with their sizes and whether they are packed,
takes out those asked for, and unpacks them, so that a font can be drawn (`rom-font` on the file) and a script's
text read through with a table offline. Names and sizes only are printed; what the files hold is written to a
scratch folder.

The card's header (docs: GBATEK "DS Cartridge Header") gives where the file name table (FNT, 0x40) and the
file allocation table (FAT, 0x48) are, and the two programs (ARM9 at 0x20, ARM7 at 0x30) and their overlay tables
(0x50, 0x58). The FNT's directories list names in order, each file's number following on from the directory's
first; the FAT gives each number its start and end on the card.
"""

from __future__ import annotations

import fnmatch
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path


class NitroError(ValueError):
    pass


@dataclass
class File:
    path: str
    id: int | None                  # the file's number in the FAT (None for the programs)
    offset: int
    size: int


def card_bytes(path: Path) -> bytes:
    """The card image's bytes: the file as it is, or the largest member of a .zip."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            return z.read(max(z.infolist(), key=lambda i: i.file_size))
    return path.read_bytes()


def _u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def files(card: bytes) -> list[File]:
    """Every file on the card, by its path; the two programs and their overlays as arm9.bin, arm7.bin,
    overlay9/NNNN.bin and overlay7/NNNN.bin."""
    if len(card) < 0x200:
        raise NitroError("too short for a DS card's header")
    fnt_at, fnt_size, fat_at, fat_size = struct.unpack_from("<IIII", card, 0x40)
    if not (0 < fnt_at < len(card) and 0 < fat_at < len(card) and fat_size % 8 == 0 and fat_at + fat_size <= len(card)):
        raise NitroError("no file system where the header says (not a DS card?)")
    fat = [struct.unpack_from("<II", card, fat_at + 8 * k) for k in range(fat_size // 8)]

    def entry(fid: int, path: str) -> File:
        if fid >= len(fat):
            raise NitroError(f"file {fid} isn't in the allocation table")
        start, end = fat[fid]
        if not start <= end <= len(card):
            raise NitroError(f"file {fid} lies outside the card")
        return File(path, fid, start, end - start)

    out: list[File] = []
    seen: set[int] = set()

    def walk(directory: int, prefix: str) -> None:
        if directory in seen:
            raise NitroError("the file name table loops")
        seen.add(directory)
        sub_at, first, _ = struct.unpack_from("<IHH", card, fnt_at + 8 * (directory & 0xFFF))
        p, fid = fnt_at + sub_at, first
        while p < len(card):
            kind = card[p]
            p += 1
            if kind == 0:
                return
            n = kind & 0x7F
            name = card[p:p + n].decode("ascii", "replace")
            p += n
            if kind & 0x80:
                sub = struct.unpack_from("<H", card, p)[0]
                p += 2
                walk(sub, f"{prefix}{name}/")
            else:
                out.append(entry(fid, prefix + name))
                fid += 1
    walk(0xF000, "")
    for name, at in (("arm9.bin", 0x20), ("arm7.bin", 0x30)):
        start, size = _u32(card, at), _u32(card, at + 0x0C)
        if 0 < start and start + size <= len(card):
            out.append(File(name, None, start, size))
    for folder, at in (("overlay9", 0x50), ("overlay7", 0x58)):
        table, size = _u32(card, at), _u32(card, at + 4)
        if table and size and table + size <= len(card):
            for k in range(size // 32):
                number, fid = _u32(card, table + 32 * k), _u32(card, table + 32 * k + 0x18)
                out.append(entry(fid, f"{folder}/{number:04d}.bin"))
    return out


def lz10(data: bytes, at: int = 0) -> tuple[bytes, int]:
    """Unpacks an LZ77 stream of type 0x10 at `at`: the bytes, and how many of `data` it took. A block of eight
    flags (high bit first): 0, a byte as it is; 1, two bytes, the length less three in the high four bits and
    the distance less one in the twelve others."""
    if at + 4 > len(data) or data[at] != 0x10:
        raise NitroError("not an LZ77 stream of type 0x10")
    size = data[at + 1] | data[at + 2] << 8 | data[at + 3] << 16
    out, p = bytearray(), at + 4
    try:
        while len(out) < size:
            flags = data[p]
            p += 1
            for bit in range(8):
                if len(out) >= size:
                    break
                if flags & (0x80 >> bit):
                    b1, b2 = data[p], data[p + 1]
                    p += 2
                    n, back = (b1 >> 4) + 3, ((b1 & 0x0F) << 8 | b2) + 1
                    if back > len(out):
                        raise NitroError("a copy reaches back before the start")
                    for _ in range(n):
                        out.append(out[-back])
                else:
                    out.append(data[p])
                    p += 1
    except IndexError:
        raise NitroError("the stream ends early") from None
    return bytes(out[:size]), p - at


def lz11(data: bytes, at: int = 0) -> tuple[bytes, int]:
    """Unpacks an LZ77 stream of type 0x11 (longer copies): as 0x10, but a copy's first four bits say its form:
    0, a length 0x11-0x110 in three bytes; 1, a length 0x111-0x10110 in four; else a length 1-16 in two. A size of
    0 in the header means the size is in the four bytes after it."""
    if at + 4 > len(data) or data[at] != 0x11:
        raise NitroError("not an LZ77 stream of type 0x11")
    size, p = data[at + 1] | data[at + 2] << 8 | data[at + 3] << 16, at + 4
    if size == 0:
        size, p = _u32(data, p), p + 4
    out = bytearray()
    try:
        while len(out) < size:
            flags = data[p]
            p += 1
            for bit in range(8):
                if len(out) >= size:
                    break
                if not flags & (0x80 >> bit):
                    out.append(data[p])
                    p += 1
                    continue
                b = data[p]
                kind = b >> 4
                if kind == 0:
                    n = ((b & 0x0F) << 4 | data[p + 1] >> 4) + 0x11
                    back = ((data[p + 1] & 0x0F) << 8 | data[p + 2]) + 1
                    p += 3
                elif kind == 1:
                    n = ((b & 0x0F) << 12 | data[p + 1] << 4 | data[p + 2] >> 4) + 0x111
                    back = ((data[p + 2] & 0x0F) << 8 | data[p + 3]) + 1
                    p += 4
                else:
                    n, back = kind + 1, ((b & 0x0F) << 8 | data[p + 1]) + 1
                    p += 2
                if back > len(out):
                    raise NitroError("a copy reaches back before the start")
                for _ in range(n):
                    out.append(out[-back])
    except IndexError:
        raise NitroError("the stream ends early") from None
    return bytes(out[:size]), p - at


UNPACK = {0x10: lz10, 0x11: lz11}


def packing(data: bytes, at: int = 0) -> str | None:
    """lz10 or lz11 when a well-formed stream starts at `at` and either fills the rest of `data` (but for up to
    three bytes of padding: a packed file) or unpacks to more than it takes; else None."""
    if at + 4 > len(data) or data[at] not in UNPACK:
        return None
    try:
        out, took = UNPACK[data[at]](data, at)
    except NitroError:
        return None
    return f"lz{data[at]:x}" if out and (len(out) >= took or len(data) - at - took < 4) else None


def unpack(data: bytes, at: int = 0) -> bytes:
    """The stream at `at` unpacked."""
    if at + 1 > len(data) or data[at] not in UNPACK:
        raise NitroError("not an LZ77 stream (0x10 or 0x11)")
    return UNPACK[data[at]](data, at)[0]


def streams(data: bytes, align: int = 4, least: int = 16) -> list[tuple[int, int, int]]:
    """The packed streams inside a file (an archive of sections, as a game's whole script often is): each place,
    on `align`, where a well-formed stream begins that unpacks to `least` bytes or more, as (where, packed
    length, unpacked length). A stream found is stepped over, so one inside another isn't counted."""
    out, at = [], 0
    while at + 4 <= len(data):
        if data[at] in UNPACK:
            size = data[at + 1] | data[at + 2] << 8 | data[at + 3] << 16
            if (size >= least or (data[at] == 0x11 and size == 0)) and size < 64 * len(data) + 0x10000:
                try:
                    body, took = UNPACK[data[at]](data, at)
                except NitroError:
                    body, took = b"", 0
                if len(body) >= least and took <= len(body):
                    out.append((at, took, len(body)))
                    at += -(-took // align) * align
                    continue
        at += align
    return out


def select(found: list[File], only: list[str]) -> list[File]:
    """The files whose path matches any of the patterns (all of them with none)."""
    return [f for f in found if not only or any(fnmatch.fnmatch(f.path, p) for p in only)]
