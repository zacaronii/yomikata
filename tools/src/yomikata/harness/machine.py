"""What the harness plays: a libretro core with a game in it, seen as memory, a picture and buttons."""

from __future__ import annotations

import gzip
from pathlib import Path

from ..layout import RAM_BASE
from ..run import MIRRORS, PLATFORM_ALIASES

RAM_BASES = {**RAM_BASE, "wii": 0x80000000}
WARM_UP = 300


class Machine:
    """A core with a game loaded. `platform` is the catalog's name for the console."""

    def __init__(self, core_path: Path, game: Path, platform: str, system_dir: Path, options: dict[str, str] | None = None):
        from ..replay.libretro import Core
        self.platform = PLATFORM_ALIASES.get(platform, platform)
        self.core = Core(core_path, system_dir, options)
        self.core.load_game(game)
        self.frames = 0

    def run(self, frames: int, buttons: set[str], stick: tuple[float, float]) -> None:
        self.core.run(frames, buttons, stick)
        self.frames += frames

    def picture(self):
        return self.core.frame_rgb()

    def ram(self) -> tuple[int, bytes]:
        """Main memory: the address of its first byte, and its bytes."""
        base = self.core.ram_base()
        return (RAM_BASES.get(self.platform, 0) if base is None else base), self.core.ram()

    def read(self, address: int, size: int) -> bytes:
        """Bytes at one of the console's addresses without copying all of memory; fewer where memory ends."""
        import ctypes as C
        from ..replay.libretro import MEMORY_SYSTEM_RAM
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

    def regions(self) -> dict[str, bytes]:
        video = self.core.video_ram()
        return {"vram": video} if video else {}

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
