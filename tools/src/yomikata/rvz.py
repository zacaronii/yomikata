"""A GameCube or Wii disc's files out of a compressed image (`yomikata rvz`): RVZ and WIA, read as Dolphin's public
description of the two formats sets them out (docs/WiaAndRvz.md in Dolphin's repository); written from that
description, no code of Dolphin's in it. A plain image (.iso, .gcm) is read as it is.

The image keeps the disc's first 0x80 bytes in its header, the rest in groups of `chunk_size` bytes, each
compressed on its own (Zstandard for RVZ; none, bzip2, LZMA or LZMA2 for WIA; WIA's "purge" isn't read). Data
outside Wii partitions is in raw-data regions; a Wii partition's data is kept decrypted and without its hashes
(0x7C00 bytes of each 0x8000-byte sector), so its files can be read with no key. RVZ may pack a group further:
runs of the data as it is, and runs of the disc's pseudorandom padding given by a seed. The padding is never a
file's, so it is given here as zeros, not made again.

The disc's own file system: the header gives where the file system table (FST) is (0x424) and its size; its
12-byte entries are directories and files, names in a table after them. On a Wii partition the offsets are
stored divided by 4.
"""

from __future__ import annotations

import bz2
import fnmatch
import lzma
import struct
from dataclasses import dataclass
from pathlib import Path

GC, WII = 1, 2
NONE, PURGE, BZIP2, LZMA, LZMA2, ZSTD = range(6)
SECTOR, SECTOR_DATA = 0x8000, 0x7C00
HEAD_SIZE = 0x48


class RvzError(ValueError):
    pass


@dataclass
class File:
    path: str
    offset: int                  # where on the disc (or in the partition's data, for P<n>/…)
    size: int
    partition: int | None = None


@dataclass
class Region:
    start: int                   # disc offset (raw data) or sector (partition data)
    size: int                    # bytes (raw data) or sectors (partition data)
    group: int
    groups: int


def _zstd(data: bytes) -> bytes:
    try:
        import zstandard
    except ImportError:
        raise RvzError("reading RVZ needs the zstandard package (pip install zstandard)") from None
    out, d = bytearray(), zstandard.ZstdDecompressor().decompressobj()
    out += d.decompress(data)
    return bytes(out)


class Image:
    """A WIA or RVZ image, opened for reading the disc it holds."""

    def __init__(self, path: Path):
        self.f = open(path, "rb")
        head = self.f.read(HEAD_SIZE)
        if len(head) < HEAD_SIZE or head[:4] not in (b"WIA\x01", b"RVZ\x01"):
            raise RvzError("not a WIA or RVZ image")
        self.rvz = head[:4] == b"RVZ\x01"
        disc_size = struct.unpack_from(">I", head, 12)[0]
        self.iso_size = struct.unpack_from(">Q", head, 0x24)[0]
        disc = self.f.read(disc_size)
        (self.disc_type, self.compression, _level, self.chunk) = struct.unpack_from(">IIiI", disc, 0)
        self.dhead = disc[0x10:0x90]
        n_part, part_t_size, part_off = struct.unpack_from(">IIQ", disc, 0x90)
        n_raw, raw_off, raw_size, n_groups, group_off, group_size = struct.unpack_from(">IQIIQI", disc, 0xB4)
        self.props = disc[0xD5:0xD5 + disc[0xD4]] if len(disc) > 0xD4 else b""
        if self.compression == PURGE:
            raise RvzError("WIA's purge compression isn't read here")
        raw = self._decompress(self._at(raw_off, raw_size), self.compression != NONE)
        self.raw = []
        for k in range(n_raw):
            at, size, group, groups = struct.unpack_from(">QQII", raw, 24 * k)
            start = at // SECTOR * SECTOR                   # the first region starts at 0x80: begin it at 0
            self.raw.append(Region(start, at + size - start, group, groups))
        table = self._decompress(self._at(group_off, group_size), self.compression != NONE)
        each = 12 if self.rvz else 8
        self.groups = [struct.unpack_from(">III" if self.rvz else ">II", table, each * k) + ((0,) if not self.rvz else ())
                       for k in range(n_groups)]
        self.parts = []
        for k in range(n_part):
            entry = self._at(part_off + k * part_t_size, part_t_size).ljust(48, b"\0")
            self.parts.append([Region(*struct.unpack_from(">IIII", entry, 16 + 16 * s)) for s in range(2)])
        self._cache: dict[tuple[int, bool], bytes] = {}

    def close(self):
        self.f.close()

    def _at(self, at: int, size: int) -> bytes:
        self.f.seek(at)
        return self.f.read(size)

    def _decompress(self, data: bytes, compressed: bool) -> bytes:
        if not compressed or self.compression == NONE:
            return data
        if self.compression == ZSTD:
            return _zstd(data)
        if self.compression == BZIP2:
            return bz2.decompress(data)
        if self.compression == LZMA:
            d = self.props[0]
            lc, d = d % 9, d // 9
            filt = {"id": lzma.FILTER_LZMA1, "lc": lc, "lp": d % 5, "pb": d // 5,
                    "dict_size": struct.unpack_from("<I", self.props, 1)[0]}
        elif self.compression == LZMA2:
            prop = self.props[0]
            filt = {"id": lzma.FILTER_LZMA2,
                    "dict_size": 0xFFFFFFFF if prop == 40 else (2 | (prop & 1)) << (prop // 2 + 11)}
        else:
            raise RvzError(f"unknown compression {self.compression}")
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[filt]).decompress(data)

    def group(self, index: int, partition: bool) -> bytes:
        """A group's data, decompressed, its hash exceptions stepped over (partition data), RVZ-unpacked."""
        key = (index, partition)
        if key in self._cache:
            return self._cache[key]
        off4, size, packed = self.groups[index]
        compressed = bool(size & 0x80000000) if self.rvz else True
        size &= 0x7FFFFFFF
        if size == 0:
            data = b""                                       # all zeros: the caller pads
        else:
            data = self._decompress(self._at(off4 * 4, size), compressed)
            if partition:                                   # hash exceptions before the data: one list each 2 MiB
                p = 0
                for _ in range(max(1, self.chunk // 0x200000)):
                    n = struct.unpack_from(">H", data, p)[0]
                    p += 2 + 22 * n
                if not compressed or self.compression == NONE:
                    p += -p % 4
                data = data[p:]
            if packed:
                data = _unpack(data)
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[key] = data
        return data

    def read(self, at: int, size: int) -> bytes:
        """The disc's bytes from `at` (outside partitions)."""
        out = bytearray()
        while size > 0:
            if at < 0x80:
                piece = self.dhead[at:at + size]
            else:
                region = next((r for r in self.raw if r.start <= at < r.start + r.size), None)
                if region is None:
                    raise RvzError(f"no data at {at:#x} on the disc")
                k, inside = divmod(at - region.start, self.chunk)
                data = self.group(region.group + k, False).ljust(min(self.chunk, region.size - k * self.chunk), b"\0")
                piece = data[inside:inside + size]
            if not piece:
                raise RvzError(f"no data at {at:#x} on the disc")
            out += piece
            at, size = at + len(piece), size - len(piece)
        return bytes(out)

    def read_partition(self, n: int, at: int, size: int) -> bytes:
        """A Wii partition's data (decrypted, without hashes) from `at`, counted from its first sector."""
        segments = self.parts[n]
        first = segments[0].start
        per = max(1, self.chunk // SECTOR)                   # sectors a group holds
        out = bytearray()
        while size > 0:
            sector, inside = divmod(at, SECTOR_DATA)
            sector += first
            seg = next((s for s in segments if s.start <= sector < s.start + s.size), None)
            if seg is None:
                raise RvzError(f"no partition data at {at:#x}")
            k, within = divmod(sector - seg.start, per)
            data = self.group(seg.group + k, True)
            want = min(per, seg.size - k * per) * SECTOR_DATA
            data = data.ljust(want, b"\0")
            piece = data[within * SECTOR_DATA + inside:][:min(size, SECTOR_DATA - inside)]
            if not piece:
                raise RvzError(f"no partition data at {at:#x}")
            out += piece
            at, size = at + len(piece), size - len(piece)
        return bytes(out)


def _unpack(data: bytes) -> bytes:
    """RVZ packing undone: runs of data as it is, and runs of padding (a seed of 68 bytes), given as zeros."""
    out, p = bytearray(), 0
    while p + 4 <= len(data):
        size = struct.unpack_from(">I", data, p)[0]
        p += 4
        if size & 0x80000000:
            size &= 0x7FFFFFFF
            p += 68
            out += bytes(size)
        else:
            out += data[p:p + size]
            p += size
    return bytes(out)


class Plain:
    """A plain image (.iso, .gcm), read as it is."""

    def __init__(self, path: Path):
        self.f = open(path, "rb")
        self.disc_type = WII if self._at(0x18, 4) == b"\x5d\x1c\x9e\xa3" else GC
        self.parts = []

    def _at(self, at, size):
        self.f.seek(at)
        return self.f.read(size)

    def read(self, at, size):
        return self._at(at, size)

    def close(self):
        self.f.close()


def open_disc(path: Path):
    path = Path(path)
    with open(path, "rb") as f:
        magic = f.read(4)
    return Image(path) if magic in (b"WIA\x01", b"RVZ\x01") else Plain(path)


def _fst(read, shift: int, prefix: str, partition: int | None) -> list[File]:
    """The files of a disc's (or a partition's) file system, `read` giving its bytes."""
    fst_at, fst_size = struct.unpack(">II", read(0x424, 8))
    fst_at <<= shift
    fst_size <<= shift
    if not fst_size:
        return []
    table = read(fst_at, fst_size)
    count = struct.unpack_from(">I", table, 8)[0]
    if count * 12 > len(table):
        raise RvzError("the file system table runs past its size")
    names = table[count * 12:]
    out: list[File] = []

    def name(k):
        at = struct.unpack_from(">I", table, 12 * k)[0] & 0xFFFFFF
        return names[at:names.index(b"\0", at)].decode("shift_jis", "replace")
    ends = [(count, prefix)]
    for k in range(1, count):
        while ends and k >= ends[-1][0]:
            ends.pop()
        folder = ends[-1][1] if ends else prefix
        word, a, b = struct.unpack_from(">III", table, 12 * k)
        if word >> 24:
            ends.append((b, f"{folder}{name(k)}/"))
        else:
            out.append(File(folder + name(k), a << shift, b, partition))
    return out


def files(disc) -> list[File]:
    """Every file on a GameCube disc, or in each Wii partition (P0/…, P1/…)."""
    if disc.disc_type == GC:
        return _fst(disc.read, 0, "", None)
    out = []
    for n in range(len(disc.parts)):
        out += _fst(lambda at, size, n=n: disc.read_partition(n, at, size), 2, f"P{n}/", n)
    if not disc.parts:
        raise RvzError("a Wii disc's files are in its partitions: read from a WIA or RVZ image")
    return out


def content(disc, f: File) -> bytes:
    return disc.read(f.offset, f.size) if f.partition is None else disc.read_partition(f.partition, f.offset, f.size)


def select(found: list[File], only: list[str]) -> list[File]:
    return [f for f in found if not only or any(fnmatch.fnmatch(f.path, p) for p in only)]
