"""A 3DS card image's files (`yomikata ctr`): its ExeFS (the program and its icon and banner) and its RomFS (the
game's data: scripts, fonts, archives), listed and taken out, read from a decrypted image (.3ds/.cci: NCSD).

A 3DS card (docs: 3dbrew "NCSD", "NCCH", "ExeFS", "RomFS") is an NCSD: a header at 0x100 ("NCSD") and a table of
eight partitions at 0x120 (offset and size, in media units of 0x200 bytes). The first partition is the game, an
NCCH: its own header at 0x100 ("NCCH"), its product code at 0x150, flags at 0x188 (byte 7, bit 0x04: not
encrypted; an encrypted one can't be read here), the ExeFS at 0x1A0 and the RomFS at 0x1B0 (offset and size, in
media units, from the NCCH's start).

The ExeFS is a header of 10 files (a name of 8 bytes, an offset and a size), their data after the header's 0x200
bytes. The RomFS is an IVFC hash tree whose third level is the data: at the master hash's end (0x60 plus its
size), rounded up to the level's block; there a header gives the directories' and files' tables and where the
files' data begins, and each directory and file names its first child and its next sibling (names UTF-16LE).
Nothing is held in memory but the tables: a file is read from the image when it is asked for.
"""

from __future__ import annotations

import fnmatch
import struct
from dataclasses import dataclass
from pathlib import Path

MEDIA = 0x200


class CtrError(ValueError):
    pass


@dataclass
class File:
    path: str          # exefs/<name> or romfs/<path>
    offset: int        # in the image
    size: int


class Card:
    """A decrypted 3DS card image, opened for reading its files."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle = self.path.open("rb")
        head = self._read(0, 0x200)
        if head[0x100:0x104] != b"NCSD":
            raise CtrError("not a 3DS card image (no NCSD header): a .3ds or .cci, decrypted")
        self.ncch = struct.unpack_from("<I", head, 0x120)[0] * MEDIA
        ncch = self._read(self.ncch, 0x200)
        if ncch[0x100:0x104] != b"NCCH":
            raise CtrError("the first partition isn't an NCCH")
        if not ncch[0x18F] & 0x04:
            raise CtrError("the game's partition is encrypted: a decrypted image is wanted")
        self.code = ncch[0x150:0x160].split(b"\0")[0].decode("ascii", "replace")
        self.exefs = tuple(v * MEDIA for v in struct.unpack_from("<II", ncch, 0x1A0))
        self.romfs = tuple(v * MEDIA for v in struct.unpack_from("<II", ncch, 0x1B0))

    def _read(self, at: int, size: int) -> bytes:
        self.handle.seek(at)
        data = self.handle.read(size)
        if len(data) < size:
            raise CtrError("the image ends early")
        return data

    def read(self, f: File) -> bytes:
        return self._read(f.offset, f.size)

    def close(self) -> None:
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def files(self) -> list[File]:
        """Every file: the ExeFS's, then the RomFS's, by path."""
        return self._exefs() + self._romfs()

    def _exefs(self) -> list[File]:
        at, size = self.exefs
        if not size:
            return []
        start = self.ncch + at
        head = self._read(start, 0x200)
        out = []
        for k in range(10):
            name, offset, length = struct.unpack_from("<8sII", head, 16 * k)
            name = name.split(b"\0")[0].decode("ascii", "replace")
            if name and length:
                out.append(File(f"exefs/{name}", start + 0x200 + offset, length))
        return out

    def _romfs(self) -> list[File]:
        at, size = self.romfs
        if not size:
            return []
        start = self.ncch + at
        ivfc = self._read(start, 0x60)
        if ivfc[:4] != b"IVFC":
            raise CtrError("the RomFS has no IVFC header")
        master = struct.unpack_from("<I", ivfc, 0x08)[0]
        block = 1 << struct.unpack_from("<I", ivfc, 0x4C)[0]
        level3 = start + -(-(0x60 + master) // block) * block
        head = self._read(level3, 0x28)
        (_, _, _, dir_meta, dir_size, _, _, file_meta, file_size, data) = struct.unpack_from("<10I", head, 0)
        dirs, files = self._read(level3 + dir_meta, dir_size), self._read(level3 + file_meta, file_size)

        def name(table: bytes, at: int, base: int) -> str:
            length = struct.unpack_from("<I", table, at + base)[0]
            return table[at + base + 4:at + base + 4 + length].decode("utf-16-le", "replace")
        out: list[File] = []
        seen: set[int] = set()

        def walk(d: int, prefix: str) -> None:
            if d in seen or d + 0x18 > len(dirs):
                raise CtrError("the RomFS's directory table loops or runs past its end")
            seen.add(d)
            _, _, child, first, _ = struct.unpack_from("<5I", dirs, d)
            f = first
            while f != 0xFFFFFFFF:
                if f + 0x20 > len(files):
                    raise CtrError("the RomFS's file table runs past its end")
                _, sibling, offset, length, _ = struct.unpack_from("<IIQQI", files, f)
                out.append(File(f"romfs/{prefix}{name(files, f, 0x1C)}", level3 + data + offset, length))
                f = sibling
            c = child
            while c != 0xFFFFFFFF:
                walk(c, f"{prefix}{name(dirs, c, 0x14)}/")
                c = struct.unpack_from("<I", dirs, c + 4)[0]
        walk(0, "")
        return out


def select(found: list[File], only: list[str]) -> list[File]:
    """The files whose path matches any of the patterns (all of them with none)."""
    return [f for f in found if not only or any(fnmatch.fnmatch(f.path, p) for p in only)]
