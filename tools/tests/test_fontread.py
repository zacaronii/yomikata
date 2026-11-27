"""`yomikata font-read`: a made-up font read by a made-up second reader, picks, corrections, sheets and the table."""

import json

import numpy as np

from yomikata import fontread
from yomikata.cli import main


def made_up_font(count: int = 6) -> bytes:
    """`count` glyphs of 16x16, a bit a pixel in rows of two bytes (rom-font's rows1): glyph n has n + 1 rows inked,
    and the last is blank."""
    out = bytearray()
    for n in range(count):
        g = np.zeros((16, 16), np.uint8)
        if n < count - 1:
            g[2:3 + n, 3:12] = 1
        out += np.packbits(g, axis=1).tobytes()
    return bytes(out)


def test_a_pick_from_what_the_second_reader_says_of_a_glyph_alone():
    assert fontread.pick("初めて") == "初"           # a kanji and the kana that would follow it
    assert fontread.pick("あ") == "あ"
    assert fontread.pick("．．．") == ""               # more than one character, none a kanji: no pick
    assert fontread.pick("") == ""
    assert fontread.corrections("1A0=ー\n# a note\n1A1=   # an icon\n18F=\u3000\n") == {0x1A0: "ー", 0x1A1: "", 0x18F: "\u3000"}


def test_resolved_puts_rows_over_the_reader_and_corrections_over_both():
    readings = {0x90: "Ｏ", 0x91: "初めて", 0x92: "．．", 0x93: "外", 0x94: "外"}
    table = fontread.resolved(readings, rows={0x90: "０"}, fixes={0x92: "。", 0x94: ""})
    assert table == {0x90: "０", 0x91: "初", 0x92: "。", 0x93: "外"}
    assert fontread.doubles({1: "人", 2: "人", 3: "外"}) == {"人": [1, 2]}


def test_the_command_reads_draws_and_writes_the_table(tmp_path, capsys, monkeypatch):
    font = tmp_path / "font.bin"
    font.write_bytes(made_up_font())
    said = iter(["一", "二です", "三", "．．．", "三"])          # what the made-up second reader takes each glyph for
    monkeypatch.setattr("yomikata.glyphread.second_reader", lambda: lambda picture: next(said))
    fix = tmp_path / "fix.txt"
    fix.write_text("93=四\n", encoding="utf-8")
    args = ["font-read", str(font), "--at", "0", "--first", "90", "--count", "6", "--form", "rows1",
            "--ocr", str(tmp_path / "ocr.tsv"), "--fix", str(fix), "--pairs", str(tmp_path / "pairs"),
            "--check", str(tmp_path / "check"), "--tbl", str(tmp_path / "out.tbl")]
    assert main(args) == 0
    counts = json.loads(capsys.readouterr().out)
    assert counts == {"glyphs": 6, "inked": 5, "read": 5, "by_the_second_reader": 5, "rows": 0, "corrections": 1,
                      "characters_given_twice": 1, "not_read": 0}
    # The pick from each reading, the correction over the reader's "．．．", and 三 given to two glyphs.
    assert (tmp_path / "out.tbl").read_text(encoding="utf-8") == "9000=一\n9100=二\n9200=三\n9300=四\n9400=三\n"
    assert (tmp_path / "pairs/pairs-00.png").is_file() and (tmp_path / "check/corrections-00.png").is_file()
    assert (tmp_path / "check/twice-00.png").is_file()
    # Read again from the readings kept, without the second reader.
    assert main(args[:10] + ["--readings", str(tmp_path / "ocr.tsv"), "--fix", str(fix)]) == 0
