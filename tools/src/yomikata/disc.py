"""Reading files from disc images without mounting them: CHD (via chdman), cue/bin, and ISO.

Handles raw 2352-byte sectors (Mode 1 and Mode 2 Form 1, the data tracks of PS1/Saturn-style discs) and plain
2048-byte ISOs (PS2/PSP). Only the ISO 9660 file system is read; streamed XA audio/video sectors are never touched."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


class DiscError(ValueError):
    pass


class Image:
    """A data track as 2048-byte logical sectors."""

    def __init__(self, path: Path):
        self.f = open(path, "rb")
        head = self.f.read(16)
        if head[:12] == SYNC:
            self.raw, mode = True, head[15]
            self.offset = 16 if mode == 1 else 24        # Mode 1 / Mode 2 Form 1 user data
        else:
            self.raw, self.offset = False, 0

    def close(self) -> None:
        self.f.close()

    def sector(self, lba: int) -> bytes:
        size = 2352 if self.raw else 2048
        self.f.seek(lba * size + self.offset)
        data = self.f.read(2048)
        if len(data) != 2048:
            raise DiscError(f"sector {lba} is past the end of the image")
        return data

    def is_form2(self, lba: int) -> bool:
        """A Mode 2 Form 2 sector (streamed XA audio/video): its data isn't file-system data."""
        if not self.raw:
            return False
        self.f.seek(lba * 2352 + 18)
        sub = self.f.read(1)
        return bool(sub and sub[0] & 0x20)

    def read(self, lba: int, length: int) -> bytes:
        out = bytearray()
        n = 0
        while len(out) < length:
            out += self.sector(lba + n)
            n += 1
        return bytes(out[:length])


@dataclass
class Entry:
    name: str
    lba: int
    size: int
    is_dir: bool


def _records(data: bytes):
    i = 0
    while i < len(data):
        n = data[i]
        if n == 0:                                   # records don't cross sectors: skip to the next one
            i = (i // 2048 + 1) * 2048
            continue
        rec = data[i:i + n]
        lba = int.from_bytes(rec[2:6], "little")
        size = int.from_bytes(rec[10:14], "little")
        flags = rec[25]
        name_len = rec[32]
        name = rec[33:33 + name_len].decode("ascii", "replace")
        if name not in ("\x00", "\x01"):
            yield Entry(name.split(";")[0], lba, size, bool(flags & 2))
        i += n


class ISO9660:
    def __init__(self, image: Image):
        self.img = image
        pvd = image.sector(16)
        if pvd[1:6] != b"CD001":
            raise DiscError("no ISO 9660 file system on this track")
        self.root = Entry("", int.from_bytes(pvd[158:162], "little"), int.from_bytes(pvd[166:170], "little"), True)

    def listdir(self, d: Entry) -> list[Entry]:
        return list(_records(self.img.read(d.lba, d.size)))

    def find(self, path: str) -> Entry | None:
        """Case-insensitive lookup of a path like `SYSTEM.CNF` or `PSP_GAME/PARAM.SFO`."""
        cur = self.root
        for part in [p for p in re.split(r"[\\/]", path) if p]:
            nxt = next((e for e in self.listdir(cur) if e.name.upper() == part.upper()), None)
            if nxt is None:
                return None
            cur = nxt
        return cur

    def read(self, e: Entry) -> bytes:
        return self.img.read(e.lba, e.size)

    def walk(self, d: Entry | None = None, prefix: str = ""):
        for e in self.listdir(d or self.root):
            path = f"{prefix}{e.name}"
            if e.is_dir:
                yield from self.walk(e, path + "/")
            else:
                yield path, e


def first_data_track(cue: Path) -> Path:
    """The bin of the cue's first data track (track 1 on PS1 discs)."""
    current = None
    for line in cue.read_text(errors="replace").splitlines():
        m = re.match(r'\s*FILE\s+"(.+)"', line)
        if m:
            current = cue.parent / m.group(1)
        if re.match(r"\s*TRACK\s+\d+\s+(MODE1|MODE2)", line) and current:
            return current
    raise DiscError(f"{cue.name} has no data track")


@contextmanager
def open_data_track(path: Path, scratch: Path | None = None, chdman: str = "chdman"):
    """The image's data track, extracting a CHD to a temporary folder first (deleted afterwards)."""
    path = Path(path)
    tmp = None
    try:
        if path.suffix.lower() == ".chd":
            tmp = Path(tempfile.mkdtemp(prefix="yomikata-", dir=scratch))
            info = subprocess.run([chdman, "info", "-i", str(path)], capture_output=True, text=True, check=True).stdout
            if "CD-ROM" in info or "CHCD" in info or "CHT2" in info:
                subprocess.run([chdman, "extractcd", "-i", str(path), "-o", str(tmp / "disc.cue"),
                                "-ob", str(tmp / "disc.bin")], capture_output=True, check=True)
                track = tmp / "disc.bin"
            else:
                subprocess.run([chdman, "extractdvd", "-i", str(path), "-o", str(tmp / "disc.iso")],
                               capture_output=True, check=True)
                track = tmp / "disc.iso"
        elif path.suffix.lower() == ".cue":
            track = first_data_track(path)
        else:
            track = path
        img = Image(track)
        try:
            yield img
        finally:
            img.close()
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
