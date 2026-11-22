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
        v["layouts" if isinstance(layout, list) else "layout"] = layout      # a list: one version's layouts
        v.update(memory=[{"address": f"0x{a:08X}", "hex": d.hex()} for a, d in memory.items()], expected=expected)
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

    case("flag-mask",
         "A flag's mask keeps only those bits before the test: nonzero, or equals. The bits are the stored ones, so "
         "a signed type's -1 under mask 3 is 3.",
         {"waiting": {"address": "0x80600000", "mask": 3},
          "choosing": {"address": "0x80600001", "mask": 3},
          "choices": {"address": "0x80600010", "charset": "shift_jis", "max_bytes": 8, "terminator": "00",
                      "when": {"address": "0x80600002", "type": "s8", "mask": 3, "equals": 3}}},
         {0x80600000: bytes.fromhex("02 08 FF") + bytes(13) + pad(sjis("はい"), 8)},
         {"waiting": True, "choosing": False, "choices": ["はい"]})

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

    state = {"message": {"address": "0x80100000", "charset": "shift_jis", "max_bytes": 16, "terminator": "00"},
             "speaker": {"address": "0x80100010", "charset": "shift_jis", "max_bytes": 8, "terminator": "00"},
             "shown": {"address": "0x80100028"},
             "waiting": {"address": "0x80100029"},
             "choosing": {"address": "0x8010002A"},
             "choices": {"address": "0x80100018", "charset": "shift_jis", "max_bytes": 8, "terminator": "00",
                         "rows": {"count": 2, "stride": 8}},
             "choice_count": {"address": "0x8010002B"},
             "choice_cursor": {"address": "0x8010002C", "type": "s8"}}
    block = pad(sjis("おはよう"), 16) + pad(sjis("先生"), 8) + rows([sjis("はい"), sjis("いいえ")], 8) \
        + bytes([3, 1, 1, 2, 1])

    case("layout-when-true",
         "A layout with its own `when` reads as usual while the flag is true (here: a 32-bit mode that "
         "`equals` 1).",
         {**state, "when": {"address": "0x80090000", "type": "u32", "equals": 1}},
         {0x80100000: block, 0x80090000: num(1)},
         {"message": "おはよう", "speaker": "先生", "shown": 3, "on_screen": "おはよ", "waiting": True,
          "choosing": True, "choices": ["はい", "いいえ"], "choice_count": 2, "choice_cursor": 1})

    case("layout-when-false",
         "While the layout's `when` is false nothing else is read, whatever the memory holds (the same bytes "
         "as in layout-when-true): the texts are empty, the flags false, there are no answers, and the "
         "numbers are left out, so nothing is on screen either.",
         {**state, "when": {"address": "0x80090000", "type": "u32", "equals": 1}},
         {0x80100000: block, 0x80090000: num(4)},
         {"message": "", "speaker": "", "waiting": False, "choosing": False, "choices": []})

    case("layout-when-false-speaker-number",
         "A speaker given as a number is a number like the others: left out while the layout's `when` is "
         "false. The fields' own memory isn't there at all, and that is no error.",
         {"when": {"address": "0x02000000"},
          "message": {"address": "0x09000000", "charset": "utf8", "max_bytes": 16, "terminator": "00"},
          "speaker": {"address": "0x09000010", "type": "u16"}, "speaker_names": {"3": "先生"},
          "waiting": {"address": "0x09000012"}},
         {0x02000000: bytes([0])},
         {"message": "", "waiting": False})

    case("layout-when-unreadable",
         "If the layout's `when` can't be read, nothing else is read, and `when` is the one field that "
         "failed.",
         {**state, "when": {"base": "0x80090000", "offsets": ["0x4"]}},
         {0x80100000: block, 0x80090000: num(0)},
         {"errors": {"when": ""}})

    # several pages in one buffer: an empty page, あ/い, う, an empty page, えお, the end, and an older text's rest
    book = b"\x0c" + sjis("あ\nい") + b"\x0c" + sjis("う") + b"\x0c\x0c" + sjis("えお") + b"\0" + sjis("か")
    at = 0x80120000

    def paged(cursor: int) -> dict:
        return {"address": f"0x{at:08X}", "charset": "shift_jis", "max_bytes": 32, "terminator": "00",
                "pages": {"break": "0C", "cursor": {"address": f"0x{cursor:08X}", "type": "u32"}}}

    def book_at(*cursors: int) -> dict:
        return {at: pad(book, 32) + b"".join(num(c) for c in cursors)}

    case("pages-cursor",
         "A buffer of pages with breaks between them: the text is the page the cursor's address is in, from the "
         "page's first character (message) to its last (choices). What follows the terminator is no page.",
         {"message": paged(at + 32), "speaker": paged(at + 36), "choices": paged(at + 40)},
         book_at(at + 1, at + 7, at + 13),
         {"message": "あ\nい", "speaker": "う", "choices": ["えお"]})

    case("pages-cursor-on-break",
         "A cursor on a break is still on the page before it (message). An empty page isn't a page: past it the "
         "cursor is still on the last page with anything in it (speaker, on the second of two breaks). Before "
         "the first such page there is nothing to show (choices, on the break the buffer starts with).",
         {"message": paged(at + 32), "speaker": paged(at + 36), "choices": paged(at + 40)},
         book_at(at + 6, at + 10, at),
         {"message": "あ\nい", "speaker": "う", "choices": []})

    case("pages-cursor-outside",
         "A cursor outside the text is on the last page: zero (message), before the text's address (speaker), "
         "past the terminator (choices).",
         {"message": paged(at + 32), "speaker": paged(at + 36), "choices": paged(at + 40)},
         book_at(0, at - 1, at + 16),
         {"message": "えお", "speaker": "えお", "choices": ["えお"]})

    wide = u16("あ\u0c05\u3000") + b"\x0c\0" + u16("い") + b"\0\0"
    case("pages-break-boundary",
         "A break counts only at the start of a character: 40 is also the second byte of ァ (83 40), and 0C 00 "
         "also lies across two UTF-16 units (05 0C, 00 30). The cursor may point into a character.",
         {"message": {"address": "0x80130000", "charset": "shift_jis", "max_bytes": 16, "terminator": "00",
                      "pages": {"break": "40", "cursor": {"address": "0x80130010", "type": "u32"}}},
          "speaker": {"address": "0x80130000", "charset": "shift_jis", "max_bytes": 16, "terminator": "00",
                      "pages": {"break": "40", "cursor": {"address": "0x80130014", "type": "u32"}}},
          "choices": {"address": "0x80130020", "charset": "utf16le", "max_bytes": 16, "terminator": "0000",
                      "pages": {"break": "0C00", "cursor": {"address": "0x80130018", "type": "u32"}}}},
         {0x80130000: pad(sjis("ァ") + b"@" + sjis("あ"), 16) + num(0x80130001) + num(0x80130003)
          + num(0x80130022) + bytes(4) + pad(wide, 16)},
         {"message": "ァ", "speaker": "あ", "choices": ["あ\u0c05\u3000"]})

    case("pages-pointers",
         "The buffer and the cursor behind pointer chains: the cursor is measured from where the text was found "
         "(speaker). A cursor that can't be read fails the field (message).",
         {"message": {"address": "0x80140000", "charset": "utf8", "max_bytes": 8, "terminator": "00",
                      "pages": {"break": "0C", "cursor": {"base": "0x80140014", "offsets": ["0x0"], "type": "u32"}}},
          "speaker": {"base": "0x80140010", "offsets": ["0x4"], "charset": "utf8", "max_bytes": 8, "terminator": "00",
                      "pages": {"break": "0C", "cursor": {"base": "0x80140018", "offsets": ["0x8"], "type": "u32"}}}},
         {0x80140000: pad(b"AB\x0cCD", 16) + num(0x8013FFFC) + num(0x90000000) + num(0x80140020),
          0x80140020: bytes(8) + num(0x80140003)},
         {"speaker": "CD", "errors": {"message": ""}})

    styled = sjis("あ") + b"\x07\x01" + sjis("い") + b"\x07\0" + sjis("う") + b"\x03\x83" + b"A\0" + sjis("か")
    case("codes-arguments",
         "A code and its argument bytes are one character and nothing in the text, so an argument of 00 isn't "
         "the terminator and an argument of 83 doesn't start a character (message). Without `codes` the same "
         "bytes end at the first 00 (speaker).",
         {"message": {"address": "0x80150000", "charset": "shift_jis", "max_bytes": 24, "terminator": "00",
                      "codes": {"03": 2, "07": 2}},
          "speaker": {"address": "0x80150000", "charset": "shift_jis", "max_bytes": 24, "terminator": "00"}},
         {0x80150000: pad(styled, 24)},
         {"message": "あいうA", "speaker": "あ\u0007\u0001い\u0007"})

    case("codes-lengths",
         "A code is one character for a length in characters (message: three characters are あ, a code, い). A "
         "code that doesn't fit in what was read is dropped like any last character (speaker, where max_bytes "
         "ends after the code's first byte; choices, where a length in bytes does).",
         {"message": {"address": "0x80160000", "charset": "shift_jis", "max_bytes": 16, "codes": {"07": 2},
                      "length": {"address": "0x80160010", "counts": "characters"}},
          "speaker": {"address": "0x80160000", "charset": "shift_jis", "max_bytes": 3, "codes": {"07": 2}},
          "choices": {"address": "0x80160000", "charset": "shift_jis", "max_bytes": 16, "codes": {"07": 2},
                      "length": {"address": "0x80160011"}}},
         {0x80160000: pad(sjis("あ") + b"\x07\x01" + sjis("いう"), 16) + bytes([3, 7])},
         {"message": "あい", "speaker": "あ", "choices": ["あい"]})

    case("codes-leads",
         "Where two leads start at the same byte the longer one counts (message: 1F 03 takes three bytes, any "
         "other 1F two). The terminator is looked for first (speaker: 07 FF ends the text though 07 is a "
         "code). In UTF-16 a code's bytes are counted the same way, so its 00 00 isn't the terminator "
         "(choices).",
         {"message": {"address": "0x80170000", "charset": "shift_jis", "max_bytes": 16, "terminator": "00",
                      "codes": {"1F": 2, "1F03": 3}},
          "speaker": {"address": "0x80170010", "charset": "shift_jis", "max_bytes": 16, "terminator": "07FF",
                      "codes": {"07": 2}},
          "choices": {"address": "0x80170020", "charset": "utf16le", "max_bytes": 16, "terminator": "0000",
                      "codes": {"1B00": 4}}},
         {0x80170000: pad(sjis("あ") + b"\x1f\x03\0" + sjis("い") + b"\x1f\0" + sjis("う"), 16)
          + pad(sjis("あ") + b"\x07\x01" + sjis("い") + b"\x07\xff" + sjis("う"), 16)
          + pad(u16("あ") + b"\x1b\0\0\0" + u16("い"), 16)},
         {"message": "あいう", "speaker": "あい", "choices": ["あい"]})

    case("codes-pages",
         "With `pages`, a code's argument isn't a break: 03 0C stays inside the first page (message), and the "
         "second page starts after the one real break (speaker).",
         {"message": {"address": "0x80180000", "charset": "shift_jis", "max_bytes": 16, "terminator": "00",
                      "codes": {"03": 2},
                      "pages": {"break": "0C", "cursor": {"address": "0x80180010", "type": "u32"}}},
          "speaker": {"address": "0x80180000", "charset": "shift_jis", "max_bytes": 16, "terminator": "00",
                      "codes": {"03": 2},
                      "pages": {"break": "0C", "cursor": {"address": "0x80180014", "type": "u32"}}}},
         {0x80180000: pad(sjis("あ") + b"\x03\x0c" + sjis("い") + b"\x0c" + sjis("う"), 16)
          + num(0x80180004) + num(0x80180007)},
         {"message": "あい", "speaker": "う"})

    case("codes-table",
         "Codes with a table charset: a control that takes an argument is skipped whole, so an argument of FFFF "
         "isn't the terminator (message). Without `codes` the same bytes end there (speaker).",
         {"message": {"address": "0x80200000", "charset": "table", "max_bytes": 16, "terminator": "FFFF",
                      "codes": {"F001": 4}},
          "speaker": {"address": "0x80200000", "charset": "table", "max_bytes": 16, "terminator": "FFFF"}},
         {0x80200000: pad(bytes.fromhex("0001 F001 FFFF 0002 FFFF 0003"), 16)},
         {"message": "あい", "speaker": "あ"},
         entry={"tables": ["tables/test/kana.tbl"], "unit": 2, "controls": {"F001": "wait"}},
         tables={"tables/test/kana.tbl": KANA})

    # A made-up script: instructions (27 01 05 and 00 28 12 34) around three texts, each after 80 00. The second
    # text carries the first one's page on; the third starts a new page, because F003 (page) stands before the
    # end code in front of it. The texts start at odd addresses.
    pieces = ["270105", "8000", "0001 0002 FFFE 0003", "FFFF", "8000", "0004", "F001 001E", "0005", "F003", "FFFF",
              "8000", "0006", "FFFF", "0028 1234"]
    script = pad(b"".join(bytes.fromhex(p) for p in pieces), 64)
    at = [sum(len(bytes.fromhex(p)) for p in pieces[:n]) for n in range(len(pieces) + 1)]   # where each starts
    told = {"tables": ["tables/test/kana.tbl"], "unit": 2, "controls": {"F001": "wait", "F003": "page"}}

    def page(cell: int, **more) -> dict:
        offset = {"address": f"0x{0x80300040 + 4 * cell:08X}", "type": "s32"}
        return {"address": "0x80300000", "charset": "table", "max_bytes": 64, "codes": {"F001": 4},
                "script": {"open": "8000", "offset": offset}, **more}

    case("script-typing",
         "A script page is read as far as the game has read it. The position is 6 bytes into the first text "
         "(message), and in the second text after a wait code and its argument (speaker): the second text "
         "follows the first one's end code directly, so it carries the page on, and neither the end code nor "
         "the 80 00 shows.",
         {"message": page(0), "speaker": page(1)},
         {0x80300000: script + num(at[2] + 4) + num(at[7])},
         {"message": "あい", "speaker": "あい\nうえ"}, entry=told, tables={"tables/test/kana.tbl": KANA})

    case("script-page-end",
         "The position is after the page's last character and its page code, before the end code (message), "
         "and after the end code of the last text, with an instruction next (speaker): the end code just "
         "behind the position is stepped over. The third text starts a new page, because a page code stands "
         "before the end code in front of it.",
         {"message": page(0), "speaker": page(1)},
         {0x80300000: script + num(at[9]) + num(at[13])},
         {"message": "あい\nうえお", "speaker": "か"}, entry=told, tables={"tables/test/kana.tbl": KANA})

    case("script-between-texts",
         "The position is on an open code that carries the page on: the page so far (message). Right after "
         "that code it is the same (speaker).",
         {"message": page(0), "speaker": page(1)},
         {0x80300000: script + num(at[4]) + num(at[5])},
         {"message": "あい\nう", "speaker": "あい\nう"}, entry=told, tables={"tables/test/kana.tbl": KANA})

    case("script-new-page",
         "The position is on an open code that starts a new page: nothing, the old page is over (message). "
         "Right after it nothing of the new page has been read yet (speaker), and then its first character "
         "(choices).",
         {"message": page(0), "speaker": page(1), "choices": page(2)},
         {0x80300000: script + num(at[10]) + num(at[11]) + num(at[12])},
         {"message": "", "speaker": "", "choices": ["か"]}, entry=told, tables={"tables/test/kana.tbl": KANA})

    case("script-not-in-a-text",
         "A position that isn't in a text reads as nothing: on the first open code, which no end code "
         "precedes (message); among the instructions after the last text, where walking back meets an end "
         "code before an open code (speaker); at the script's start (choices: the offset is 0).",
         {"message": page(0), "speaker": page(1), "choices": page(2)},
         {0x80300000: script + num(at[1]) + num(at[14]) + num(0)},
         {"message": "", "speaker": "", "choices": []}, entry=told, tables={"tables/test/kana.tbl": KANA})

    case("script-max-bytes",
         "Never more than max_bytes behind the position. 8 bytes reach back to the second text's open code "
         "but not to the first text, so the page starts at the second (message); 6 bytes reach no open code "
         "at all (speaker). An offset below zero is nothing (choices).",
         {"message": page(0, max_bytes=8), "speaker": page(0, max_bytes=6), "choices": page(1)},
         {0x80300000: script + num(at[7]) + num(-4)},
         {"message": "え", "speaker": "", "choices": []}, entry=told, tables={"tables/test/kana.tbl": KANA})

    # A list as the game writes a choice: 80 07, a count, then each answer after 00 00 01, ending in FFFF.
    asked = pad(bytes.fromhex("8007 0003 0012" "000001 0001 0002 FFFF" "000001 0003 FFFF" "000001 FFFF"
                              "000001 0004 0005 FFFF" "27"), 48)
    answers = {"address": "0x80310000", "charset": "table", "max_bytes": 48,
               "script": {"open": "000001", "offset": {"address": "0x80310030", "type": "u32"}, "texts": "rows"}}
    case("script-rows",
         "With texts: rows each text of the page is a row of its own. The position is after the last answer's "
         "end code: four answers, the third an empty one (choices). The same bytes without texts run "
         "together (message). Partway through the list the rows so far are read, the last one as far as the "
         "game has read it (speaker: the position is one character into the second answer).",
         {"choices": answers,
          "message": {**answers, "script": {k: v for k, v in answers["script"].items() if k != "texts"}},
          "speaker": {**answers, "script": {**answers["script"], "offset": {"address": "0x80310034", "type": "u32"}}}},
         {0x80310000: asked + num(36) + num(20)},
         {"choices": ["あい", "う", "", "えお"], "message": "あいうえお", "speaker": "あい\nう"},
         entry={"tables": ["tables/test/kana.tbl"], "unit": 2}, tables={"tables/test/kana.tbl": KANA})

    case("script-unreadable",
         "If the offset can't be read, the field fails (message), and so it does if the script can't be read "
         "at the offset (speaker: the offset points far outside memory).",
         {"message": {**page(0), "script": {"open": "8000", "offset": {"base": "0x80300044", "offsets": ["0x0"]}}},
          "speaker": page(2)},
         {0x80300000: script + num(at[7]) + num(0) + num(0x100000)},
         {"errors": {"message": "", "speaker": ""}}, entry=told, tables={"tables/test/kana.tbl": KANA})

    # Another made-up script, counted in codes: an instruction (0027 0105), then a window that opens at 8000 and
    # closes at F004. A text in it has two pages, each ending in F003 (page); after two instructions (0028) the
    # window opens again for a third. FFFF ends the script. F003 and F004 are both page codes.
    words = ["0027", "0105", "8000", "0001", "0002", "F003", "0003", "FFFE", "0004", "F003", "F004", "0028",
             "0028", "8000", "0005", "F003", "F004", "FFFF"]
    windows = pad(b"".join(bytes.fromhex(w) for w in words), 48)
    shut = {"tables": ["tables/test/kana.tbl"], "unit": 2,
            "controls": {"F003": "page", "F004": "clear", "0028": "ignore"}}

    def window(cell: int) -> dict:
        offset = {"address": f"0x{0x80320030 + 2 * cell:08X}", "type": "u16", "counts": "codes"}
        return {"address": "0x80320000", "charset": "table", "max_bytes": 48, "script": {"open": "8000", "offset": offset}}

    case("script-offset-codes",
         "An offset that counts codes: 5 codes of 2 bytes are 10 bytes into the script, after the first page's "
         "two characters (message). One code on, the position has passed the page code and nothing follows it "
         "yet: the page it ended is still up (speaker). And one more: a page code with something after it "
         "starts the page anew, so only the second page's first character is read (choices).",
         {"message": window(0), "speaker": window(1), "choices": window(2)},
         {0x80320000: windows + num(5, 2) + num(6, 2) + num(7, 2)},
         {"message": "あい", "speaker": "あい", "choices": ["う"]}, entry=shut, tables={"tables/test/kana.tbl": KANA})

    case("script-page-codes",
         "Page codes inside a text. The second page with its line break, the position past its page code "
         "(message). Past the code that closes the window as well: that one is a page code too, and the page "
         "after it is empty (speaker). The same among the instructions that follow, which are controls and "
         "read as nothing (choices).",
         {"message": window(0), "speaker": window(1), "choices": window(2)},
         {0x80320000: windows + num(10, 2) + num(11, 2) + num(12, 2)},
         {"message": "う\nえ", "speaker": "", "choices": []}, entry=shut, tables={"tables/test/kana.tbl": KANA})

    case("script-page-codes-end",
         "The window opened again: its one character with the position on the page code (message) and past it "
         "(speaker). At the script's end, past the closing code and the end code: the end code is stepped "
         "over, the closing code is the last thing read and so starts no page, and the page code before it has "
         "the closing code after it, so the page is empty (choices).",
         {"message": window(0), "speaker": window(1), "choices": window(2)},
         {0x80320000: windows + num(15, 2) + num(16, 2) + num(18, 2)},
         {"message": "お", "speaker": "お", "choices": []}, entry=shut, tables={"tables/test/kana.tbl": KANA})

    # A third made-up script, counted in codes: a choice's two answers, each a text of its own, then the end code
    # and two numbers the game reads after it (where each answer leads). The game waits past those.
    words = ["8000", "0001", "FFFF", "8000", "0002", "0003", "FFFF", "0006", "0005"]
    answers = pad(b"".join(bytes.fromhex(w) for w in words), 24)

    def resting(cell: int, rest: int | None, texts: str | None = None) -> dict:
        offset = {"address": f"0x{0x80340018 + 2 * cell:08X}", "type": "u16", "counts": "codes"}
        more = {k: v for k, v in (("rest", rest), ("texts", texts)) if v is not None}
        return {"address": "0x80340000", "charset": "table", "max_bytes": 24,
                "script": {"open": "8000", "offset": offset, **more}}

    case("script-rest",
         "The position is two codes past the end code, on neither a text nor an open code. With rest 2 it is "
         "read as just past the end code, so the page is still up: both texts, joined (message). With rest 1 "
         "the end code is too far back, and without rest nobody looks: nothing (speaker, shown as a text). "
         "With rows, each text is an answer (choices).",
         {"message": resting(0, 2), "speaker": resting(0, 1), "choices": resting(0, 16, "rows")},
         {0x80340000: answers + num(9, 2)},
         {"message": "あいう", "speaker": "", "choices": ["あ", "いう"]},
         entry={"tables": ["tables/test/kana.tbl"], "unit": 2}, tables={"tables/test/kana.tbl": KANA})

    case("script-rest-nearest",
         "Rest looks back from the position and stops at the nearest end code. Just past an end code that "
         "is the one, and nothing changes (message, at the script's end). Two codes into the second answer, the first one's "
         "end code is in reach: the position goes back to it and the second answer isn't read, which is why "
         "rest isn't for a script whose texts can start that close to an end code (speaker). One code past "
         "the end, rest 16 reaches the first answer's end code as well but takes the nearer one (choices).",
         {"message": resting(0, 4), "speaker": resting(1, 2), "choices": resting(2, 16, "rows")},
         {0x80340000: answers + num(7, 2) + num(5, 2) + num(8, 2)},
         {"message": "あいう", "speaker": "あ", "choices": ["あ", "いう"]},
         entry={"tables": ["tables/test/kana.tbl"], "unit": 2}, tables={"tables/test/kana.tbl": KANA})

    # A fourth made-up script: a question and two answers in one text, a line each, then the end code and where
    # each answer leads. An answer's row is a code that says which answer it is (they are not in order), a colon
    # and its words; the game draws the letters. It waits past the end with a cell at 0xFFFF; another holds the
    # cursor.
    colon = KANA + "00C7=：\n"
    numbered = {"tables": ["tables/test/colon.tbl"], "unit": 2, "controls": {"0098": "choice", "0198": "choice"}}
    colons = [{"regex": "(?s)^(.*?\\n)??：", "replace": ""}, {"regex": "\\n：", "replace": "\n"}]

    def asking(address: int, words: list[str]) -> tuple[dict, bytes]:
        size = 2 * len(words)
        page = {"address": f"0x{address:08X}", "charset": "table", "max_bytes": size,
                "script": {"open": "8000", "rest": 2,
                           "offset": {"address": f"0x{address + size:08X}", "type": "u16", "counts": "codes"}}}
        up = {"address": f"0x{address + size + 2:08X}", "type": "u16", "equals": 65535}
        return ({"message": page, "choosing": up, "choices": {**page, "when": up, "cleanup": colons},
                 "choice_cursor": {"address": f"0x{address + size + 4:08X}", "type": "u16"}},
                b"".join(bytes.fromhex(w) for w in words))

    choice, asked = asking(0x80350000, ["8000", "0001", "0002", "FFFE", "0198", "00C7", "0003", "FFFE", "0098",
                                        "00C7", "0004", "0005", "FFFE", "FFFF", "0006", "0005"])

    case("script-rest-choice",
         "A choice in one text: the question's row, then a row for each answer, a colon before its words. The "
         "game waits two codes past the end code; with rest the message is the whole page. The answers are the "
         "same text with a cleanup of their own, which runs over the whole text before it is split into rows: "
         "the first rule takes everything up to the first row that starts with a colon, line breaks included "
         "(its group is optional and lazy, ??), the second the other rows' colons.",
         choice, {0x80350000: asked + num(16, 2) + num(0xFFFF, 2) + num(1, 2)},
         {"message": "あい\n：う\n：えお", "choosing": True, "choices": ["う", "えお"], "choice_cursor": 1},
         entry=numbered, tables={"tables/test/colon.tbl": colon})

    case("script-rest-choice-typing",
         "The same choice while its first answer types out: the message is the page so far, and the answers "
         "are none, because their own when is false until the choice is up.",
         choice, {0x80350000: asked + num(7, 2) + num(0, 2) + num(0, 2)},
         {"message": "あい\n：う", "choosing": False, "choices": [], "choice_cursor": 0},
         entry=numbered, tables={"tables/test/colon.tbl": colon})

    alone, asked = asking(0x80360000, ["8000", "0098", "00C7", "0003", "FFFE", "0198", "00C7", "0004", "FFFE",
                                       "FFFF", "0005", "0006"])

    case("script-rest-choice-first-row",
         "A choice with no question's row: the page starts with an answer. The first rule's group is lazy, so it "
         "matches nothing here and the first answer stays; a greedy one would take the first answer away.",
         alone, {0x80360000: asked + num(12, 2) + num(0xFFFF, 2) + num(0, 2)},
         {"message": "：う\n：え", "choosing": True, "choices": ["う", "え"], "choice_cursor": 0},
         entry=numbered, tables={"tables/test/colon.tbl": colon})

    # A fifth made-up script: a game that starts a choice's first answer on a row of its own, whether or not the
    # script has a line break before it. The code that opens the choice (0097) is a line break in `controls`, and
    # the message's own cleanup takes out the doubled break that leaves before a colon.
    opened = {**numbered, "controls": {**numbered["controls"], "0097": "newline"}}
    doubled = [{"regex": "^\\n：", "replace": "："}, {"regex": "\\n\\n：", "replace": "\n："}]

    def opening(address: int, words: list[str]) -> tuple[dict, bytes]:
        fields, data = asking(address, words)
        return {**fields, "message": {**fields["message"], "cleanup": doubled}}, data

    glued, asked = opening(0x80370000, ["8000", "0001", "0002", "0097", "0198", "00C7", "0003", "FFFE", "0098",
                                        "00C7", "0004", "0005", "FFFE", "FFFF", "0006", "0005"])

    case("script-choice-row-no-break",
         "The question runs straight into the choice, with no line break code before it. The code that opens the "
         "choice is a newline in controls, so the first answer starts a row: the message has the question's row "
         "and a row for each answer, and the answers are both there. Were that code nothing, the first answer "
         "would sit on the question's row and be lost from the answers.",
         glued, {0x80370000: asked + num(16, 2) + num(0xFFFF, 2) + num(0, 2)},
         {"message": "あい\n：う\n：えお", "choosing": True, "choices": ["う", "えお"], "choice_cursor": 0},
         entry=opened, tables={"tables/test/colon.tbl": colon})

    case("script-choice-row-no-break-typing",
         "The same script just past the code that opens the choice: the line break it gives ends the text so "
         "far and is dropped with the other trailing line breaks.",
         glued, {0x80370000: asked + num(4, 2) + num(0, 2) + num(0, 2)},
         {"message": "あい", "choosing": False, "choices": [], "choice_cursor": 0},
         entry=opened, tables={"tables/test/colon.tbl": colon})

    broken, asked = opening(0x80380000, ["8000", "0001", "0002", "FFFE", "0097", "0198", "00C7", "0003", "FFFE",
                                         "0098", "00C7", "0004", "0005", "FFFE", "FFFF", "0006", "0005"])

    case("script-choice-row-break",
         "The usual script: a line break code, then the choice. Now there are two line breaks before the first "
         "colon, and the message's own cleanup makes them one (its second rule), so the message is the same as "
         "with no line break code. The answers' cleanup takes everything up to the first row that starts with a "
         "colon either way.",
         broken, {0x80380000: asked + num(17, 2) + num(0xFFFF, 2) + num(1, 2)},
         {"message": "あい\n：う\n：えお", "choosing": True, "choices": ["う", "えお"], "choice_cursor": 1},
         entry=opened, tables={"tables/test/colon.tbl": colon})

    first, asked = opening(0x80390000, ["8000", "0097", "0098", "00C7", "0003", "FFFE", "0198", "00C7", "0004",
                                        "FFFE", "FFFF", "0005", "0006"])

    case("script-choice-row-first",
         "A choice with no question: the page starts with the code that opens it. The line break it gives is "
         "the first thing in the text, and the cleanup's first rule (anchored at the start of the text, not of "
         "each line) takes it out.",
         first, {0x80390000: asked + num(13, 2) + num(0xFFFF, 2) + num(0, 2)},
         {"message": "：う\n：え", "choosing": True, "choices": ["う", "え"], "choice_cursor": 0},
         entry=opened, tables={"tables/test/colon.tbl": colon})

    # Two layouts for one version: a game that loads a program per chapter keeps its cells elsewhere in each.
    def chapter(value: int, text: int) -> dict:
        return {"when": {"address": "0x80330000", "type": "u32", "equals": value},
                "message": {"address": f"0x{text:08X}", "charset": "shift_jis", "max_bytes": 8, "terminator": "00"},
                "waiting": {"address": "0x80330004"}}
    both = [chapter(0x27BD0018, 0x80330008), chapter(0xAFBF0020, 0x80330010)]
    lines = num(1) + pad(sjis("一章"), 8) + pad(sjis("二章"), 8)
    case("layouts-first-true",
         "Several layouts for one version are taken in order, and the first whose own when is true is read. "
         "Here it is the second.",
         both, {0x80330000: num(0xAFBF0020) + lines}, {"message": "二章", "waiting": True})

    case("layouts-none-true",
         "No layout's when is true: the first is read, which gives what a false when gives.",
         both, {0x80330000: num(0x3C028007) + lines}, {"message": "", "waiting": False})

    case("layouts-without-when",
         "A layout without a when is always true, so nothing after it is ever read. One whose when can't be "
         "read is passed over.",
         [{**both[0], "when": {"base": "0x80330020", "offsets": ["0x0"], "type": "u32"}},
          {k: v for k, v in both[0].items() if k != "when"}, both[1]],
         {0x80330000: num(0xAFBF0020) + lines + bytes(12) + num(0)}, {"message": "一章", "waiting": True})

    sets = {"tables": {"by": {"address": "0x80210010", "type": "u16"},
                       "sets": {"36": ["tables/test/one.tbl"], "94": ["tables/test/two.tbl"]}},
            "message": {"address": "0x80210000", "charset": "table", "max_bytes": 16, "terminator": "FFFF"},
            "speaker": {"address": "0x80210008", "charset": "shift_jis", "max_bytes": 8, "terminator": "00"}}
    chapters = {"tables/test/kana.tbl": KANA, "tables/test/one.tbl": "0100=学\n",
                "tables/test/two.tbl": "0100=校\n0002=胃\n"}
    words = bytes.fromhex("0001 0002 0100 FFFF") + pad(sjis("先生"), 8)
    case("tables-set",
         "A layout's own tables: the number is 94, so two.tbl follows the entry's table (and wins over it for "
         "0002). The same bytes under 36 would be あい学.",
         sets, {0x80210000: words + num(94, 2)},
         {"message": "あ胃校", "speaker": "先生"},
         entry={"tables": ["tables/test/kana.tbl"], "unit": 2}, tables=chapters)

    case("tables-no-set",
         "No set for the number: a table text fails instead of being read with the wrong table. A text in a "
         "standard charset is still read.",
         sets, {0x80210000: words + num(7, 2)},
         {"speaker": "先生", "errors": {"message": ""}},
         entry={"tables": ["tables/test/kana.tbl"], "unit": 2}, tables=chapters)

    case("tables-unreadable",
         "The number can't be read: the same. Here the entry has no tables of its own; a set can be all there is.",
         {**sets, "tables": {**sets["tables"], "by": {"base": "0x80210014", "offsets": ["0x0"], "type": "u16"}}},
         {0x80210000: words + num(94, 2) + bytes(2) + num(0)},
         {"speaker": "先生", "errors": {"message": ""}},
         entry={"unit": 2}, tables=chapters)

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

    def load(paths: list[str]) -> tbl.Table:
        table = tbl.Table()
        for path in paths:
            table.update(tbl.parse(vector["tables"][path], path))
        return table
    table = load(entry["tables"]) if entry.get("tables") else None
    read = memory(vector)
    lay = layout.live(vector["layouts"], read) if "layouts" in vector else vector["layout"]
    return layout.read_layout(lay, entry, read, table, load)


def layouts_of(vector: dict) -> list[dict]:
    return vector.get("layouts") or [vector["layout"]]


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
