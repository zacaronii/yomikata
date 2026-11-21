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


def window(ram: bytearray, at: int, rows: list[str], stride: int, codec: str):
    for r in range(4):
        ram[at + r * stride:at + (r + 1) * stride] = (rows[r] if r < len(rows) else "").encode(codec).ljust(stride, b"\0")


def test_a_message_block_is_proposed_for_the_buffer_that_changes_in_place():
    from yomikata.locate import find_standard, propose
    lines = [["きのうの夜はずっと雨が", "降っていたらしいね。"], ["それで校庭があんなに", "ぬかるんでいるのか。"],
             ["まあ、放課後までには", "乾くんじゃないかな。"]]
    buffer, script, snaps = 0x4264, 0x9000, []
    for n, rows in enumerate(lines):
        ram = bytearray(0x10000)
        ram[buffer - 2:buffer] = b"\x1e\x00"                                   # a field right before the buffer
        window(ram, buffer, rows, 108, "utf-16-le")
        ram[buffer + 4 * 108:buffer + 4 * 108 + 4] = b"\x07\x07\x07\x07"         # other data after the fourth row
        for m, old in enumerate(lines[:n + 1]):                                 # the script so far: lines stay put
            ram[script + m * 60:script + m * 60 + 60] = "".join(old).encode("utf-16-le").ljust(60, b"\0")
        snaps.append((bytes(ram), ["".join(rows) if n else "X" + rows[0][1:] + rows[1]]))    # one misread first character
    snaps.append((bytes(bytearray(0x10000)), []))                               # nothing on screen
    half = bytearray(snaps[2][0])
    snaps.append((bytes(half), [lines[2][0][:6]]))                              # the last line again, still typing
    (charset, hits), *_ = find_standard(snaps).items()
    got = propose(snaps, hits, charset, base=0x08000000)
    assert got["message"] == {"address": "0x08004264", "charset": "utf16le", "max_bytes": 432, "terminator": "0000",
                              "rows": {"count": 4, "stride": 108}}
    assert got["rows_seen"] == 2 and got["agreement"][1:] == [1.0, 1.0, 1.0, 1.0] and got["agreement"][0] > 0.9
    assert got["longer"][4] == 14 and got["longer"][:4] == [1, 0, 0, 0]
    assert propose(snaps[:1], [h for h in hits if h["snapshot"] == 0], charset) is None      # one line: can't tell


def test_a_single_text_is_proposed_when_the_window_is_not_stored_in_rows():
    from yomikata.locate import find_standard, propose
    lines = ["「おはよう、今日も早いね」", "「うん、朝練があるから」"]
    snaps = []
    for line in lines:
        ram = bytearray(0x8000)
        ram[0x1234:0x1234 + 64] = line.encode("cp932").ljust(64, b"\0")
        snaps.append((bytes(ram), [line]))
    (charset, hits), *_ = find_standard(snaps).items()
    got = propose(snaps, hits, charset)
    assert got["message"] == {"address": "0x00001234", "charset": "shift_jis", "max_bytes": 64, "terminator": "00"}
    assert got["agreement"] == [1.0, 1.0]
