import gzip
import json
import os

from fastapi.testclient import TestClient

from yomikata import inbox as inbox_mod
from yomikata.inbox import create_app

AUTH = {"authorization": "Bearer secret"}


def meta(**kw):
    m = {"id": "SLPS-01001", "platform": "ps1", "base_address": "0x80000000",
         "ocr": ["あんなのだったらもっと早く帰ってくればよかった"], "genkan_version": "0.1"}
    m.update(kw)
    return m


def send(c, ram, m=None, headers=AUTH):
    return c.post("/api/snapshots", data={"meta": json.dumps(m or meta(), ensure_ascii=False)},
                  files={"ram": ("ram.bin", ram, "application/octet-stream")}, headers=headers)


def test_snapshots_are_stored_compressed_and_deduplicated(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    ram = os.urandom(1024) + bytes(0x200000 - 1024)
    assert send(c, ram, headers={}).status_code == 401
    first = send(c, ram).json()
    assert first["added"] and first["stored"] == 1
    assert send(c, gzip.compress(ram)).json()["added"] is False          # the same RAM, sent gzipped
    folder = tmp_path / "data/snapshots/ps1/SLPS-01001"
    assert gzip.decompress((folder / f"{first['sha1']}.ram.gz").read_bytes()) == ram
    stored = json.loads((folder / f"{first['sha1']}.json").read_text())
    assert stored["size"] == 0x200000 and stored["ocr"] == meta()["ocr"]
    assert c.get("/api/snapshots", headers=AUTH).json()["games"][0]["snapshots"] == 1


def test_bad_snapshots_get_the_reason(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    small = os.urandom(4096)
    for m, ram, says in ((meta(base_address="0x1FC00000"), small, "base_address"),       # the BIOS ROM
                         (meta(), os.urandom(0x200001), "main RAM"),                       # bigger than PS1 RAM
                         (meta(base_address="0x801FF000"), small + small, "runs past"),
                         (meta(ocr=[]), small, "meta.ocr"), (meta(platform="md"), small, "snapshots are taken"),
                         (meta(no_text=True), small, "no_text"), (meta(ocr=[], no_text="yes"), small, "meta.ocr")):
        r = send(c, ram, m)
        assert r.status_code == 400 and says in r.json()["detail"], (m, r.text)
    r = c.post("/api/snapshots", data={"meta": json.dumps(meta())}, headers=AUTH)
    assert r.status_code == 400 and "ram file part" in r.json()["detail"]


def test_a_screen_without_text_is_stored_when_it_says_so(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    with_text, without = os.urandom(4096), os.urandom(4096)
    send(c, with_text)
    for m in (meta(ocr=[], no_text=True), {k: v for k, v in meta(no_text=True).items() if k != "ocr"}):
        r = send(c, without, m).json()
        assert r["ok"], r
    stored = json.loads((tmp_path / f"data/snapshots/ps1/SLPS-01001/{r['sha1']}.json").read_text())
    assert stored["ocr"] == [] and stored["no_text"] is True
    game = c.get("/api/snapshots", headers=AUTH).json()["games"][0]
    assert (game["snapshots"], game["lines"], game["no_text"]) == (2, 1, 1)


def test_only_the_newest_snapshots_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox_mod, "SNAPSHOTS_PER_GAME", 2)
    c = TestClient(create_app("secret", tmp_path / "data"))
    for i in range(3):
        send(c, os.urandom(2048))
    folder = tmp_path / "data/snapshots/ps1/SLPS-01001"
    assert len(list(folder.glob("*.json"))) == 2 and len(list(folder.glob("*.ram.gz"))) == 2


def test_super_famicom_work_ram(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    m = meta(id="SHVC-ZQ", platform="sfc", base_address="0x7E0000", ocr=["つくりもののぎょう"])
    ram = os.urandom(512) + bytes(0x20000 - 512)
    assert send(c, ram, m).json()["added"]
    assert (tmp_path / "data/snapshots/sfc/SHVC-ZQ").is_dir()
    assert send(c, ram + b"\0", m).status_code == 400                             # more than the 128 KB there is
    assert send(c, gzip.compress(bytes(0x200000)), m).status_code == 400            # also when it only unpacks to more
    assert send(c, ram[:0x10000], dict(m, base_address="0x7F8000")).status_code == 400     # runs past the end
    assert send(c, ram, dict(m, base_address="0x000000")).status_code == 400       # a low mirror isn't taken
    assert send(c, ram, dict(m, id="SFC-1A2B3C4D")).json()["added"]


CONSOLES = [("gba", "0x02000000", 0x40000, 0x18000), ("gb", "0xC000", 0x2000, 0x4000), ("gbc", "0xC000", 0x2000, 0x4000),
            ("nds", "0x02000000", 0x400000, 0xA4000), ("sfc", "0x7E0000", 0x20000, 0x10000)]


def test_each_console_has_its_range_and_caps(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    for platform, base, size, vram in CONSOLES:
        m = meta(id="ZQ-0001", platform=platform, base_address=base, vram_format=f"{platform}-raw")
        ram = os.urandom(64) + bytes(size - 64)
        video = {"vram": ("vram.bin", gzip.compress(bytes(vram)), "application/octet-stream")}
        r = c.post("/api/snapshots", data={"meta": json.dumps(m)}, headers=AUTH,
                   files={"ram": ("ram.bin", gzip.compress(ram), "application/octet-stream"), **video})
        assert r.json()["parts"] == ["ram", "vram"], platform
        stored = json.loads((tmp_path / f"data/snapshots/{platform}/ZQ-0001/{r.json()['sha1']}.json").read_text())
        assert stored["vram_format"] == f"{platform}-raw"
        assert send(c, ram + b"\0", m).status_code == 400, platform                 # more RAM than there is
        assert send(c, ram, dict(m, base_address=hex(int(base, 16) + 0x1000))).status_code == 400, platform
        assert send(c, ram, dict(m, base_address="0x10000000")).status_code == 400, platform
        big = {"vram": ("vram.bin", gzip.compress(bytes(vram + 1)), "application/octet-stream")}
        r = c.post("/api/snapshots", data={"meta": json.dumps(m)}, headers=AUTH,
                   files={"ram": ("ram.bin", os.urandom(64), "application/octet-stream"), **big})
        assert r.status_code == 400 and "vram" in r.json()["detail"], platform
    assert send(c, bytes(64), meta(vram_format="Not A Name")).status_code == 400


def test_a_nothing_on_screen_snapshot_of_a_cartridge(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    m = meta(id="SHVC-ZQ", platform="sfc", base_address="0x7E0000", ocr=[], no_text=True)
    assert send(c, os.urandom(0x20000), m).json()["added"]


def gamecube(game_id=b"GXXJ01", version=2, magic="C2339F3D") -> bytes:
    """24 MB as a GameCube game has it: the disc's header at its start, made up."""
    head = game_id + bytes([0, version]) + bytes(0x14) + bytes.fromhex(magic)
    return head + os.urandom(64) + bytes(0x1800000 - len(head) - 64)


def test_gamecube_main_memory(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    m = meta(id="GXXJ01", platform="ngc", base_address="0x80000000")       # as Genkan names the console
    ram = gamecube()
    r = send(c, gzip.compress(ram), m).json()
    assert r["added"] and r["parts"] == ["ram"]
    stored = json.loads((tmp_path / f"data/snapshots/gc/GXXJ01/{r['sha1']}.json").read_text())
    assert (stored["platform"], stored["size"], stored["disc_version"], stored["disc_number"]) == ("gc", 0x1800000, 2, 0)
    assert send(c, ram, dict(m, platform="gc")).json()["added"] is False    # the same memory under the catalog's name
    listing = c.get("/api/snapshots", headers=AUTH).json()["games"]
    assert [(g["id"], g["platform"]) for g in listing] == [("GXXJ01", "gc")]

    def reason(ram, **kw):
        r = send(c, ram, dict(m, **kw))
        assert r.status_code == 400
        return r.json()["detail"]
    assert "24 MB" in reason(ram + b"\0")
    assert "past main RAM" in reason(ram[:0x1000], base_address="0x817FF800")
    assert "base_address" in reason(ram, base_address="0x90000000")
    assert "byte order" in reason(gamecube(magic="3D9F33C2"))                # each word turned round
    assert "disc header" in reason(bytes(0x1800000))
    assert "GXXJ01" in reason(gamecube(b"GYYJ01"))
    # A stretch that doesn't start at the start has no header to show.
    assert send(c, os.urandom(0x1000), dict(m, base_address="0x80400000")).json()["added"]


def test_gamecube_samples(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    def post(address):
        return c.post("/api/samples", headers=AUTH, json={"id": "GXXJ01", "platform": "ngc", "samples": [
            {"ocr": "あいうえお", "bytes_hex": "82a082a282a482a682a8", "address": address}]})
    assert post("0x80550384").json()["added"] == 1
    assert (tmp_path / "data/samples/gc/GXXJ01.json").is_file()
    assert post("0x90000000").status_code == 400


def test_gamecube_savestate_cap(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    def post(m, ram, state):
        return c.post("/api/snapshots", data={"meta": json.dumps(m)}, headers=AUTH,
                      files={"ram": ("ram.bin", gzip.compress(ram), "application/octet-stream"),
                             "state": ("state.bin", gzip.compress(bytes(state), 1), "application/octet-stream")})
    m = meta(id="GXXJ01", platform="ngc", base_address="0x80000000")
    r = post(m, gamecube(), 93 * 1024 * 1024)                # as large as Dolphin's
    assert r.json()["parts"] == ["ram", "state"]
    r = post(m, gamecube(version=1), 128 * 1024 * 1024 + 1)
    assert r.status_code == 400 and "state is larger than 128 MB" in r.json()["detail"]
    # Elsewhere the cap is what it was.
    r = post(meta(), os.urandom(64), 64 * 1024 * 1024 + 1)
    assert r.status_code == 400 and "state is larger than 64 MB" in r.json()["detail"]


def test_a_screen_the_reader_could_not_read(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    r = send(c, os.urandom(64), meta(ocr=[], ocr_unavailable=True))
    assert r.status_code == 200 and r.json()["added"]
    stored = json.loads((tmp_path / f"data/snapshots/ps1/SLPS-01001/{r.json()['sha1']}.json").read_text())
    assert stored["ocr"] == [] and stored["ocr_unavailable"] is True and "no_text" not in stored
    assert c.get("/api/snapshots", headers=AUTH).json()["games"][0]["ocr_unavailable"] == 1
    assert send(c, os.urandom(64), meta(ocr=["あ"], ocr_unavailable=True)).status_code == 400
    assert send(c, os.urandom(64), meta(ocr=[], ocr_unavailable=True, no_text=True)).status_code == 400
    assert send(c, os.urandom(64), meta(ocr=[], ocr_unavailable=False)).status_code == 400
