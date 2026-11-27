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


PCE_CD = b"PC Engine CD-ROM SYSTEM\0"
SEGA_DISCS = {b"SEGA SEGASATURN ": ("saturn", 0x20, 0x2A, 0x38, rb"CD-(\d+)/(\d+)"),
              b"SEGA SEGAKATANA ": ("dc", 0x40, 0x4A, 0x20, rb"GD-ROM(\d+)/(\d+)")}


def identify_sega(head: bytes) -> Release | None:
    """A Saturn or Dreamcast disc by the header at its first data sector (Saturn: the system ID; Dreamcast:
    IP.BIN): the product number (10 bytes, spaces trimmed), with "-D" and the disc's number after it when the
    disc is one of a set ("CD-1/2", "GD-ROM1/3"), since the discs of a set share a product number. The build is
    the id, "-" and the version (6 bytes, "V1.005"); header_crc32 the CRC-32 of the header's first 0x100 bytes."""
    kind = SEGA_DISCS.get(head[:16])
    if not kind:
        return None
    platform, at_product, at_version, at_device, disc = kind
    product = head[at_product:at_product + 10].decode("ascii", "replace").strip()
    version = head[at_version:at_version + 6].decode("ascii", "replace").strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{2,9}", product):
        return None
    m = re.search(disc, head[at_device:at_device + 16])
    code = f"{product}-D{int(m.group(1))}" if m and int(m.group(2)) > 1 else product
    return Release(platform, code, header_crc32=f"{zlib.crc32(head[:0x100]):08x}", build=f"{code}-{version}")


def identify_disc(path: Path, scratch: Path | None = None) -> Release:
    with open_data_track(path, scratch) as img:
        first = img.sector(0)
        sega = identify_sega(first)
        if sega:
            return sega
        boot = img.sector(1) if img.raw else b""
        if boot[0x20:0x20 + len(PCE_CD)] == PCE_CD:
            # A PC Engine CD: no file system and no product code; its boot block (the data track's second
            # sector: the system's name, the makers, the program's name) names it, by its CRC-32.
            crc = f"{zlib.crc32(boot):08x}"
            return Release("pce-cd", f"PCECD-{crc.upper()}", header_crc32=crc)
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


N64_FIRST = bytes.fromhex("80371240")                                  # a cartridge's first word, big-endian


def n64_big_endian(data: bytes) -> bytes | None:
    """An N64 cartridge's bytes in its own order (big-endian, as a .z64 keeps them), from any of the three
    orders dumps come in: .z64 as it is, .v64 with each 16-bit word's bytes swapped, .n64 with each 32-bit
    word's reversed. None when the first word isn't a cartridge's."""
    n = len(data) // 4 * 4
    if data[:4] == N64_FIRST:
        return data[:n]
    if data[:4] == bytes.fromhex("37804012"):
        out = bytearray(n)
        out[0::2], out[1::2] = data[1:n:2], data[0:n:2]
        return bytes(out)
    if data[:4] == bytes.fromhex("40123780"):
        out = bytearray(n)
        for k in range(4):
            out[k::4] = data[3 - k:n:4]
        return bytes(out)
    return None


def identify_n64(head: bytes) -> Release | None:
    """An N64 cartridge, by its header (the first 0x40 bytes, in the cartridge's own order): the 4-character game
    code at 0x3B (a kind, two letters, a region: NAFJ) with "NUS-" before it, else the header's CRC-32; the build
    is the id, "-" and the version at 0x3F as two hex digits; header_crc32 the CRC-32 of the 0x40 bytes."""
    head = n64_big_endian(head[:0x40])
    if head is None or len(head) < 0x40:
        return None
    crc = f"{zlib.crc32(head):08x}"
    code = head[0x3B:0x3F].decode("ascii", "replace")
    ident = f"NUS-{code}" if re.fullmatch(r"[A-Z0-9]{4}", code) else f"N64-{crc.upper()}"
    return Release("n64", ident, header_crc32=crc, build=f"{ident}-{head[0x3F]:02X}")


def identify_cart(path: Path) -> Release:
    head = _cart_bytes(path, 0x200)
    n64 = identify_n64(head)
    if n64:
        return n64
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
    raise DiscError("not a Game Boy, Game Boy Advance, N64, DS or 3DS card image")


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


def sfc_layout(rom: bytes) -> tuple[bytes, bytes, bool]:
    """A Super Famicom image without a copier's header, its 48 header bytes (bus addresses 0xFFB0-0xFFDF) and
    whether it is laid out HiROM (its header at 0xFFB0 in the file) or LoROM (at 0x7FB0), by the rule in
    docs/format.md."""
    if len(rom) % 0x8000 == 512:                                        # a copier's own header comes first
        rom = rom[512:]
    best, best_score, best_hirom = b"", -1, False
    for offset, hirom in ((0x7FB0, 0), (0xFFB0, 1)):
        h = rom[offset:offset + 0x30]
        if len(h) < 0x30:
            continue
        complement, checksum = struct.unpack("<HH", h[0x2C:0x30])
        score = 2 * (complement ^ checksum == 0xFFFF) + ((h[0x25] & 1) == hirom)
        if score > best_score:                                          # on a tie, the first (0x7FB0)
            best, best_score, best_hirom = h, score, bool(hirom)
    if not best:
        raise DiscError("too short for a Super Famicom image")
    return rom, best, best_hirom


def sfc_header(rom: bytes) -> bytes:
    """The 48 header bytes of a Super Famicom image (bus addresses 0xFFB0-0xFFDF), by the rule in docs/format.md."""
    return sfc_layout(rom)[1]


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


GC_MAGIC, WII_MAGIC = bytes.fromhex("c2339f3d"), bytes.fromhex("5d1c9ea3")


def identify_nintendo_disc(head: bytes) -> Release | None:
    """A GameCube or Wii disc by its header's first 0x80 bytes: the id (6 characters), the disc's version at 0x07,
    the magic at 0x1C (GameCube) or 0x18 (Wii). The build is the id and the version as two hex digits
    (G8MJ01-00); header_crc32 the CRC-32 of those 0x80 bytes. None when they aren't a disc header."""
    if len(head) < 0x80:
        return None
    platform = "gc" if head[0x1C:0x20] == GC_MAGIC else "wii" if head[0x18:0x1C] == WII_MAGIC else None
    code = head[:6].decode("ascii", "replace")
    if not platform or not re.fullmatch(r"[A-Z0-9]{6}", code):
        return None
    return Release(platform, code, header_crc32=f"{zlib.crc32(head[:0x80]):08x}", build=f"{code}-{head[7]:02X}")


def identify_hucard(rom: bytes) -> Release:
    """A PC Engine HuCard: "PCE-" and the CRC-32 of the ROM, without the 512-byte copier header some dumps have
    (a size that leaves 512 when divided by 8192)."""
    if len(rom) % 8192 == 512:
        rom = rom[512:]
    crc = f"{zlib.crc32(rom):08x}"
    return Release("pce", f"PCE-{crc.upper()}", header_crc32=crc)


INES_MAGIC, FWNES_MAGIC, FDS_SIDE = b"NES\x1a", b"FDS\x1a", 65500
FDS_BLOCK1 = b"\x01*NINTENDO-HVC*"


def identify_nes(rom: bytes) -> Release:
    """A Famicom (NES) cartridge: "FC-" and the CRC-32 of its PRG and CHR ROM, as No-Intro counts it: without
    the 16-byte iNES or NES 2.0 header, and without the 512-byte trainer a header may say is there. A file
    with no header is taken as PRG and CHR already, so every header variant of one dump gives one id."""
    if rom[:4] == INES_MAGIC:
        rom = rom[16 + (512 if rom[6] & 0x04 else 0):]
    crc = f"{zlib.crc32(rom):08x}"
    return Release("fc", f"FC-{crc.upper()}", header_crc32=crc)


def identify_fds(disk: bytes) -> Release:
    """A Famicom Disk System disk, by the disk-info block that begins each side: "FDS-", the maker's code (two
    hex digits) and the game's name code (four characters, ones not A-Z or 0-9 as "-", spaces at the end left
    out); for the second disk of a set or later, "-D" and its number from 1 (the first disk has none). The build
    is the id and the revision as two hex digits, header_crc32 the CRC-32 of the first side's info block (56
    bytes). A file with the 16-byte fwNES header and one without give the same release."""
    if disk[:4] == FWNES_MAGIC:
        disk = disk[16:]
    if disk[:15] != FDS_BLOCK1:
        raise ValueError("not a Famicom Disk System image: its first side has no disk-info block")
    block = disk[:0x38]
    maker, name, revision, number = block[0x0F], block[0x10:0x14], block[0x15], block[0x17]
    code = re.sub(r"[^A-Z0-9]", "-", name.decode("ascii", "replace").rstrip(" ")) or "----"
    ident = f"FDS-{maker:02X}{code}" + (f"-D{number + 1}" if number else "")
    return Release("fds", ident, header_crc32=f"{zlib.crc32(block):08x}", build=f"{ident}-{revision:02X}")


MD_SUFFIXES = (".md", ".gen", ".smd", ".bin")
SMD_BLOCK = 0x4000


def md_unpack(data: bytes) -> bytes:
    """A Mega Drive image as the console sees it: an SMD dump (a 512-byte header, then 16 KB blocks, each the odd
    bytes' 8 KB and then the even bytes') put back in order; any other image as it is."""
    if len(data) % SMD_BLOCK == 0x200 and data[0x300:0x304] != b"SEGA":
        body, out = data[0x200:], bytearray()
        for at in range(0, len(body) - SMD_BLOCK + 1, SMD_BLOCK):
            block = body[at:at + SMD_BLOCK]
            pairs = bytearray(SMD_BLOCK)
            pairs[0::2], pairs[1::2] = block[SMD_BLOCK // 2:], block[:SMD_BLOCK // 2]
            out += pairs
        return bytes(out)
    return data


def is_md(head: bytes) -> bool:
    """Whether an image's first bytes are a Mega Drive cartridge's ("SEGA" at 0x100, as "SEGA MEGA DRIVE" or
    "SEGA GENESIS" begin), an SMD dump's once put back in order too."""
    return head[0x100:0x104] == b"SEGA" or md_unpack(head + bytes(-len(head) % SMD_BLOCK))[0x100:0x104] == b"SEGA"


def identify_md(data: bytes) -> Release:
    """A Mega Drive cartridge: its header at 0x100 ("SEGA MEGA DRIVE" or "SEGA GENESIS"). The id is the serial of
    the product code at 0x180 ("GM G-5512   00": the kind, the serial, the revision at 0x18C), as the console's
    discs are named; a header without one gives "MD-" and the CRC-32 of the whole ROM. The build is the id and the
    revision; header_crc32 the ROM's CRC-32. An SMD dump is put back in order first."""
    rom = md_unpack(data)
    if rom[0x100:0x104] != b"SEGA":
        raise ValueError("not a Mega Drive cartridge: no SEGA header at 0x100")
    crc = f"{zlib.crc32(rom):08x}"
    code = rom[0x180:0x18E].decode("ascii", "replace")
    serial = re.sub(r"[^A-Z0-9-]", "", code[3:12].strip().upper().replace(" ", "-")).strip("-")
    revision = re.sub(r"[^A-Z0-9]", "", code[12:14].upper()) or "00"
    if re.fullmatch(r"[A-Z0-9][A-Z0-9-]{3,19}", serial):
        return Release("md", serial, header_crc32=crc, build=f"{serial}-{revision}")
    ident = f"MD-{crc.upper()}"
    return Release("md", ident, header_crc32=crc, build=ident)


def identify(path: Path, scratch: Path | None = None) -> Release:
    path = Path(path)
    if _cart_name(path).lower().endswith(".pce"):
        return identify_hucard(_cart_bytes(path, 0x800000))
    if _cart_name(path).lower().endswith(".nes"):
        return identify_nes(_cart_bytes(path, 0x1000000))
    if _cart_name(path).lower().endswith(".fds"):
        return identify_fds(_cart_bytes(path, 16 + 0x38))
    name = _cart_name(path).lower()
    if name.endswith(MD_SUFFIXES) and (not name.endswith(".bin") or is_md(_cart_bytes(path, 0x4400))):
        return identify_md(_cart_bytes(path, 0x1000000))
    if path.suffix.lower() in (".rvz", ".wia", ".gcm", ".iso"):
        with open(path, "rb") as f:
            head = f.read(0xD8)
        # A compressed image (RVZ, WIA) keeps a copy of the disc header's first 0x80 bytes at 0x58.
        found = identify_nintendo_disc(head[0x58:0xD8] if head[:3] in (b"RVZ", b"WIA") else head[:0x80])
        if found:
            return found
        if path.suffix.lower() in (".rvz", ".wia", ".gcm"):
            raise DiscError("not a GameCube or Wii disc image")
    if path.suffix.lower() in (".chd", ".cue", ".gdi", ".bin", ".iso", ".img"):
        return identify_disc(path, scratch)
    if _cart_name(path).lower().endswith(SFC_SUFFIXES):
        return identify_sfc(_cart_bytes(path, 0x800000))
    return identify_cart(path)
