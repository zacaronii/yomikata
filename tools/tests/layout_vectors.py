"""The layout conformance vectors (tools/tests/layout-vectors/): made-up layouts and memory, and what a reader
must read from them. Other clients run the JSON files against their own readers.

The expected values here are written by hand and checked against the reference reader, so a vector can't quietly
follow a bug in it. After changing a case: `python tools/tests/layout_vectors.py` rewrites the files."""

from __future__ import annotations

import json
import re
from pathlib import Path

from yomikata import layout, tbl

HERE = Path(__file__).resolve().parent / "layout-vectors"
HIDDEN = re.compile("[-�]")            # written as \uXXXX: a diff shows nothing for these


def sjis(s: str) -> bytes:
    return s.encode("cp932")


def u16(s: str, big: bool = False) -> bytes:
    return s.encode("utf-16-be" if big else "utf-16-le")


def pad(data: bytes, size: int) -> bytes:
    assert len(data) <= size, (data, size)
    return data.ljust(size, b"\0")


def rows(lines: list[bytes], stride: int) -> bytes:
    return b"".join(pad(line, stride) for line in lines)


def num(n: int, size: int = 4, big: bool = False) -> bytes:
    return n.to_bytes(size, "big" if big else "little", signed=n < 0)


KANA = "0001=あ\n0002=い\n0003=う\n0004=え\n0005=お\n0006=か\n/FFFF\n*FFFE\n"
YESNO = rows([u16("はい") + b"\0\0", u16("いいえ") + b"\0\0", u16("古い") + b"\0\0", b""], 16)
YESNO_ROWS = {"address": "0x08A20000", "charset": "utf16le", "max_bytes": 16, "terminator": "0000",
              "rows": {"count": 4, "stride": 16}}
TWO_ROWS = {"address": "0x80110000", "charset": "shift_jis", "max_bytes": 8, "terminator": "00",
            "rows": {"count": 2, "stride": 8}}
TWO_ROWS_DATA = rows([sjis("あい"), sjis("うえ")], 8)


def cases() -> list[dict]:
    out = []

    def case(name, checks, layout, memory, expected, entry=None, tables=None):
        v = {"name": name, "checks": checks, "entry": entry or {}}
        if tables:
            v["tables"] = tables
        v.update(layout=layout, memory=[{"address": f"0x{a:08X}", "hex": d.hex()} for a, d in memory.items()],
                 expected=expected)
        out.append(v)

    case("sjis-message-state",
         "A Shift-JIS message up to its terminator, the speaker's name, how much has typed out, and both flags. "
         "Keys a reader doesn't read (versions, poll, notes) change nothing.",
         {"versions": [], "poll": "frame", "notes": "made up",
          "message": {"address": "0x80100000", "charset": "shift_jis", "max_bytes": 64, "terminator": "00"},
          "speaker": {"address": "0x80100040", "charset": "shift_jis", "max_bytes": 16, "terminator": "00"},
          "shown": {"address": "0x80100050"},
          "waiting": {"address": "0x80100051"},
          "choosing": {"address": "0x80100052", "equals": 2}},
         {0x80100000: pad(sjis("今日は、いい天気ですね。") + b"\0" + sjis("残り"), 64)
          + pad(sjis("先生") + b"\0" + sjis("前"), 16) + bytes([6, 1, 1])},
         {"message": "今日は、いい天気ですね。", "speaker": "先生", "shown": 6, "waiting": True, "choosing": False,
          "on_screen": "今日は、いい"})

    case("sjis-terminator-boundary",
         "A terminator counts only where a character starts: ソ is 83 5C, and 5C is the terminator.",
         {"message": {"address": "0x80100000", "charset": "shift_jis", "max_bytes": 16, "terminator": "5C"}},
         {0x80100000: pad(sjis("ソア") + b"\x5c" + sjis("後"), 16)},
         {"message": "ソア"})

    case("sjis-mappings",
         "Shift-JIS is Windows' (cp932): full-width tilde and minus, NEC and IBM extensions, the user-defined area "
         "as U+E000 up, a backslash and a tilde for 5C and 7E, half-width kana.",
         {"message": {"address": "0x80100000", "charset": "shift_jis", "max_bytes": 32, "terminator": "00"}},
         {0x80100000: pad(bytes.fromhex("8160 817C 8161 8191 8192 81CA 8740 FA5C ED40 5C 7E B1 DF F040 F9FC"), 32)},
         {"message": "～－∥￠￡￢①纊纊\\~ｱﾟ"})

    case("sjis-invalid",
         "Bytes that aren't a character are one U+FFFD each: a lead byte with a bad second byte (both bytes), an "
         "unassigned pair, and the single bytes 80, A0, FD, FE, FF. A character cut off by max_bytes is dropped.",
         {"message": {"address": "0x80100000", "charset": "shift_jis", "max_bytes": 16}},
         {0x80100000: bytes.fromhex("41 8120 42 8540 80 A0 FD FE FF 43 835C B1 82A0")},
         {"message": "A�B������Cソｱ"})

    case("eucjp-text",
         "EUC-JP: kanji and kana, 8E + half-width kana, the eight codes decoders disagree on (A1BD, A1C0, A1C1, "
         "A1C2, A1DD, A1F1, A1F2, A2CC) as JIS X 0208 has them, and a single byte from 80 up as U+FFFD.",
         {"message": {"address": "0x00200000", "charset": "euc_jp", "max_bytes": 32, "terminator": "00"}},
         {0x00200000: pad("漢字とカナ".encode("euc_jp") + bytes.fromhex("8EB1 A1BD A1C0 A1C1 A1C2 A1DD A1F1 A1F2 A2CC 80 41"), 32)},
         {"message": "漢字とカナｱ\u2015\uff3c\u301c\u2016\u2212\xa2\xa3\xac\ufffdA"})

    case("eucjp-three-byte",
         "EUC-JP's 8F starts a three-byte character (JIS X 0212). One that isn't a character is one U+FFFD for "
         "all three bytes, and one cut off by max_bytes is dropped.",
         {"message": {"address": "0x00200000", "charset": "euc_jp", "max_bytes": 9}},
         {0x00200000: bytes.fromhex("8FB0A1 41 8F4142 8FB0A1")},
         {"message": "丂A�"})

    case("utf16le-rows-pointer",
         "Rows behind a pointer chain: a row ends at its terminator or at max_bytes, empty rows at the end are "
         "dropped and one in the middle is kept. The speaker is a number with a name.",
         {"message": {"base": "0x08A00020", "offsets": ["0x8", "0x4"], "charset": "utf16le", "max_bytes": 8,
                      "terminator": "0000", "rows": {"count": 4, "stride": 32}},
          "speaker": {"base": "0x08A00020", "offsets": ["0x8", "0x2"], "type": "u16"},
          "speaker_names": {"3": "先生", "4": "母"}},
         {0x08A00020: num(0x08A00200),
          0x08A00200: bytes(8) + num(0x08A00400),
          0x08A00400: bytes(2) + num(3, 2)
          + rows([u16("一行目です。"), b"", u16("三行目") + b"\0\0" + u16("古い"), b""], 32)},
         {"message": "一行目で\n\n三行目", "speaker": "先生"})

    case("utf16be-big-endian",
         "endian: big turns pointers and numbers around (the text's order is the charset's own).",
         {"endian": "big",
          "message": {"base": "0x80400000", "offsets": ["0x10"], "charset": "utf16be", "max_bytes": 32,
                      "terminator": "0000"},
          "shown": {"address": "0x80400004", "type": "u16"},
          "choice_cursor": {"address": "0x80400006", "type": "s16"}},
         {0x80400000: num(0x80400100, big=True) + num(2, 2, big=True) + num(-2, 2, big=True),
          0x80400100: bytes(16) + pad(u16("こんにちは", big=True), 32)},
         {"message": "こんにちは", "shown": 2, "choice_cursor": -2, "on_screen": "こん"})

    case("utf16-surrogates",
         "A surrogate pair is one character, also for `shown`. A surrogate on its own is one U+FFFD: a high one "
         "before another character, a low one, and a high one whose other half max_bytes cut off.",
         {"message": {"address": "0x08A01000", "charset": "utf16le", "max_bytes": 15},
          "shown": {"address": "0x08A01010"}},
         {0x08A01000: bytes.fromhex("3DD800DE 4100 3DD8 4200 00DE 3DD800DE") + bytes([2])},
         {"message": "\U0001f600A�B��", "shown": 2, "on_screen": "\U0001f600A"})

    case("utf8-length-cleanup",
         "A length field ends the text (here in the middle of a character, which is dropped); then the text's "
         "cleanup runs in order, a regex and a remap.",
         {"message": {"address": "0x02000000", "charset": "utf8", "max_bytes": 64,
                      "length": {"address": "0x02000040", "type": "u16"},
                      "cleanup": [{"regex": "<C\\d>", "replace": ""}, {"remap": {"～": "〜"}}]}},
         {0x02000000: pad("<C1>あ～いう".encode("utf-8"), 64) + num(4 + 3 + 3 + 3 + 1, 2)},
         {"message": "あ〜い"})

    case("utf8-invalid",
         "UTF-8 that isn't a character is one U+FFFD for as many bytes as its first byte claims: an overlong "
         "form, a continuation byte alone, a lead byte whose followers are wrong (they go with it), an encoded "
         "surrogate, and F8.",
         {"message": {"address": "0x02000000", "charset": "utf8", "max_bytes": 32, "terminator": "00"}},
         {0x02000000: pad(bytes.fromhex("41 C080 80 E38141 EDA080 F8888080 42 F09F9880"), 32)},
         {"message": "A�����B\U0001f600"})

    case("length-clamped",
         "A length larger than max_bytes reads max_bytes (0xFFFFFFFF is not -1); a length below zero reads "
         "nothing.",
         {"message": {"address": "0x02000000", "charset": "utf8", "max_bytes": 2,
                      "length": {"address": "0x02000008", "type": "u32"}},
          "speaker": {"address": "0x02000004", "charset": "utf8", "max_bytes": 4,
                      "length": {"address": "0x02000008", "type": "s32"}}},
         {0x02000000: b"ABCDEFGH" + num(0xFFFFFFFF)},
         {"message": "AB", "speaker": ""})

    case("table-two-files-controls",
         "A table charset: two table files (the later wins), end and newline codes from the table, and the "
         "entry's controls. wait, color and name are nothing, page and clear a blank line, newline a line break, "
         "a code nobody knows U+FFFD. Line breaks at the end are dropped, and so is half a code.",
         {"message": {"address": "0x80200000", "charset": "table", "max_bytes": 64, "terminator": "FFFF"},
          "speaker": {"address": "0x80200040", "charset": "table", "max_bytes": 5}},
         {0x80200000: pad(bytes.fromhex("0001 0002 F001 FFFE 0003 F002 0004 F003 0005 F004 0006 F006 0007 9999 "
                                        "F005 FFFF 0001"), 64) + bytes.fromhex("0001 FFFF 0002")},
         {"message": "あ学\nうえ\n\nお\nか\n\n校�", "speaker": "あ"},
         entry={"tables": ["tables/test/kana.tbl", "tables/test/kanji.tbl"], "unit": 2,
                "controls": {"F001": "wait", "F002": "color", "F003": "page", "F004": "newline", "F005": "name",
                             "F006": "clear"}},
         tables={"tables/test/kana.tbl": KANA, "tables/test/kanji.tbl": "0002=学\n0007=校\n"})

    case("table-unit-1",
         "A table with one byte per code.",
         {"message": {"address": "0x80200000", "charset": "table", "max_bytes": 8, "terminator": "00"}},
         {0x80200000: bytes.fromhex("01 02 7F 7E 02 00 01 01")},
         {"message": "あい�い"},
         entry={"tables": ["tables/test/one.tbl"], "unit": 1, "controls": {"7E": "ignore"}},
         tables={"tables/test/one.tbl": "01=あ\n02=い\n"})

    case("choices-count",
         "Choices are the text's rows, the first choice_count of them (a stale row after them doesn't count).",
         {"choices": YESNO_ROWS, "choice_count": {"address": "0x08A20040"},
          "choice_cursor": {"address": "0x08A20041"}, "choosing": {"address": "0x08A20042"}},
         {0x08A20000: YESNO + bytes([2, 1, 1])},
         {"choices": ["はい", "いいえ"], "choice_count": 2, "choice_cursor": 1, "choosing": True})

    case("choices-count-over",
         "A count larger than the rows gives the rows there are.",
         {"choices": YESNO_ROWS, "choice_count": {"address": "0x08A20040"}},
         {0x08A20000: YESNO + bytes([200])},
         {"choices": ["はい", "いいえ", "古い"], "choice_count": 200})

    case("choices-negative-count",
         "A count below zero gives no choices.",
         {"choices": YESNO_ROWS, "choice_count": {"address": "0x08A20040", "type": "s8"}},
         {0x08A20000: YESNO + bytes([0xFF])},
         {"choices": [], "choice_count": -1})

    case("choices-no-count",
         "A count that can't be read gives every row: empty rows at the end dropped, one in the middle kept.",
         {"choices": YESNO_ROWS, "choice_count": {"address": "0x08A30000"}},
         {0x08A20000: rows([u16("はい") + b"\0\0", b"", u16("いいえ") + b"\0\0", b""], 16)},
         {"choices": ["はい", "", "いいえ"], "errors": {"choice_count": ""}})

    case("choices-empty",
         "Rows that are all empty are no choices (not one empty choice).",
         {"choices": YESNO_ROWS},
         {0x08A20000: bytes(64)},
         {"choices": []})

    case("pointers-unreadable",
         "A field that can't be read is left out and named in errors; the others are still read. Here: a null "
         "pointer, a pointer that leads outside memory, a base outside memory, and a number that runs past the "
         "end of a block.",
         {"message": {"base": "0x80300000", "offsets": ["0x10"], "charset": "shift_jis", "max_bytes": 16},
          "speaker": {"base": "0x80300004", "offsets": ["0x0", "0x8"], "charset": "shift_jis", "max_bytes": 16},
          "shown": {"base": "0x80500000", "offsets": ["0x0"]},
          "choice_cursor": {"address": "0x80300106", "type": "u32"},
          "waiting": {"address": "0x80300008"}},
         {0x80300000: num(0) + num(0x80300100) + bytes(4),
          0x80300100: num(0x90000000) + bytes(4)},
         {"waiting": False, "errors": {"message": "", "speaker": "", "shown": "", "choice_cursor": ""}})

    case("flags-and-signed",
         "A flag with equals is true only at that value; signed types read below zero. A shown below zero is "
         "kept as read, and nothing is on screen.",
         {"message": {"address": "0x80600010", "charset": "shift_jis", "max_bytes": 16, "terminator": "00"},
          "waiting": {"address": "0x80600000", "equals": 255},
          "choosing": {"address": "0x80600001", "type": "s8", "equals": -1},
          "shown": {"address": "0x80600002", "type": "s16"},
          "choice_cursor": {"address": "0x80600004", "type": "s32"},
          "choice_count": {"address": "0x80600008", "type": "u16"}},
         {0x80600000: bytes.fromhex("FF FF FEFF FFFFFFFF 0300") + bytes(6) + pad(sjis("あいう"), 16)},
         {"message": "あいう", "waiting": True, "choosing": True, "shown": -2, "choice_cursor": -1,
          "choice_count": 3, "on_screen": ""})

    case("speaker-number-unnamed",
         "A speaker number without a name stays a number. A flag with equals 0 is true at zero.",
         {"speaker": {"address": "0x80600000"}, "speaker_names": {"3": "先生"},
          "waiting": {"address": "0x80600001", "equals": 0}},
         {0x80600000: bytes([7, 0])},
         {"speaker": 7, "waiting": True})

    case("shown-across-rows",
         "shown counts the message's characters without the breaks between rows.",
         {"message": TWO_ROWS, "shown": {"address": "0x80110010"}},
         {0x80110000: TWO_ROWS_DATA + bytes([3])},
         {"message": "あい\nうえ", "shown": 3, "on_screen": "あいう"})

    case("shown-past-end",
         "A shown past the end is the whole message.",
         {"message": TWO_ROWS, "shown": {"address": "0x80110010"}},
         {0x80110000: TWO_ROWS_DATA + bytes([200])},
         {"message": "あい\nうえ", "shown": 200, "on_screen": "あいうえ"})

    case("cleanup-own-then-entry",
         "A text's own cleanup runs first, then the entry's (the other order would leave a half-width ｲ here).",
         {"message": {"address": "0x02000000", "charset": "utf8", "max_bytes": 16, "terminator": "00",
                      "cleanup": [{"regex": "ｱ", "replace": "ｲ"}]},
          "choices": {"address": "0x02000010", "charset": "utf8", "max_bytes": 8, "terminator": "00",
                      "rows": {"count": 2, "stride": 8}}},
         {0x02000000: pad("ｱｲ".encode("utf-8"), 16) + rows(["ｲ".encode("utf-8"), "ｱ".encode("utf-8")], 8)},
         {"message": "いい", "choices": ["い", "ｱ"]},
         entry={"cleanup": [{"remap": {"ｲ": "い"}}]})

    case("rows-unreadable-partway",
         "If one row can't be read, the whole text fails.",
         {"message": {"address": "0x02000000", "charset": "utf8", "max_bytes": 8, "terminator": "00",
                      "rows": {"count": 3, "stride": 8}},
          "speaker": {"address": "0x02000000", "charset": "utf8", "max_bytes": 8, "terminator": "00"}},
         {0x02000000: rows(["あ".encode("utf-8"), "い".encode("utf-8")], 8)},
         {"speaker": "あ", "errors": {"message": ""}})

    case("rows-lengths-characters",
         "One length per row, counted in characters: a game that writes a new line over the old one leaves the "
         "old line's end behind, and only the lengths say where the new one stops. A one-byte character counts "
         "as one. A row of length 0 is empty, and empty rows at the end are dropped.",
         {"message": {"address": "0x80110000", "charset": "shift_jis", "max_bytes": 12,
                      "rows": {"count": 3, "stride": 12,
                               "lengths": {"address": "0x80110024", "counts": "characters"}}}},
         {0x80110000: rows([sjis("Aあいうえ"), sjis("かきくけ"), sjis("さし")], 12) + bytes([3, 1, 0])},
         {"message": "Aあい\nか"})

    case("rows-lengths-bytes",
         "Row lengths count bytes unless they say otherwise, and each number comes right after the last: u16 "
         "lengths are 2 bytes apart. A length that ends inside a character drops it; an empty row in the middle "
         "stays.",
         {"message": {"address": "0x08A20000", "charset": "utf16le", "max_bytes": 8,
                      "rows": {"count": 3, "stride": 8, "lengths": {"address": "0x08A20018", "type": "u16"}}}},
         {0x08A20000: rows([u16("あいうえ"), u16("かきくけ"), u16("さしすせ")], 8) + num(4, 2) + num(0, 2) + num(3, 2)},
         {"message": "あい\n\nさ"})

    case("rows-lengths-pointer",
         "Row lengths behind a pointer chain, big-endian: the chain gives the first length's address.",
         {"endian": "big",
          "message": {"address": "0x80300000", "charset": "utf8", "max_bytes": 8,
                      "rows": {"count": 2, "stride": 8,
                               "lengths": {"base": "0x80300010", "offsets": ["0x4"], "type": "s16"}}}},
         {0x80300000: rows([b"ABCDEFGH", b"IJKLMNOP"], 8) + num(0x80300020, 4, big=True),
          0x80300020: bytes(4) + num(2, 2, big=True) + num(-1, 2, big=True)},
         {"message": "AB"})

    case("length-characters",
         "A length in characters. The terminator still ends the text first, and so does max_bytes; a length "
         "below zero is 0.",
         {"message": {"address": "0x02000000", "charset": "utf8", "max_bytes": 16, "terminator": "00",
                      "length": {"address": "0x02000030", "counts": "characters"}},
          "speaker": {"address": "0x02000010", "charset": "utf8", "max_bytes": 16, "terminator": "00",
                      "length": {"address": "0x02000031", "counts": "characters"}},
          "choices": {"address": "0x02000020", "charset": "utf8", "max_bytes": 4,
                      "length": {"address": "0x02000031", "counts": "characters"},
                      "rows": {"count": 2, "stride": 8}}},
         {0x02000000: pad("あAいう".encode("utf-8"), 16) + pad("あい".encode("utf-8"), 16)
                      + rows(["あい".encode("utf-8"), b"ABCDEFGH"], 8) + bytes([3, 9])},
         {"message": "あAい", "speaker": "あい", "choices": ["あ", "ABCD"]})

    case("length-characters-negative-table",
         "A length in characters with a table: each code is one character, whatever it decodes to (here a "
         "control that is nothing, and a newline). A length below zero is 0.",
         {"message": {"address": "0x80200000", "charset": "table", "max_bytes": 16,
                      "length": {"address": "0x80200010", "counts": "characters"}},
          "speaker": {"address": "0x80200000", "charset": "table", "max_bytes": 16,
                      "length": {"address": "0x80200011", "type": "s8", "counts": "characters"}}},
         {0x80200000: bytes.fromhex("0001 F001 FFFE 0002 0003 0004 0005 0006") + bytes([4, 0xFF])},
         {"message": "あ\nい", "speaker": ""},
         entry={"tables": ["tables/test/kana.tbl"], "unit": 2, "controls": {"F001": "wait"}},
         tables={"tables/test/kana.tbl": KANA})

    case("length-and-rows-lengths",
         "With both a length and row lengths, both hold: here no row is longer than 4 bytes, and each row "
         "also stops at its own count of characters.",
         {"message": {"address": "0x80110000", "charset": "shift_jis", "max_bytes": 8,
                      "length": {"address": "0x80110010"},
                      "rows": {"count": 2, "stride": 8,
                               "lengths": {"address": "0x80110011", "counts": "characters"}}}},
         {0x80110000: rows([sjis("あいうえ"), sjis("かきくけ")], 8) + bytes([4, 3, 1])},
         {"message": "あい\nか"})

    case("when-false",
         "A text whose `when` is false is empty, and nothing else of it is read (the message's address isn't "
         "in memory here, and that is no error). What is on screen is empty too; an empty choices text is no "
         "answers. `when` is a flag like the others: nonzero, or `equals`.",
         {"message": {"address": "0x09000000", "charset": "utf8", "max_bytes": 16, "terminator": "00",
                      "when": {"address": "0x02000020"}},
          "shown": {"address": "0x02000023"},
          "speaker": {"address": "0x02000000", "charset": "utf8", "max_bytes": 16, "terminator": "00",
                      "when": {"address": "0x02000021", "equals": 2}},
          "choices": {"address": "0x02000010", "charset": "utf8", "max_bytes": 8, "terminator": "00",
                      "rows": {"count": 2, "stride": 8}, "when": {"address": "0x02000022", "equals": 2}}},
         {0x02000000: pad("あい".encode("utf-8"), 16) + rows(["う".encode("utf-8"), "え".encode("utf-8")], 8)
                      + bytes([0, 2, 1, 5])},
         {"message": "", "shown": 5, "on_screen": "", "speaker": "あい", "choices": []})

    case("when-unreadable",
         "A `when` that can't be read fails its text (the reader can't tell whether it is on screen), and so "
         "do row lengths that can't all be read. The other fields are still read.",
         {"message": {"address": "0x02000000", "charset": "utf8", "max_bytes": 8, "terminator": "00",
                      "when": {"base": "0x02000018", "offsets": ["0x0"]}},
          "choices": {"address": "0x02000000", "charset": "utf8", "max_bytes": 8,
                      "rows": {"count": 3, "stride": 8, "lengths": {"address": "0x0200001A"}}},
          "speaker": {"address": "0x02000008", "charset": "utf8", "max_bytes": 8, "terminator": "00",
                      "when": {"address": "0x0200001A"}}},
         {0x02000000: rows(["あ".encode("utf-8"), "い".encode("utf-8"), "う".encode("utf-8")], 8)
                      + bytes([0, 0, 3, 3])},
         {"speaker": "い", "errors": {"message": "", "choices": ""}})

    return out


def memory(vector: dict):
    """The vector's memory as a reader's `read`: a read has to lie inside one block."""
    blocks = [(int(b["address"], 16), bytes.fromhex(b["hex"])) for b in vector["memory"]]

    def read(address: int, size: int) -> bytes:
        for base, data in blocks:
            if base <= address and address + size <= base + len(data):
                return data[address - base:address - base + size]
        raise layout.LayoutError(f"0x{address:08X}+{size} is outside memory")
    return read


def replay(vector: dict) -> dict:
    """What the reference reader reads from a vector."""
    entry = vector["entry"]
    table = None
    if entry.get("tables"):
        table = tbl.Table()
        for path in entry["tables"]:
            table.update(tbl.parse(vector["tables"][path], path))
    return layout.read_layout(vector["layout"], entry, memory(vector), table)


def agrees(read: dict, expected: dict) -> bool:
    """Every value the same; for errors, which fields failed (the messages are each reader's own)."""
    plain = lambda d: {**d, "errors": sorted(d.get("errors", {}))}
    return plain(read) == plain(expected)


def files() -> dict[str, str]:
    """File name -> its text, with each error's message as the reference words it."""
    out = {}
    for v in cases():
        read = replay(v)
        assert agrees(read, v["expected"]), (v["name"], read, v["expected"])
        if "errors" in read:
            v["expected"]["errors"] = read["errors"]
        text = json.dumps(v, ensure_ascii=False, indent=1)
        out[v["name"] + ".json"] = HIDDEN.sub(lambda m: f"\\u{ord(m.group()):04x}", text) + "\n"
    return out


if __name__ == "__main__":
    for old in HERE.glob("*.json"):
        old.unlink()
    for name, text in files().items():
        (HERE / name).write_text(text, encoding="utf-8")
