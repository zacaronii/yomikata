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
    crc: str | None = None            # PS2: what an emulator says of the game beside its serial
    build: str | None = None          # PS2: `<serial>-<crc>`, a script's build line (docs/script-api.md)

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def serial_from_boot(boot: str) -> tuple[str, str]:
    """`cdrom:\\SLPS_012.34;1` -> (`SLPS-01234`, `SLPS_012.34`)."""
    exe = re.split(r"[\\/:]", boot.split(";")[0])[-1].strip()
    m = re.fullmatch(r"([A-Z]{4})[_-](\d{3})\.(\d{2})", exe.upper())
    if not m:
        raise DiscError(f"boot file {exe!r} doesn't look like a serial")
    return f"{m.group(1)}-{m.group(2)}{m.group(3)}", exe


def ps2_crc(program: bytes) -> str:
    """The CRC a PS2 emulator gives a game (PCSX2's, which PINE hands out as the game's UUID): the boot
    program's 32-bit words, low byte first, all XORed together; bytes past the last whole word are left out."""
    crc = 0
    for (word,) in struct.iter_unpack("<I", program[:len(program) // 4 * 4]):
        crc ^= word
    return f"{crc:08x}"


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
            boot_path = m.group(2).split(":", 1)[-1].split(";")[0]          # cdrom:\DIR\SLPM_861.11;1 -> \DIR\…
            entry = iso.find(boot_path) or iso.find(exe)
            if entry is None:
                raise DiscError(f"boot file {exe} not found on the disc")
            program = iso.read(entry)
            if not m.group(1):
                return Release("ps1", serial, exe, hashlib.sha1(program).hexdigest())
            crc = ps2_crc(program)
            return Release("ps2", serial, exe, hashlib.sha1(program).hexdigest(), crc=crc, build=f"{serial}-{crc}")
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
    # Game Boy Advance: a fixed byte at 0xB2, the 4-character game code before it. The header is 192 bytes.
    code = head[0xAC:0xB0].decode("ascii", "replace")
    if head[0xB2:0xB3] == b"\x96" and re.fullmatch(r"[A-Z0-9]{4}", code):
        return Release("gba", f"AGB-{code}", header_crc32=f"{zlib.crc32(head[:0xC0]):08x}")
    code = head[0x0C:0x10].decode("ascii", "replace")
    if re.fullmatch(r"[A-Z0-9]{4}", code):                               # DS: the 4-character game code
        return Release("nds", code, header_crc32=f"{zlib.crc32(head[:0x160]):08x}")
    if head[0x104:0x108] == GB_LOGO:
        return identify_gb(head)
    raise DiscError("not a Game Boy, Game Boy Advance, DS or 3DS card image")


GB_LOGO = bytes.fromhex("ceed6666")                                     # the start of the logo every cartridge shows


def identify_gb(head: bytes) -> Release:
    """A Game Boy or Game Boy Color cartridge, by its header (0x100-0x14F): a Color one sets bit 7 of 0x143. A
    later cartridge has a 4-character game code in 0x13F-0x142 (its maker in 0x144, the old maker byte 0x33);
    the id is the code with the console's prefix, else the header's CRC-32. The build is that CRC-32 too."""
    crc = f"{zlib.crc32(head[0x100:0x150]):08x}"
    color = bool(head[0x143] & 0x80)
    code = head[0x13F:0x143].decode("ascii", "replace")
    if head[0x14B] == 0x33 and re.fullmatch(r"[A-Z0-9]{4}", code):
        return Release("gbc" if color else "gb", f"{'CGB' if color else 'DMG'}-{code}", header_crc32=crc)
    return Release("gbc" if color else "gb", f"{'GBC' if color else 'GB'}-{crc.upper()}", header_crc32=crc)


SFC_SUFFIXES = (".sfc", ".smc", ".swc", ".fig")


def sfc_header(rom: bytes) -> bytes:
    """The 48 header bytes of a Super Famicom image (bus addresses 0xFFB0-0xFFDF), by the rule in docs/format.md."""
    if len(rom) % 0x8000 == 512:                                        # a copier's own header comes first
        rom = rom[512:]
    best, best_score = b"", -1
    for offset, hirom in ((0x7FB0, 0), (0xFFB0, 1)):
        h = rom[offset:offset + 0x30]
        if len(h) < 0x30:
            continue
        complement, checksum = struct.unpack("<HH", h[0x2C:0x30])
        score = 2 * (complement ^ checksum == 0xFFFF) + ((h[0x25] & 1) == hirom)
        if score > best_score:                                          # on a tie, the first (0x7FB0)
            best, best_score = h, score
    if not best:
        raise DiscError("too short for a Super Famicom image")
    return best


def identify_sfc(rom: bytes) -> Release:
    h = sfc_header(rom)
    crc = zlib.crc32(h)
    code = h[2:6].decode("ascii", "replace").rstrip(" ")
    if h[0x2A] == 0x33 and re.fullmatch(r"[A-Z0-9]{2,4}", code):         # a maker byte of 0x33: the game code is there
        return Release("sfc", f"SHVC-{code}", header_crc32=f"{crc:08x}")
    return Release("sfc", f"SFC-{crc:08X}", header_crc32=f"{crc:08x}")


def _cart_name(path: Path) -> str:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            return max(z.infolist(), key=lambda i: i.file_size).filename
    return path.name


def identify(path: Path, scratch: Path | None = None) -> Release:
    path = Path(path)
    if path.suffix.lower() in (".chd", ".cue", ".bin", ".iso", ".img"):
        return identify_disc(path, scratch)
    if _cart_name(path).lower().endswith(SFC_SUFFIXES):
        return identify_sfc(_cart_bytes(path, 0x800000))
    return identify_cart(path)
