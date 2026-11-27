import gzip
import json
import os
import struct

from fastapi.testclient import TestClient

from yomikata import inbox as inbox_mod
from yomikata.inbox import create_app, gunzip
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


def test_snapshots_of_saturn_pce_cd_dreamcast_and_wii_with_their_region_parts(tmp_path):
    """The platforms Genkan sends that the inbox refused (saturn, pce-cd, dc, wii) are taken at their main memory,
    as the harness has it; a platform's region parts (lwram, io, wram) are kept beside the snapshot as
    <sha1>.<region>.gz, as the harness's snaps keep them; a part too large, or another platform's, is refused
    with the reason; a part of no known name isn't read."""
    c = TestClient(create_app("secret", tmp_path / "data"))

    def post(game, platform, base, ram, **parts):
        meta = {"id": game, "platform": platform, "base_address": base, "ocr": ["こんにちは"]}
        files = {"ram": ("ram", ram), **{k: (k, v) for k, v in parts.items()}}
        return c.post("/api/snapshots", data={"meta": json.dumps(meta)}, headers=AUTH, files=files)

    cases = [("GS-9037-D1", "saturn", "0x06000000", 0x100000, {"lwram": os.urandom(0x100000)}),
             ("PCECD-92261ECE", "pce-cd", "0xD0000", 0x40000, {"wram": os.urandom(0x2000), "vram": os.urandom(0x10000)}),
             ("HDR-0152-D1", "dc", "0x8C000000", 0x1000000, {}),
             ("RZDJ01", "wii", "0x80000000", 0x1800000, {}),
             ("GB-29D07340", "gb", "0xC000", 0x2000, {"io": os.urandom(0x100)})]
    for game, platform, base, size, parts in cases:
        ram = os.urandom(4096) + bytes(size - 4096)
        r = post(game, platform, base, gzip.compress(ram, 1), **{k: gzip.compress(v) for k, v in parts.items()})
        assert r.status_code == 200, (platform, r.json())
        assert r.json()["parts"] == sorted(["ram", *parts]), platform
        folder = tmp_path / "data/snapshots" / platform / game
        for name, data in parts.items():
            assert gzip.decompress((folder / f"{r.json()['sha1']}.{name}.gz").read_bytes()) == data, (platform, name)
    # Too large for the platform: refused, saying the size.
    r = post("GS-9037-D1", "saturn", "0x06000000", os.urandom(4096), lwram=os.urandom(0x100001))
    assert r.status_code == 400 and r.json()["detail"] == "lwram is larger than saturn's 1024 KB"
    r = post("GB-29D07340", "gb", "0xC000", os.urandom(4096), io=os.urandom(0x101))
    assert r.status_code == 400 and r.json()["detail"] == "io is larger than gb's 256 bytes"
    # Another platform's region: refused, naming this one's.
    r = post("PCECD-92261ECE", "pce-cd", "0xD0000", os.urandom(4096), io=os.urandom(0x100))
    assert r.status_code == 400 and r.json()["detail"] == "io isn't a part of a pce-cd snapshot (its parts beside ram: wram)"
    r = post("HDR-0152-D1", "dc", "0x8C000000", os.urandom(4096), lwram=os.urandom(16))
    assert r.status_code == 400 and r.json()["detail"] == "lwram isn't a part of a dc snapshot"
    # A part of no known name isn't read: the snapshot is kept without it.
    r = post("RZDJ01", "wii", "0x80000000", os.urandom(4096), mem2=os.urandom(64))
    assert r.status_code == 200 and r.json()["parts"] == ["ram"]
    # Outside main memory, or past it: refused as before.
    assert post("HDR-0152-D1", "dc", "0x8D000000", os.urandom(16)).status_code == 400
    assert post("PCECD-92261ECE", "pce-cd", "0xD0000", os.urandom(0x40001)).status_code == 400
    listing = {g["id"]: g for g in c.get("/api/snapshots", headers=AUTH).json()["games"]}
    assert listing["GS-9037-D1"]["with"]["lwram"] == 1 and listing["GB-29D07340"]["with"]["io"] == 1
    # Samples for the new platforms, at their main memory.
    for game, platform, address in (("RZDJ01", "wii", "0x80400000"), ("HDR-0152-D1", "dc", "0x8C400000"),
                                    ("PCECD-92261ECE", "pce-cd", "0x100100")):
        r = c.post("/api/samples", json={"id": game, "platform": platform,
                                         "samples": [{"ocr": "こんにちは", "bytes_hex": "82b182f182c982bf82cd",
                                                                     "address": address}]},
                   headers=AUTH)
        assert r.status_code == 200, (platform, r.json())


def test_snapshots_of_the_n64_at_its_8_mb_and_the_uncached_mirror(tmp_path):
    """Genkan's N64 core sends n64 snapshots: 8 MB of RAM at 0x80000000, in the console's byte order. One that
    size is kept as sent (bytes in that order, not swapped); one larger, or outside RAM and its uncached mirror at
    0xA0000000, is refused; a sample is taken at either."""
    c = TestClient(create_app("secret", tmp_path / "data"))

    def post(base, ram):
        meta = {"id": "NUS-NAFJ", "platform": "n64", "base_address": base, "ocr": ["こんにちは"]}
        return c.post("/api/snapshots", data={"meta": json.dumps(meta)}, headers=AUTH, files={"ram": ("ram", ram)})
    ram = b"\x80\x37\x12\x40" + os.urandom(4092) + bytes(0x800000 - 4096)       # a word as the console holds it
    r = post("0x80000000", gzip.compress(ram, 1))
    assert r.status_code == 200, r.json()
    kept = tmp_path / "data/snapshots/n64/NUS-NAFJ" / f"{r.json()['sha1']}.ram.gz"
    assert gzip.decompress(kept.read_bytes()) == ram
    assert post("0x80000000", os.urandom(0x800001)).status_code == 400
    assert post("0x80800000", os.urandom(16)).status_code == 400
    for address in ("0x80400000", "0xA0400000"):
        r = c.post("/api/samples", json={"id": "NUS-NAFJ", "platform": "n64",
                                         "samples": [{"ocr": "こんにちは", "bytes_hex": "82b182f182c982bf82cd",
                                                      "address": address}]}, headers=AUTH)
        assert r.status_code == 200, (address, r.json())
    r = c.post("/api/samples", json={"id": "NUS-NAFJ", "platform": "n64",
                                     "samples": [{"ocr": "こんにちは", "bytes_hex": "82b1", "address": "0x90000000"}]},
               headers=AUTH)
    assert r.status_code == 400 and "outside n64 main RAM" in r.json()["detail"]


def test_snapshots_of_the_famicom_and_its_disk_system_with_their_work_ram(tmp_path):
    """fc and fds snapshots: the 2 KB at 0x0000, and the work RAM at 0x6000 as the part `wram` (8 KB on a
    cartridge, up to the Disk System's 32 KB); samples in the 2 KB, its mirrors and the work RAM are taken, others
    refused."""
    c = TestClient(create_app("secret", tmp_path / "data"))

    def post(game, platform, ram, **parts):
        meta = {"id": game, "platform": platform, "base_address": "0x0000", "ocr": ["こんにちは"]}
        files = {"ram": ("ram", ram), **{k: (k, v) for k, v in parts.items()}}
        return c.post("/api/snapshots", data={"meta": json.dumps(meta)}, headers=AUTH, files=files)
    for game, platform, wram in (("FC-12345678", "fc", 0x2000), ("FDS-01ZEL", "fds", 0x8000)):
        r = post(game, platform, os.urandom(0x800), wram=os.urandom(wram), vram=os.urandom(0x800))
        assert r.status_code == 200 and r.json()["parts"] == ["ram", "vram", "wram"], (platform, r.json())
        r = post(game, platform, os.urandom(0x800), wram=os.urandom(wram + 1))
        assert r.status_code == 400 and r.json()["detail"].startswith(f"wram is larger than {platform}'s"), r.json()
        assert post(game, platform, os.urandom(0x801)).status_code == 400

    def sample(game, platform, address):
        return c.post("/api/samples", json={"id": game, "platform": platform, "samples": [
            {"ocr": "こんにちは", "bytes_hex": "82b182f182c982bf82cd", "address": address}]}, headers=AUTH)
    for address in ("0x0300", "0x1300", "0x6100"):
        assert sample("FC-12345678", "fc", address).status_code == 200, address
    assert sample("FDS-01ZEL", "fds", "0xC000").status_code == 200
    assert sample("FC-12345678", "fc", "0xC000").status_code == 400                 # the cartridge's ROM


def test_offline_reads_take_a_snapshots_regions_but_not_its_savestate(tmp_path):
    """yomikata run's regions beside a snapshot: lwram and the like, not the savestate kept with it."""
    from yomikata.run import _regions
    (tmp_path / "s.ram.gz").write_bytes(gzip.compress(bytes(16)))
    (tmp_path / "s.lwram.gz").write_bytes(gzip.compress(b"low"))
    (tmp_path / "s.state.gz").write_bytes(gzip.compress(b"whole machine"))
    assert _regions(tmp_path / "s.ram.gz") == {"lwram": b"low"}
