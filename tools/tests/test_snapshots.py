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
                         (meta(ocr=[]), small, "meta.ocr"), (meta(platform="nds"), small, "snapshots are taken"),
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
