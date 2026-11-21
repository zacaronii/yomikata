import json
import time

from fastapi.testclient import TestClient

from test_tools import make_ps1
from yomikata.inbox import create_app

AUTH = {"authorization": "Bearer secret"}


def body(n=2, **kw):
    return {"id": "SLPS-01234", "platform": "ps1", "title": "テスト", "genkan_version": "1.0",
            "samples": [{"ocr": f"ている{i}", "address": f"0x801A2B{i:02X}", "bytes_hex": "0102ff" * (i + 1),
                         "unit": 2, "byte_order": "little", "probe": "repeat"} for i in range(n)], **kw}


def test_uploads_need_the_token_and_valid_samples(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    assert c.get("/health").json() == {"ok": True}
    assert c.post("/api/samples", json=body()).status_code == 401
    assert c.post("/api/samples", json=body(), headers={"authorization": "Bearer nope"}).status_code == 401
    for bad, says in (({"id": "slps 1"}, "id"), ({"platform": "pc"}, "platform"), ({"samples": []}, "samples"),
                      ({"samples": [{"ocr": "x", "bytes_hex": "zz"}]}, "bytes_hex"),
                      ({"samples": [{"ocr": "", "bytes_hex": "00"}]}, "ocr")):
        r = c.post("/api/samples", json={**body(), **bad}, headers=AUTH)
        assert r.status_code == 400 and says in r.json()["detail"], (bad, r.text)
    assert c.post("/api/samples", content=b"nope", headers=AUTH).status_code == 400


def test_uploads_merge_without_duplicates_and_stay_out_of_any_repo(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    assert c.post("/api/samples", json=body(2), headers=AUTH).json() == {"ok": True, "id": "SLPS-01234", "added": 2, "stored": 2}
    assert c.post("/api/samples", json=body(3), headers=AUTH).json()["added"] == 1    # the same file sent again, grown
    stored = json.loads((tmp_path / "data/samples/ps1/SLPS-01234.json").read_text())
    assert len(stored["samples"]) == 3 and stored["samples"][0]["probe"] == "repeat" and len(stored["uploads"]) == 2
    games = c.get("/api/samples", headers=AUTH).json()["games"]
    assert games == [{"id": "SLPS-01234", "platform": "ps1", "title": "テスト", "samples": 3, "exe_sha1": None,
                      "last_upload": stored["uploads"][-1]["at"]}]


def test_the_exe_hash_is_filled_from_the_library(tmp_path):
    lib = tmp_path / "roms"
    (lib / "psx").mkdir(parents=True)
    make_ps1(lib / "psx" / "Game (Japan).iso", raw=False)
    c = TestClient(create_app("secret", tmp_path / "data", library_root=lib))
    c.post("/api/samples", json=body(1), headers=AUTH)
    for _ in range(200):                                 # identified in the background
        game = c.get("/api/samples", headers=AUTH).json()["games"][0]
        if game["exe_sha1"]:
            break
        time.sleep(0.05)
    assert game["exe_sha1"] and len(game["exe_sha1"]) == 40
    cache = json.loads((tmp_path / "data/library.json").read_text())
    assert cache["psx/Game (Japan).iso"]["id"] == "SLPS-01234"


def test_samples_outside_main_ram_are_refused(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    bad = body(1)
    bad["samples"][0]["address"] = "0x1FC5EF8E"                  # PS1 BIOS ROM: a coincidental match
    r = c.post("/api/samples", json=bad, headers=AUTH)
    assert r.status_code == 400 and "outside ps1 main RAM" in r.json()["detail"]
    ok = body(1)
    ok["samples"][0]["address"] = "0x801A2B40"
    assert c.post("/api/samples", json=ok, headers=AUTH).status_code == 200
