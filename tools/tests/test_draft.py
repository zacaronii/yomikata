import json

from yomikata.draft import draft, summary


def disc(path, id_, sjis, enc="unknown"):
    return {"path": path, "image": path.rsplit("/", 1)[-1], "platform": "ps1", "id": id_, "exe": "X", "exe_sha1": "a" * 40,
            "chars": {"shift_jis": sjis, "utf16le": 0, "utf8": 0}, "encoding": enc}


def test_discs_in_a_folder_become_one_entry_and_existing_ones_are_kept(tmp_path):
    (tmp_path / "games/ps1").mkdir(parents=True)
    (tmp_path / "games/ps1/SLPS-00003.json").write_text('{"id": "SLPS-00003", "keep": true}')
    rows = draft([disc("Game (Japan)/Game (Japan) (Disc 1).chd", "SLPS-00001", 5000),
                  disc("Game (Japan)/Game (Japan) (Disc 2).chd", "SLPS-00002", 100),
                  disc("Other (Japan).chd", "SLPS-00003", 10)], tmp_path, {"Game (Japan)": {"title": "ゲーム"}})
    e = json.loads((tmp_path / "games/ps1/SLPS-00001.json").read_text())
    assert e["aliases"] == ["SLPS-00002"] and e["encoding"] == "shift_jis" and e["title"] == "ゲーム"
    assert [v["id"] for v in e["versions"]] == ["SLPS-00001", "SLPS-00002"] and e["status"] == "auto"
    assert json.loads((tmp_path / "games/ps1/SLPS-00003.json").read_text()) == {"id": "SLPS-00003", "keep": True}
    assert "kept existing" in summary(rows) and "| SLPS-00001 | ゲーム | 2 | shift_jis |" in summary(rows)
