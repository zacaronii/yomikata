"""PS1 video memory from an emulator savestate, and views of it for finding fonts.

PCSX ReARMed's savestate (what a libretro frontend gets from `retro_serialize`) is, in order: a 32-byte header, a
version word, the HLE flag, a 128x96 RGB thumbnail, main RAM (2 MB), the BIOS (512 KB), the hardware registers
(64 KB), the CPU registers, then the GPU's freeze block: version (1), status, 256 control words, and the 1 MB of video
memory (1024x512 16-bit words). The CPU block's size varies between versions, so the GPU block is found by its shape
(and by the drawing settings among its control words) rather than a fixed offset; when the snapshot's RAM is given, finding it inside the state confirms the layout."""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

RAM_SIZE, BIOS_SIZE, HW_SIZE = 0x200000, 0x80000, 0x10000
VRAM_SIZE = 1024 * 512 * 2
FREEZE_HEAD = 4 + 4 + 256 * 4


class StateError(ValueError):
    pass


@dataclass
class Extracted:
    vram: bytes
    vram_offset: int
    ram_offset: int | None


def extract_vram(state: bytes, ram: bytes | None = None) -> Extracted:
    ram_at = None
    search_from = 0
    if ram is not None:
        probe = ram[0x10000:0x10000 + 4096]                 # past the BIOS-owned low memory, which changes
        i = state.find(probe)
        if i < 0:
            raise StateError("the snapshot's RAM isn't in this savestate (another moment, or another format)")
        ram_at = i - 0x10000
        search_from = ram_at + RAM_SIZE + BIOS_SIZE + HW_SIZE
    elif state[:4] == b"STv4" or state[:2] == b"ST":
        search_from = 32 + 4 + 1 + 128 * 96 * 3 + RAM_SIZE + BIOS_SIZE + HW_SIZE
    # The GPU block: version word 1, then the status word, then 256 control words, then VRAM. The CPU registers
    # before it can hold the same two-word shape by chance, so a block whose control words 0xE1-0xE6 carry their
    # own command numbers in the top byte (the drawing settings, as the core's GPU saves them) wins; without one,
    # the first version-1 word after the CPU registers that leaves room for a whole VRAM.
    first = None
    for off in range(search_from, min(len(state) - FREEZE_HEAD - VRAM_SIZE, search_from + 0x40000) + 1, 4):
        if struct.unpack_from("<I", state, off)[0] == 1:
            status = struct.unpack_from("<I", state, off + 4)[0]
            if status & 0x1C000000:                         # GPUSTAT bits that are set on a running PS1 GPU
                settings = struct.unpack_from("<6I", state, off + 8 + 0xE1 * 4)
                if all(word >> 24 == 0xE1 + n for n, word in enumerate(settings)):
                    first = off
                    break
                if first is None:
                    first = off
    if first is not None:
        v = first + FREEZE_HEAD
        return Extracted(state[v:v + VRAM_SIZE], v, ram_at)
    raise StateError("no GPU freeze block found after the CPU registers")


def views(vram: bytes) -> dict[str, np.ndarray]:
    """`rgb`: VRAM as 15-bit colour (1024x512): framebuffers and 16-bit textures. `nibbles`: every 4-bit value as a
    grey pixel (4096x512): 4-bit textures, which is how fonts are usually kept, appear as readable glyphs here."""
    w = np.frombuffer(vram, dtype="<u2").reshape(512, 1024)
    r = (w & 31) << 3
    g = ((w >> 5) & 31) << 3
    b = ((w >> 10) & 31) << 3
    rgb = np.stack([r, g, b], axis=-1).astype(np.uint8)
    by = np.frombuffer(vram, dtype=np.uint8).reshape(512, 2048)
    nib = np.empty((512, 4096), dtype=np.uint8)
    nib[:, 0::2] = (by & 15) * 17
    nib[:, 1::2] = (by >> 4) * 17
    return {"rgb": rgb, "nibbles": nib}
