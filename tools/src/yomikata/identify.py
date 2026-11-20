"""Which release an image is: its id and the hash of its boot executable (discs) or header (cartridges)."""

from __future__ import annotations

import hashlib
import re
import struct
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

from .disc import ISO9660, DiscError, open_data_track


@dataclass
class Release:
    platform: str
    id: str
    exe: str | None = None
    exe_sha1: str | None = None
    header_crc32: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def serial_from_boot(boot: str) -> tuple[str, str]:
    """`cdrom:\\SLPS_012.34;1` -> (`SLPS-01234`, `SLPS_012.34`)."""
    exe = re.split(r"[\\/:]", boot.split(";")[0])[-1].strip()
    m = re.fullmatch(r"([A-Z]{4})[_-](\d{3})\.(\d{2})", exe.upper())
    if not m:
        raise DiscError(f"boot file {exe!r} doesn't look like a serial")
    return f"{m.group(1)}-{m.group(2)}{m.group(3)}", exe


def identify_disc(path: Path, scratch: Path | None = None) -> Release:
    with open_data_track(path, scratch) as img:
        iso = ISO9660(img)
        cnf = iso.find("SYSTEM.CNF")
        if cnf:
            text = iso.read(cnf).decode("ascii", "replace")
            m = re.search(r"BOOT(2?)\s*=\s*(\S+)", text)
            if not m:
                raise DiscError("SYSTEM.CNF has no BOOT line")
            serial, exe = serial_from_boot(m.group(2))
            entry = iso.find(exe)
            if entry is None:
                raise DiscError(f"boot file {exe} not found on the disc")
            return Release("ps2" if m.group(1) else "ps1", serial, exe, hashlib.sha1(iso.read(entry)).hexdigest())
        umd = iso.find("UMD_DATA.BIN")
        if umd:
            disc_id = iso.read(umd).split(b"|")[0].decode("ascii").replace("-", "")
            boot = iso.find("PSP_GAME/SYSDIR/EBOOT.BIN")
            return Release("psp", disc_id, "EBOOT.BIN", hashlib.sha1(iso.read(boot)).hexdigest() if boot else None)
        raise DiscError("not a PS1, PS2 or PSP disc (no SYSTEM.CNF or UMD_DATA.BIN)")


def _cart_bytes(path: Path, n: int, offset: int = 0) -> bytes:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            with z.open(max(z.infolist(), key=lambda i: i.file_size)) as f:
                f.seek(offset)
                return f.read(n)
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(n)


def identify_cart(path: Path) -> Release:
    head = _cart_bytes(path, 0x200)
    if head[0x100:0x104] == b"NCSD":                                    # 3DS card image
        ncch = _cart_bytes(path, 0x200, struct.unpack("<I", head[0x120:0x124])[0] * 0x200)
        code = ncch[0x150:0x160].split(b"\0")[0].decode("ascii")
        return Release("3ds", code, header_crc32=f"{zlib.crc32(ncch):08x}")
    code = head[0x0C:0x10].decode("ascii", "replace")
    if re.fullmatch(r"[A-Z0-9]{4}", code):                               # DS: the 4-character game code
        return Release("nds", code, header_crc32=f"{zlib.crc32(head[:0x160]):08x}")
    raise DiscError("not a DS or 3DS card image")


def identify(path: Path, scratch: Path | None = None) -> Release:
    path = Path(path)
    if path.suffix.lower() in (".chd", ".cue", ".bin", ".iso", ".img"):
        return identify_disc(path, scratch)
    return identify_cart(path)
