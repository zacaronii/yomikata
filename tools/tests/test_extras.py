import gzip
import json
import os
import struct

from fastapi.testclient import TestClient

from yomikata import inbox as inbox_mod
from yomikata.inbox import create_app, gunzip
from yomikata.vram import BIOS_SIZE, HW_SIZE, RAM_SIZE, VRAM_SIZE, extract_vram, views

AUTH = {"authorization": "Bearer secret"}


def fake_state(ram: bytes, vram: bytes, cpu_size: int = 0x4A0) -> bytes:
    head = b"STv4 PCSX v1.5".ljust(32, b"\0") + struct.pack("<I", 0x8B410006) + b"\0" + bytes(128 * 96 * 3)
    gpu = struct.pack("<II", 1, 0x14802000) + bytes(256 * 4) + vram
    return head + ram + bytes(BIOS_SIZE) + bytes(HW_SIZE) + os.urandom(cpu_size).replace(b"\x01\x00\x00\x00", b"\x02\x00\x00\x00") + gpu + bytes(4096)


def test_video_memory_comes_out_of_a_savestate():
    ram, vram = os.urandom(RAM_SIZE), os.urandom(VRAM_SIZE)
    state = fake_state(ram, vram)
    got = extract_vram(state, ram)
    assert got.vram == vram and got.ram_offset == 32 + 4 + 1 + 128 * 96 * 3
    assert extract_vram(state).vram == vram                     # without the RAM, from the header
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


def test_a_psp_savestate_fits_and_an_oversized_one_is_refused(tmp_path, monkeypatch):
    c = TestClient(create_app("secret", tmp_path / "data"))
    meta = {"id": "NPJH50372", "platform": "psp", "base_address": "0x08000000", "ocr": ["この学園を卒業すれば、"]}
    post = lambda state: c.post("/api/snapshots", data={"meta": json.dumps(meta)}, headers=AUTH,
                                files={"ram": ("ram", os.urandom(4096)), "state": ("s", state)})
    ppsspp = gzip.compress(bytes(40 * 1024 * 1024), 1)                 # PPSSPP's savestate is 40 MB
    assert post(ppsspp).json()["parts"] == ["ram", "state"]
    monkeypatch.setitem(inbox_mod.EXTRA_PARTS, "state", 1024 * 1024)
    assert gunzip(ppsspp, 1024 * 1024, "state") == bytes(1024 * 1024 + 1)   # unpacking stops just past the limit
    r = post(ppsspp)
    assert r.status_code == 400 and r.json()["detail"] == "state is larger than 1 MB"
    for broken in (b"\x1f\x8bnot gzip", gzip.compress(os.urandom(4096))[:-200]):      # not gzip; cut short
        assert post(broken).json()["detail"] == "state looks gzipped but doesn't decompress"
