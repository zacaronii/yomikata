import gzip
import json
import os
import struct

from fastapi.testclient import TestClient

from yomikata.inbox import create_app
from yomikata.vram import BIOS_SIZE, HW_SIZE, RAM_SIZE, VRAM_SIZE, extract_vram, views

AUTH = {"authorization": "Bearer secret"}


def fake_state(ram: bytes, vram: bytes, cpu_size: int = 0x4A0, settings: bool = False, decoy: bool = False) -> bytes:
    head = b"STv4 PCSX v1.5".ljust(32, b"\0") + struct.pack("<I", 0x8B410006) + b"\0" + bytes(128 * 96 * 3)
    control = [0] * 256
    if settings:
        control[0xE1:0xE7] = [(0xE1 + n) << 24 | 0x200 for n in range(6)]
    gpu = struct.pack("<II", 1, 0x14802000) + struct.pack("<256I", *control) + vram
    cpu = bytearray(os.urandom(cpu_size).replace(b"\x01\x00\x00\x00", b"\x02\x00\x00\x00"))
    if decoy:                                                   # two words in the CPU registers that look like a GPU block's start
        cpu[0x26C:0x274] = struct.pack("<II", 1, 0x1CA39B2A)
    return head + ram + bytes(BIOS_SIZE) + bytes(HW_SIZE) + bytes(cpu) + gpu + bytes(4096)


def test_video_memory_comes_out_of_a_savestate():
    ram, vram = os.urandom(RAM_SIZE), os.urandom(VRAM_SIZE)
    state = fake_state(ram, vram)
    got = extract_vram(state, ram)
    assert got.vram == vram and got.ram_offset == 32 + 4 + 1 + 128 * 96 * 3
    assert extract_vram(state).vram == vram                     # without the RAM, from the header
    # a look-alike in the CPU registers loses to the block that has the GPU's drawing settings
    assert extract_vram(fake_state(ram, vram, settings=True, decoy=True)).vram == vram
    v = views(vram)
    assert v["rgb"].shape == (512, 1024, 3) and v["nibbles"].shape == (512, 4096)


def test_snapshots_keep_their_extra_parts(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    png = b"\x89PNG\r\n\x1a\n" + bytes(100)
    meta = {"id": "SLPS-02100", "platform": "ps1", "base_address": "0x80000000", "ocr": ["ハイ、そこお！"]}
    r = c.post("/api/snapshots", data={"meta": json.dumps(meta)}, headers=AUTH,
               files={"ram": ("ram", os.urandom(4096)), "state": ("s", gzip.compress(os.urandom(8192))),
                      "vram": ("v", os.urandom(1024)), "screen": ("p.png", png), "junk": ("j", b"x")})
    assert r.json()["parts"] == ["ram", "screen", "state", "vram"]
    sha = r.json()["sha1"]
    folder = tmp_path / "data/snapshots/ps1/SLPS-02100"
    assert (folder / f"{sha}.screen.png").read_bytes() == png and (folder / f"{sha}.state.gz").exists()
    assert c.get("/api/snapshots", headers=AUTH).json()["games"][0]["with"] == {"state": 1, "vram": 1, "screen": 1}
    bad = c.post("/api/snapshots", data={"meta": json.dumps(meta)}, headers=AUTH,
                 files={"ram": ("ram", os.urandom(4096)), "screen": ("p.png", b"not a png")})
    assert bad.status_code == 400 and "PNG" in bad.json()["detail"]
