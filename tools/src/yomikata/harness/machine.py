"""What the harness plays: a libretro core with a game in it, seen as memory, a picture and buttons."""

from __future__ import annotations

import gzip
from pathlib import Path

from ..layout import RAM_BASE
from ..run import MIRRORS, PLATFORM_ALIASES

RAM_BASES = {**RAM_BASE, "wii": 0x80000000}
# Consoles whose memory is big-endian, as a script reads it here.
BIG_ENDIAN = ("gc", "wii", "saturn", "n64")
# Where a core's "system RAM" isn't the console's main memory: the stretch of the core's memory map that is,
# by its start, and others worth having beside it, as regions. mGBA gives the 32 KB in the CPU as system RAM;
# a Game Boy Advance game's data is in the 256 KB on the board (as a snapshot of one has it, inbox.py).
MAIN_STRETCH = {"gba": 0x02000000}
# A region's name is the file a snap keeps it in beside the main memory's (<snap>.ram.gz), so none is "ram".
OTHER_STRETCHES = {"gba": {"iwram": 0x03000000}, "pce-cd": {"wram": 0x1F0000}}
# Main memory made of stretches that follow one another: a PC Engine CD's Super System Card RAM (192 KB at
# 0xD0000) runs on into the CD's own RAM (64 KB at 0x100000), where a CD game keeps what it loads. Read whole
# here; a script reads through the core's map, which leaves out the card's middle 64 KB (docs/script-api.md).
JOINED = {"pce-cd": (0xD0000, 0x100000)}
# A Game Boy's cartridge, whole: a core shows only the two banks switched in now, and a game that draws its text
# straight from the cartridge has switched to others by the time a line stands. The file is given to scripts
# at this address on, byte for byte (docs/script-api.md).
CARTRIDGE = {"gb": 0x01000000, "gbc": 0x01000000}
# A Game Boy's I/O registers and high RAM (0xFF00-0xFFFF), where the core maps them (mGBA does): the LCD's
# registers say what the screen shows (LCDC at 0xFF40: the window on or off, which map; WY and WX at 0xFF4A
# where it is), which the game's copy of the screen in work RAM doesn't. Read through the core's map, and kept
# in a snap as the region `io`, answering at its own addresses there too (docs/script-api.md).
IO = {"gb": (0xFF00, 0x100), "gbc": (0xFF00, 0x100)}
IO_REGISTERS = 0x80                     # the I/O registers' part of it, without which the region isn't kept
# A core whose "system RAM" is more than one of the console's stretches, kept as 16-bit words in the host's
# order: mednafen_saturn gives the Saturn's work RAM so, the low 1 MB (0x00200000) and then the high 1 MB
# (0x06000000), each pair of bytes swapped; mupen64plus_next gives the N64's RDRAM (8 MB, 0x80000000 on) as
# 32-bit words, each one's four bytes reversed. Here they are read at the console's own addresses and in its
# order (big-endian): the first stretch as main memory, the others as regions ("lwram" on a Saturn). Each
# entry: the console's address, where it starts in the core's system RAM, its length, its region's name.
WORDS = {"saturn": ((0x06000000, 0x100000, 0x100000, None), (0x00200000, 0, 0x100000, "lwram")),
         "n64": ((0x80000000, 0, 0x800000, None),)}
WORD_SIZE = {"saturn": 2, "n64": 4}


def _flip(raw: bytes, size: int) -> bytes:
    """Each `size`-byte word of `raw` with its bytes reversed (its own inverse)."""
    out = bytearray(len(raw))
    for k in range(size):
        out[k::size] = raw[size - 1 - k::size]
    return bytes(out)


def region_starts(platform: str) -> dict[str, int]:
    """A platform's regions beside main memory, by name, at the console address each starts at."""
    out = dict(OTHER_STRETCHES.get(platform, {}))
    out.update({name: start for start, _, _, name in WORDS.get(platform, ())[1:]})
    if platform in IO:
        out["io"] = IO[platform][0]
    return out


# The same memory again past the CPU's cache: where those addresses begin, and how far below them the cached
# ones are. The Saturn's SH-2 at 0x20000000 on; the N64's MIPS at 0xA0000000 (KSEG1) for 0x80000000 (KSEG0).
UNCACHED = {"saturn": (0x20000000, 0x20000000), "n64": (0xA0000000, 0x20000000)}


def stretch(maps: list[tuple[int, int, int]], start: int) -> tuple[int, int, int] | None:
    """The stretch of a core's memory map that begins at `start`: (start, length, pointer), or None."""
    return next((m for m in maps if m[0] == start and m[1]), None)
WARM_UP = 300


class Machine:
    """A core with a game loaded. `platform` is the catalog's name for the console."""

    def __init__(self, core_path: Path, game: Path, platform: str, system_dir: Path, options: dict[str, str] | None = None):
        from ..replay.libretro import Core
        self.platform = PLATFORM_ALIASES.get(platform, platform)
        self.core = Core(core_path, system_dir, options)
        self.core.load_game(game)
        self.cartridge = Path(game).read_bytes() if self.platform in CARTRIDGE else b""
        self.frames = 0
        # A core that draws with the card can't be loaded a second time in one process: the second load never
        # comes back (seen with Dolphin).
        self.once = self.core.hw is not None

    def run(self, frames: int, buttons: set[str], stick: tuple[float, float]) -> None:
        self.core.run(frames, buttons, stick)
        self.frames += frames

    def close(self) -> None:
        """Ends the core (unload, deinit)."""
        self.core.close()

    def picture(self):
        return self.core.frame_rgb()

    def ram(self) -> tuple[int, bytes]:
        """Main memory: the address of its first byte, and its bytes."""
        import ctypes as C
        if self.platform in WORDS:
            start, at, length, _ = WORDS[self.platform][0]
            return start, self._words(at, length)
        main = stretch(self.core.maps, MAIN_STRETCH[self.platform]) if self.platform in MAIN_STRETCH else None
        if main:
            return main[0], C.string_at(main[2], main[1])
        parts = [stretch(self.core.maps, start) for start in JOINED.get(self.platform, ())]
        if parts and all(parts) and all(a[0] + a[1] == b[0] for a, b in zip(parts, parts[1:])):
            return parts[0][0], b"".join(C.string_at(p[2], p[1]) for p in parts)
        base = self.core.ram_base()
        return (RAM_BASES.get(self.platform, 0) if base is None else base), self.core.ram()

    def read(self, address: int, size: int) -> bytes:
        """Bytes at one of the console's addresses without copying all of memory; fewer where memory ends."""
        import ctypes as C
        from ..replay.libretro import MEMORY_SYSTEM_RAM
        at = address - CARTRIDGE.get(self.platform, 1 << 40)
        if self.cartridge and 0 <= at < len(self.cartridge):
            return self.cartridge[at:at + size]
        if self.platform in WORDS:
            found = self._in_words(address)
            if not found:
                return b""
            offset, left = found
            return self._words(offset, max(0, min(size, left)))
        if self.core.maps:                      # where the core says its memory is; a core may say it at
            data = self.core.read(address, size)        # other addresses than the game uses (PS1: from 0)
            if data:
                return data
        ptr = self.core.lib.retro_get_memory_data(MEMORY_SYSTEM_RAM)
        length = self.core.lib.retro_get_memory_size(MEMORY_SYSTEM_RAM)
        for start in (RAM_BASES.get(self.platform, 0), *self.mirrors()):
            at = address - start
            if ptr and 0 <= at < length:
                return C.string_at(ptr + at, max(0, min(size, length - at)))
        return b""

    def write(self, address: int, data: bytes) -> int:
        """Puts bytes at one of the console's addresses, in main memory; gives how many fitted."""
        import ctypes as C
        from ..replay.libretro import MEMORY_SYSTEM_RAM
        if self.platform in WORDS:
            found = self._in_words(address)
            if not found:
                return 0
            offset, left = found
            size = max(0, min(len(data), left))
            if size:
                w = WORD_SIZE[self.platform]
                first, last = offset // w * w, -(-(offset + size) // w) * w   # whole words around the bytes
                words = bytearray(self._words(first, last - first))
                words[offset - first:offset - first + size] = data[:size]
                ptr = self.core.lib.retro_get_memory_data(MEMORY_SYSTEM_RAM)
                C.memmove(ptr + first, _flip(bytes(words), w), len(words))
            return size
        for start, length, ptr in self.core.maps:
            if start <= address < start + length:
                size = max(0, min(len(data), start + length - address))
                C.memmove(ptr + (address - start), data, size)
                return size
        ptr = self.core.lib.retro_get_memory_data(MEMORY_SYSTEM_RAM)
        length = self.core.lib.retro_get_memory_size(MEMORY_SYSTEM_RAM)
        for start in (RAM_BASES.get(self.platform, 0), *self.mirrors()):
            at = address - start
            if ptr and 0 <= at < length:
                size = max(0, min(len(data), length - at))
                C.memmove(ptr + at, data, size)
                return size
        return 0

    def core_options(self) -> dict[str, dict]:
        """The core's options as it declares them: what each is, its values (the first is the core's own
        choice), and the value given at load, if one was."""
        out = {}
        for key, said in self.core.variables.items():
            what, _, values = said.partition("; ")
            given = self.core.options.get(key.encode())
            out[key] = {"what": what, "values": values.split("|"), **({"given": given.decode()} if given else {})}
        return out

    def regions(self) -> dict[str, bytes]:
        video = self.core.video_ram()
        if not video and self.platform == "ps1":
            video = self._ps1_video()
        out = {"vram": video} if video else {}
        for _, at, length, name in WORDS.get(self.platform, ())[1:]:
            out[name] = self._words(at, length)
        import ctypes as C
        for name, start in OTHER_STRETCHES.get(self.platform, {}).items():
            other = stretch(self.core.maps, start)
            if other:
                out[name] = C.string_at(other[2], other[1])
        if self.platform in IO:
            # Read a stretch of the core's map at a time: a core gives the registers and the high RAM as
            # stretches of their own (mGBA: 0xFF00-0xFF7F, 0xFF80-0xFFFE, and not 0xFFFF). What none gives is 0.
            start, length = IO[self.platform]
            io, at = bytearray(), start
            while len(io) < length:
                piece = self.read(at, length - len(io))
                if not piece:
                    break
                io += piece
                at += len(piece)
            if len(io) >= IO_REGISTERS:             # the registers at least
                out["io"] = bytes(io) + bytes(length - len(io))
        return out

    def _ps1_video(self) -> bytes | None:
        """A PlayStation core gives out no video memory, but its savestate holds it (vram.py): 1024x512 16-bit
        words. Taken from a state made now, once for each frame it is asked for."""
        from ..vram import StateError, extract_vram
        if getattr(self, "_video_at", None) != self.frames:
            try:
                self._video = extract_vram(self.core.save_state()).vram
            except (StateError, RuntimeError):
                self._video = None
            self._video_at = self.frames
        return self._video

    def _in_words(self, address: int) -> tuple[int, int] | None:
        """Where a console address of a WORDS platform is in the core's system RAM, and how many bytes are
        left from there in its stretch; None outside them."""
        uncached = UNCACHED.get(self.platform)
        if uncached and address & 0xF0000000 == uncached[0]:
            address -= uncached[1]
        for start, at, length, _ in WORDS[self.platform]:
            if start <= address < start + length:
                return at + address - start, start + length - address
        return None

    def _words(self, offset: int, size: int) -> bytes:
        """Bytes of the core's system RAM from `offset`, its words put back in the console's order."""
        import ctypes as C
        from ..replay.libretro import MEMORY_SYSTEM_RAM
        ptr = self.core.lib.retro_get_memory_data(MEMORY_SYSTEM_RAM)
        length = self.core.lib.retro_get_memory_size(MEMORY_SYSTEM_RAM)
        w = WORD_SIZE[self.platform]
        first, last = offset // w * w, min(length // w * w, -(-(offset + size) // w) * w)
        if not ptr or first >= last:
            return b""
        out = _flip(C.string_at(ptr + first, last - first), w)
        return out[offset - first:offset - first + size]

    def mirrors(self) -> tuple[int, ...]:
        return MIRRORS.get(self.platform, ())

    def save_state(self) -> bytes:
        return self.core.save_state()

    def load_state(self, state: bytes) -> None:
        # A core that draws with the card isn't fully up in its first frames: a state loaded then is taken
        # and the game stands still (seen with Dolphin at two frames; at 300 it goes on).
        if self.core.hw is not None and self.frames < WARM_UP:
            self.core.run(WARM_UP - self.frames)
        self.core.load_state(gzip.decompress(state) if state[:2] == b"\x1f\x8b" else state)
        self.frames = max(self.frames, WARM_UP if self.core.hw is not None else 0)
