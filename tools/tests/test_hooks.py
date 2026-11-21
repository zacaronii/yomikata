import json
from pathlib import Path

from fastapi.testclient import TestClient

from yomikata.hooks import merge, summary
from yomikata.inbox import create_app

AUTH = {"authorization": "Bearer secret"}


def report(**kw):
    r = {"id": "NPJH50372", "platform": "psp", "disc_version": "1.03", "title": "ダンガンロンパ",
         "hook": {"address": "0x08812abc", "register": "a1", "charset": "utf16le"},
         "evidence": {"lines_checked": 8, "lines_matched": 8, "pairs": [{"ocr": "そんな学園", "hook": "そんな学園"}]},
         "reporter": {"app": "Genkan", "version": "0.9"}, "emulator": {"name": "PPSSPP", "version": "1.18"}}
    r.update(kw)
    return r


def test_hook_reports_are_checked_and_stored(tmp_path):
    c = TestClient(create_app("secret", tmp_path / "data"))
    assert c.post("/api/hooks", json=report()).status_code == 401
    for bad, says in (({"hook": {"address": "88540B0", "register": "a0", "charset": "utf16le"}}, "address"),
                      ({"hook": {"address": "0x1", "register": "a0", "charset": "ebcdic"}}, "charset"),
                      ({"evidence": {"lines_checked": 2, "lines_matched": 5}}, "matched <= checked"),
                      ({"platform": "pc"}, "platform")):
        r = c.post("/api/hooks", json=report(**bad), headers=AUTH)
        assert r.status_code == 400 and says in r.json()["detail"], (bad, r.text)
    assert c.post("/api/hooks", json=report(), headers=AUTH).json()["reports"] == 1
    assert c.post("/api/hooks", json=report(), headers=AUTH).json() == {"ok": True, "id": "NPJH50372", "replaced": True,
                                                                         "reports": 1}
    games = c.get("/api/hooks", headers=AUTH).json()["games"]
    assert games[0]["reports"][0]["address"] == "0x08812ABC" and games[0]["reports"][0]["matched"] == "8/8"


def write_reports(folder: Path, *reports):
    for r in reports:
        p = folder / r["platform"] / f"{r['id']}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        stored = json.loads(p.read_text()) if p.exists() else {"id": r["id"], "platform": r["platform"], "reports": []}
        stored["reports"].append(r)
        p.write_text(json.dumps(stored, ensure_ascii=False))


def test_reports_become_entries_only_with_enough_evidence(tmp_path):
    root, reports = tmp_path / "catalog", tmp_path / "reports"
    (root / "games/psp").mkdir(parents=True)
    (root / "games/psp/ULJM05338.json").write_text(json.dumps({
        "id": "ULJM05338", "aliases": ["ULJM05339"], "platform": "psp", "title": "CLANNAD", "encoding": "utf16le",
        "hooks": [{"address": "0x0880F240", "register": "a0", "charset": "utf16le"}], "status": "partial",
        "sources": ["LunaHook"]}, ensure_ascii=False))
    write_reports(reports,
                  {**report(), "exe_sha1": "e" * 40},
                  report(id="ULJM05339", hook={"address": "0x0880F240", "register": "a0", "charset": "utf16le"},
                         exe_sha1="c" * 40, disc_version="1.00"),
                  report(id="ULJM05887", evidence={"lines_checked": 3, "lines_matched": 3, "pairs": []}))
    rows = merge(root, reports)
    by = {r["id"]: r["result"] for r in rows}
    assert by == {"NPJH50372": "new entry", "ULJM05339": "hook confirmed on another version",
                  "ULJM05887": "skipped: not enough matching evidence"}
    new = json.loads((root / "games/psp/NPJH50372.json").read_text())
    assert new["encoding"] == "utf16le" and new["hooks"][0]["versions"] == ["e" * 40] and new["status"] == "auto"
    assert new["versions"] == [{"exe_sha1": "e" * 40, "note": "disc version 1.03"}]
    assert "8/8 lines matched OCR" in new["sources"][0] and "pairs" not in json.dumps(new)
    clannad = json.loads((root / "games/psp/ULJM05338.json").read_text())
    assert clannad["hooks"][0]["versions"] == ["c" * 40] and len(clannad["sources"]) == 2
    assert "そんな" not in summary(rows)                       # summaries count, never quote
