"""`compare` on made-up snapshots: a script and a layout reading the same memory."""

import gzip
import json
import os

import pytest

from yomikata import reader as rd
from yomikata.cli import main
from yomikata.compare import compare

try:
    rd.library()
except rd.ReaderUnavailable as e:        # pragma: no cover
    if os.environ.get("YOMIKATA_REQUIRE_READER"):
        raise
    pytest.skip(str(e), allow_module_level=True)

ENTRY = {"id": "ZQ-0001", "platform": "ps1", "encoding": "shift_jis", "layouts": [{
    "message": {"address": "0x80000100", "charset": "shift_jis", "max_bytes": 64, "terminator": "00"},
    "speaker": {"address": "0x80000080", "charset": "shift_jis", "max_bytes": 16, "terminator": "00"},
    "choices": {"address": "0x80000200", "charset": "shift_jis", "max_bytes": 16, "terminator": "00",
                "rows": {"count": 2, "stride": 16}}}]}
SCRIPT = """-- reader: no hooks
reader.on_frame(function()
    local line = text.sjis(mem.cstr(0x80000100, 64))
    if line ~= "" and reader.changed("line", line) then
        reader.emit{ text = %s, speaker = text.sjis(mem.cstr(0x80000080, 16)) }
    end
    local answers = {}
    for row = 0, 1 do
        local answer = text.sjis(mem.cstr(0x80000200 + 16 * row, 16))
        if answer ~= "" then answers[#answers + 1] = answer end
    end
    if #answers > 0 and reader.changed("answers", table.concat(answers, "\\n")) then
        reader.emit{ text = table.concat(answers, "\\n"), channel = "choice" }
    end
end)
"""


def snapshot(folder, name, message="", speaker="", answers=()):
    ram = bytearray(0x400)
    for at, value in ((0x100, message), (0x80, speaker), *((0x200 + 16 * i, a) for i, a in enumerate(answers))):
        raw = value.encode("cp932")
        ram[at:at + len(raw)] = raw
    (folder / f"{name}.ram.gz").write_bytes(gzip.compress(bytes(ram)))
    (folder / f"{name}.json").write_text(json.dumps({"base_address": "0x80000000", "ocr": [], "ocr_unavailable": True}))
    return folder / f"{name}.ram.gz"


def catalog(tmp_path, body):
    folder = tmp_path / "games/ps1"
    folder.mkdir(parents=True)
    (folder / "ZQ-0001.json").write_text(json.dumps(ENTRY), encoding="utf-8")
    (folder / "ZQ-0001.lua").write_text(body, encoding="utf-8")
    return folder / "ZQ-0001.lua", folder / "ZQ-0001.json"


def test_a_script_that_reads_what_the_layout_reads(tmp_path, capsys):
    script, entry = catalog(tmp_path, SCRIPT % "line")
    snaps = [snapshot(tmp_path, "a", "つくりもののぎょう", "はかせ"), snapshot(tmp_path, "b"),
             snapshot(tmp_path, "c", "どうする", "", ("はい", "いいえ"))]
    result = compare(script, ENTRY, snaps, tmp_path)
    assert result.counts() == {"snapshots": 3, "the same": 2, "nothing in either": 1, "different": 0,
                               "the script didn't load": 0}
    assert result.agrees
    assert main(["compare", str(script), str(entry), *map(str, snaps)]) == 0
    assert "2  the same" in capsys.readouterr().out


def test_a_script_that_reads_something_else(tmp_path, capsys):
    script, entry = catalog(tmp_path, SCRIPT % 'line .. "！"')
    snaps = [snapshot(tmp_path, "a", "つくりもののぎょう", "はかせ"), snapshot(tmp_path, "b")]
    result = compare(script, ENTRY, snaps, tmp_path)
    assert result.counts()["different"] == 1 and result.rows[0].differing() == ["message"] and not result.agrees
    assert main(["compare", str(script), str(entry), *map(str, snaps)]) == 1
    captured = capsys.readouterr()
    assert "a: message" in captured.err and "つくりもの" not in captured.err + captured.out      # counts only
    assert main(["compare", "--show", str(script), str(entry), *map(str, snaps)]) == 1
    assert "つくりもののぎょう！" in capsys.readouterr().err


def test_a_script_for_another_build_counts_as_not_loaded(tmp_path):
    script, _ = catalog(tmp_path, "error('this script is for another build of the game', 0)")
    result = compare(script, ENTRY, [snapshot(tmp_path, "a", "つくりもののぎょう")], tmp_path)
    assert result.counts()["the script didn't load"] == 1 and not result.agrees


def test_what_the_layout_has_not_got_is_not_compared(tmp_path):
    entry = {**ENTRY, "layouts": [{"message": ENTRY["layouts"][0]["message"]}]}
    script, _ = catalog(tmp_path, SCRIPT % "line")
    result = compare(script, entry, [snapshot(tmp_path, "a", "つくりもののぎょう", "はかせ", ("はい",))], tmp_path)
    assert result.agrees and list(result.rows[0].layout) == ["message"]
