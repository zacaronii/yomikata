"""`yomikata regress`: a merged entry's bar screens kept apart, read again with the catalog's script, against a
baseline; what falls is reported by name and number, never by its text."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from yomikata import snapfile
from yomikata.reader import ReaderUnavailable, library_path

try:
    READER = library_path() is not None
except ReaderUnavailable:
    READER = False
needs_reader = pytest.mark.skipif(not READER, reason="the reader library isn't built")

GAME = "gc/Made Up (Japan).iso"
SCRIPT = """-- reader: title つくりもの (Made Up)
-- reader: no hooks
reader.on_frame(function()
    local bytes = mem.cstr(0x80000100 + OFFSET, 32)
    if bytes and #bytes > 0 and reader.changed("line", bytes) then
        reader.emit{ text = text.sjis(bytes) }
    end
end)
"""


def _snap(snaps: Path, name: str, line: str, ocr: list[str] | None):
    memory = bytearray(0x1000)
    raw = line.encode("shift_jis")
    memory[0x100:0x100 + len(raw)] = raw
    snapfile.write(snaps / f"{name}.ram.gz", bytes(memory))
    (snaps / f"{name}.json").write_text(json.dumps({"platform": "gc", "game": GAME, "base_address": "0x80000000",
                                                    "frame": 1, "ocr": ocr or []}, ensure_ascii=False),
                                        encoding="utf-8")


@pytest.fixture
def world(tmp_path):
    catalog, scratch, games = tmp_path / "catalog", tmp_path / "scratch", tmp_path / "games"
    (catalog / "games" / "gc").mkdir(parents=True)
    (scratch / "snaps").mkdir(parents=True)
    (games / "gc").mkdir(parents=True)
    (games / GAME).write_bytes(b"")
    (catalog / "games" / "gc" / "GXXJ01.lua").write_text(SCRIPT.replace("OFFSET", "0"), encoding="utf-8")
    _snap(scratch / "snaps", "bar-0", "こんにちは", ["こんにちは"])
    _snap(scratch / "snaps", "bar-1", "さようなら", ["さようなら"])
    _snap(scratch / "snaps", "loose", "またあした", None)            # no rows read off it: not a bar screen
    identify = lambda path: SimpleNamespace(platform="gc", id="GXXJ01")   # noqa: E731
    return SimpleNamespace(catalog=catalog, scratch=scratch, games=games, identify=identify)


@needs_reader
def test_a_bar_is_kept_apart_read_again_and_a_fall_reported_by_name_only(world):
    from yomikata.regress import markdown, regress, summary
    run = lambda **k: regress(world.scratch, world.games, world.catalog, identify=world.identify, day="d", **k)  # noqa: E731
    first = run()
    kept = world.scratch / "regress" / "keep" / "GXXJ01"
    assert first.kept == 2 and sorted(p.name for p in kept.glob("*.json")) == ["bar-0.json", "bar-1.json"]
    assert not first.failed and len(first.new) == 2 and all(r.agreement == 1.0 for r in first.rows)
    # A snap written later under the same name, in the harness's folder, doesn't touch the kept one.
    _snap(world.scratch / "snaps", "bar-0", "またあした", ["またあした"])
    again = run()
    assert again.kept == 0 and not again.failed and not again.changed
    # A script that reads elsewhere: both screens fall, the exit is a failure, and nothing of the text is told.
    (world.catalog / "games" / "gc" / "GXXJ01.lua").write_text(SCRIPT.replace("OFFSET", "0x200"), encoding="utf-8")
    fallen = run()
    assert fallen.failed and sorted(r.snap for r, _ in fallen.fell) == ["bar-0", "bar-1"]
    told = markdown(fallen, "d") + summary(fallen) + (world.scratch / "regress" / "d.md").read_text(encoding="utf-8")
    assert "GXXJ01 bar-0: 1.0 -> 0.00" in told and "2 bar screens fell of 2" in told
    assert not any(line in told for line in ("こんにちは", "さようなら", "またあした"))
    baseline = json.loads((world.scratch / "regress" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["GXXJ01/bar-0"]["agreement"] == 1.0                  # a fall stays out of the baseline
    assert run().failed                                                 # and is reported again the next night
    accepted = run(accept=("GXXJ01",))
    assert not accepted.failed
    assert not run().failed                                             # accepted: the baseline is the new reading


def test_only_games_with_a_merged_script_are_kept(world):
    from yomikata.regress import keep
    (world.catalog / "games" / "gc" / "GXXJ01.lua").unlink()
    copied, skipped = keep(world.scratch, world.games, world.catalog, world.identify)
    assert copied == 0 and skipped == {GAME: "no merged script"}
    assert not (world.scratch / "regress" / "keep" / "GXXJ01").exists()
