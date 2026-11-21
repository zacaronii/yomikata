import os

from yomikata.locate import GOJUON, locate, windows


def encode(text: str, table: dict, width=2, order="little") -> bytes:
    return b"".join(table[c].to_bytes(width, order) for c in text)


def test_finds_a_line_in_ram_by_its_repeats_and_reads_the_gojuon_offset():
    table = {c: 0x50 + i for i, c in enumerate(GOJUON)}
    table.update({"早": 0x104, "帰": 0x105, "っ": 0x9F, "だ": 0x88, "ば": 0x8D})
    line = "もっと早く帰ってくればよかった"
    ram = bytearray(os.urandom(300_000))
    at = 123_457                                                      # odd: alignment is searched too
    ram[at:at + 2 * len(line)] = encode(line, table, order="big")
    (best, *_) = locate([(bytes(ram), ["o." + line + "▼"])])
    assert best.encoding == "u16be" and not best.conflicts
    assert best.mapping[0x72] == "も" and best.mapping[0x104] == "早"
    assert max(best.gojuon_offsets()["hiragana"], key=best.gojuon_offsets()["hiragana"].get) == 0x50
    assert any(h["offset"] == at for h in best.hits)


def test_windows_need_enough_repeats():
    assert windows("あいうえおかきく") == []                              # no repeats: proves nothing
    assert windows("もっと早く帰ってくればよかった")[0][0].count("っ") >= 2


def test_the_pattern_search_agrees_with_checking_every_pair_everywhere():
    import numpy as np
    from yomikata.locate import isomorph
    rng = np.random.default_rng(7)
    a = rng.integers(0, 4, 20_000).astype("<u2")                      # few codes, so patterns match often
    for seg in ("あいあい", "あいうあ", "ああいい", "あいう"):
        slow = [p for p in range(len(a) - len(seg)) if a[p] != 0 and all(
            (a[p + i] == a[p + j]) == (seg[i] == seg[j]) for i in range(len(seg)) for j in range(i + 1, len(seg)))]
        assert isomorph(a, seg).tolist() == slow and slow


def test_a_standard_encoding_is_found_as_it_is_across_a_row_break():
    from yomikata.locate import find_standard
    rows = ["窓の外は雪だった、", "列車はまだ来ない。"]
    ram = bytearray(200_000)
    ram[1000:1000 + 18] = rows[0].encode("utf-16-le")                   # two rows of a message buffer
    ram[1108:1108 + 18] = rows[1].encode("utf-16-le")
    ram[50_001:50_001 + 18] = rows[1].encode("cp932")                   # Shift-JIS may stand at an odd address
    ram[70_001:70_001 + 18] = rows[0].encode("utf-16-be")               # UTF-16 at an odd address is ignored
    for at in range(100_000, 100_000 + 40 * 64, 64):                    # a piece that is everywhere says nothing
        ram[at:at + 8] = "列車はま".encode("utf-16-le")
    found = find_standard([(bytes(ram), ["".join(rows)])])
    assert list(found) == ["utf16le", "shift_jis"]
    assert [(h["offset"], h["segment"]) for h in found["utf16le"]] == [(1000, rows[0]), (1110, rows[1][1:])]
    assert [(h["offset"], h["segment"]) for h in found["shift_jis"]] == [(50_001, rows[1])]
