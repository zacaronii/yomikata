"""The catalog's scripts and lib/ modules, run by the reader runtime on made-up memory. No game's text is here:
the messages below are written for the tests, in the form the scripts' notes describe."""

import os
import struct
from pathlib import Path

import pytest

from yomikata import reader as rd
from yomikata.bundle import BundleError, bundle

try:
    rd.library()
except rd.ReaderUnavailable as e:        # pragma: no cover
    if os.environ.get("YOMIKATA_REQUIRE_READER"):
        raise
    pytest.skip(str(e), allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]


def sparse(chunks: dict[int, bytes]):
    """Memory with a few stretches in it: {address: bytes}."""
    def read(address: int, size: int) -> bytes:
        for start, data in chunks.items():
            if start <= address < start + len(data):
                return data[address - start:address - start + size]
        return b""
    return read


def test_markup_tokens():
    with rd.Reader(sparse({})) as r:
        r.load(bundle_text('local markup = require("lib.markup")\n'
                           'function walk(s) local out = {} for kind, value in markup.tokens(s) do '
                           'out[#out + 1] = kind .. "=" .. value end return table.concat(out, "|") end\n'
                           'function name(s) return markup.name(s) end'))
        assert r.eval('walk("<speed 2>ab<k>\\ncd<p>")') == "tag=speed 2|text=ab|tag=k|text=\ncd|tag=p"
        assert r.eval('walk("ab<wai")') == "text=ab"            # a tag still being written
        assert r.eval('walk("")') == ""
        assert r.eval('name("speed 2")') == "speed\t2"
        assert r.eval('name("k")') == "k\t"


def bundle_text(source: str) -> str:
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "t.lua"
        path.write_text(source, encoding="utf-8")
        return bundle(path, ROOT)


def test_bundle_keeps_the_header_first_and_refuses_what_is_not_in_lib(tmp_path):
    script = tmp_path / "X.lua"
    script.write_text('-- reader: title あ (A)\n-- reader: no hooks\n\n-- notes\nlocal m = require("lib.markup")\n',
                      encoding="utf-8")
    text = bundle(script, ROOT)
    assert text.startswith("-- reader: title あ (A)\n-- reader: no hooks\n\n-- notes\nlocal __loaded")
    assert rd.header(text) == [("title", "あ (A)"), ("no hooks", "")]
    assert text.count('__module["lib.markup"]') == 1
    script.write_text('local m = require("lib.nothing")', encoding="utf-8")
    with pytest.raises(BundleError):
        bundle(script, ROOT)
    script.write_text('local m = require("os")', encoding="utf-8")
    with pytest.raises(BundleError):
        bundle(script, ROOT)
    script.write_text("reader.log('alone')", encoding="utf-8")
    assert bundle(script, ROOT) == "reader.log('alone')"


# ---- games/gc/G8MJ01.lua ----
#
# Memory as the script's notes describe it, made up: the disc's header and two words of code for the build
# check, a font with a MAP1 part of each kind, the window slots, and one window's records.

PM_WINDOWS, PM_FONT = 0x80418808, 0x80418758
SLOTS_AT, FONT_AT, HOLDER_AT, DATA_AT = 0x80817000, 0x80B00000, 0x803C0000, 0x81600000
KANJI = {"勇": 300, "者": 301, "旅": 302, "町": 303}           # a few kanji, as a table of pairs
LINE, KEY, PAGE, END = 0xFFFB, 0xFFFE, 0xFFFD, 0xFFFF


def glyph_of(ch: str) -> int:
    code = int.from_bytes(ch.encode("cp932"), "big")
    if 0x20 <= code <= 0x7E:
        return code - 0x20                                   # method 0: straight on from the first
    if 0x829F <= code <= 0x82F1:
        return 100 + (code - 0x829F)                         # method 2: a table
    if code in (0x8140, 0x8141, 0x8142):
        return (0x81 - 0x88) * 188 - 0x5E + 2000 + (code & 0xFF) - 0x40      # method 1: by arithmetic
    return KANJI[ch]                                         # method 3: pairs


def pm_font() -> bytes:
    def part(method, first, last, entries, data=b""):
        body = struct.pack(">4H", method, first, last, entries) + data
        body += bytes(-(len(body) + 8) % 4)
        return b"MAP1" + struct.pack(">I", len(body) + 8) + body
    parts = [b"INF1" + struct.pack(">I", 0x20) + bytes(0x18),
             part(0, 0x20, 0x7E, 0),
             part(2, 0x829F, 0x82F1, 0x53, b"".join(struct.pack(">H", 100 + i) for i in range(0x53))),
             part(1, 0x8140, 0x8142, 1, struct.pack(">H", 2000)),
             part(3, 0x8800, 0x9FFF, len(KANJI), b"".join(
                 struct.pack(">HH", int.from_bytes(k.encode("cp932"), "big"), g) for k, g in KANJI.items()))]
    return b"FONTbfn1" + struct.pack(">II", 0, len(parts)) + bytes(0x10) + b"".join(parts)


def pm_records(*items) -> bytes:
    """Records from characters, the codes above, and ("ruby", reading) for furigana over what came before
    (("ruby", reading, n) says over how many characters)."""
    out, x, y, when, xs = [], 0, -10, 0, []
    for item in items:
        if isinstance(item, tuple):
            over = item[2] if len(item) > 2 else 1
            small_x = xs[-over] + (22 * over - 12 * len(item[1])) // 2
            for ch in item[1]:
                out.append(struct.pack(">IHhhHIfI", 0, glyph_of(ch), small_x, y + 22, 0, 0, 0.55, 0))
                small_x += 12
        elif isinstance(item, int):
            out.append(struct.pack(">IHhhHIfI", 0, item, 0, 0, 0, when, 0.0, 0))
            if item == LINE:
                x, y, xs = 0, y - 32, []
        else:
            for ch in item:
                out.append(struct.pack(">IHhhHIfI", 8, glyph_of(ch), x, y, 0, when, 1.0, 0))
                xs.append(x)
                x, when = x + 22, when + 25
    return b"".join(out)


def pm_memory(records: bytes | None, at: int = 0, started: int = 1, window_type: int = 0, slot: int = 0,
              version: int = 0, font: bytes | None = None, place: tuple = (-280.0, 225.0), cursor: int = 0):
    chunks = {0x80000000: b"G8MJ01" + bytes([0, version]) + bytes(0x14) + bytes.fromhex("C2339F3D"),
              0x80073C00: bytes.fromhex("80CD14F8"), 0x8007EF18: bytes.fromhex("83ED15A8"),
              PM_FONT: struct.pack(">I", FONT_AT), FONT_AT: pm_font() if font is None else font,
              PM_WINDOWS: struct.pack(">I", SLOTS_AT)}
    slots = bytearray(7 * 0x48)
    if records is not None:
        struct.pack_into(">HHhi", slots, slot * 0x48 + 2, 3, 0, 255, window_type)
        struct.pack_into(">ff", slots, slot * 0x48 + 0x0C, *place)
        struct.pack_into(">I", slots, slot * 0x48 + 0x28, HOLDER_AT)
        count = len(records) // 0x18 - 1                     # the end code isn't counted
        data = bytearray(0x3C) + records
        data += bytes(0x7A58 - len(data))
        data[0x7A43] = cursor
        struct.pack_into(">i", data, 0x10, at)
        struct.pack_into(">Q", data, 0x18, started)
        struct.pack_into(">i", data, 0x38, count)
        chunks[HOLDER_AT] = struct.pack(">I", DATA_AT)
        chunks[DATA_AT] = bytes(data)
    chunks[SLOTS_AT] = bytes(slots)
    return sparse(chunks)


def of(events, kind):
    return [e for k, e in events if k == kind]


def pm_reader(read):
    r = rd.Reader(read, platform="gc", game_id="G8MJ01")
    r.load(bundle(ROOT / "games/gc/G8MJ01.lua"), "G8MJ01.lua")
    r.take()
    return r


# "Hello, hero. / You came well. [key] [page] Now, on a journey." with furigana on two words.
BUBBLE = pm_records("やあ、", "勇者", ("ruby", "ゆうしゃ", 2), "よ。", LINE, "よくきたな", LINE, KEY, PAGE,
                    "さあ、", "旅", ("ruby", "たび"), "にでよう", LINE, KEY, END)
BUBBLE_KEY, BUBBLE_END = 14, 23         # the record of the first wait, and how many records there are


def test_paper_mario_pages_as_the_window_reaches_them():
    with pm_reader(pm_memory(BUBBLE, at=3)) as r:                        # typing its first page
        r.frame(1)
        r.frame(2)
        events = r.take()
        (first,) = of(events, "text")
        assert (first["text"], first["ruby"]) == ("やあ、勇者よ。\nよくきたな", [[3, 2, "ゆうしゃ"]])
        assert first["meta"] == {"page": 1, "pages": 2} and first["channel"] == "dialogue"
        # Where each character is: 608 by 480, the slot's corner at 24, 15, the text 62.5 and 47 in from it.
        (layout,) = of(events, "layout")
        assert (layout["window"], layout["space"]) == (first["window"], [608, 480])
        assert len(layout["glyphs"]) == len(first["text"])
        assert layout["glyphs"][:2] == [[86.5, 62, 22, 24], [108.5, 62, 22, 24]]
        assert layout["glyphs"][7] is None and layout["glyphs"][8] == [86.5, 94, 22, 24]       # the second line
        r.read = pm_memory(BUBBLE, at=BUBBLE_KEY)                        # waiting at the end of it
        r.frame(3)
        r.read = pm_memory(BUBBLE, at=BUBBLE_KEY + 1)                    # at the scroll, not through it
        r.frame(4)
        assert r.take("text") == []
        r.read = pm_memory(BUBBLE, at=BUBBLE_KEY + 3)                    # the second page types
        r.frame(5)
        r.read = pm_memory(BUBBLE, at=BUBBLE_END)
        r.frame(6)
        events = r.take()
        (second,) = of(events, "text")
        assert of(events, "layout")[0]["glyphs"][0] == [86.5, 62, 22, 24]    # the page's first line is at the top again
        assert (second["text"], second["ruby"]) == ("さあ、旅にでよう", [[3, 1, "たび"]])
        assert second["meta"] == {"page": 2, "pages": 2} and second["id"] != first["id"]
        assert not [e for e in of(events, "log") if e["level"] == "error"]


def test_paper_mario_window_closed_and_opened_again():
    with pm_reader(pm_memory(None)) as r:
        r.frame(1)
        assert r.take("text") == []
        r.read = pm_memory(BUBBLE, at=5, started=100)
        r.frame(2)
        r.read = pm_memory(None)                             # closed: the list's memory is no longer the window's
        r.frame(3)
        r.read = pm_memory(BUBBLE, at=5, started=200, slot=2)            # the same message again, later
        r.frame(4)
        r.state_loaded()
        r.frame(5)                                           # after a savestate: said again
        events = r.take()
        assert [e["text"][:3] for e in of(events, "text")] == ["やあ、"] * 3
        assert of(events, "layout")[1] == {"window": "window 0", "seq": 1, "closed": True}


def test_paper_mario_choice():
    answers = pm_records("はい", LINE, "いいえ", END)
    box = dict(window_type=5, slot=1, place=(-75.0, 40.0))
    with pm_reader(pm_memory(answers, at=6, **box)) as r:
        r.frame(1)
        events = r.take()
        (event,) = [e for k, e in events if k == "text"]
        assert (event["channel"], event["text"]) == ("choice", "はい\nいいえ")
        (layout,) = [e for k, e in events if k == "layout"]
        assert layout["glyphs"][0] == [269.4, 236, 22, 24] and layout["glyphs"][3] == [269.4, 268, 22, 24]
        assert [e for k, e in events if k == "cursor"] == [{"index": 0, "horizontal": False}]
        r.read = pm_memory(answers, at=6, cursor=1, **box)
        r.frame(2)
        r.read = pm_memory(None)
        r.frame(3)
        assert r.take("cursor") == [{"index": 1, "horizontal": False}, {"closed": True}]


def test_paper_mario_furigana_only_over_the_kanji_they_reach():
    # Kana before the word, and a kanji two places back that the furigana don't reach.
    records = pm_records("町", "の", "旅", ("ruby", "たび"), "へ", END)
    with pm_reader(pm_memory(records, at=1)) as r:
        r.frame(1)
        assert r.take("text")[0]["ruby"] == [[2, 1, "たび"]]


def test_paper_mario_without_its_font_reads_nothing():
    with pm_reader(pm_memory(BUBBLE, at=3, font=bytes(0x40))) as r:
        r.frame(1)
        assert r.take() == []


def test_paper_mario_another_build():
    for read in (pm_memory(BUBBLE, version=1),
                 sparse({0x80000000: b"GXXJ01" + bytes(0x1A)})):
        with rd.Reader(read, platform="gc", game_id="G8MJ01") as r:
            with pytest.raises(rd.ScriptError, match="another build"):
                r.load(bundle(ROOT / "games/gc/G8MJ01.lua"))


# ---- games/ps1/SLPS-02100.lua ----
#
# A scene made up of the table's own characters: two messages, a control with an argument inside the first.

P2_SCENE, P2_WINDOW, P2_UP = 0x800DE014, 0x8007B6E0, 0x8007B708
P2_TEXT_AT, P2_OBJECT_AT = 0x80150000, 0x80170000


def p2_codes(text: str) -> bytes:
    from yomikata import tbl
    table = tbl.parse((ROOT / "games/ps1/SLPS-02100.tbl").read_text(encoding="utf-8"))
    back = {}
    for code, ch in table.chars.items():
        back.setdefault(ch, code)
    return b"".join(back[ch] for ch in text)


def p2_memory(reading: int, up: int = 1, kind: int = 0x2408, word: int = 0x02603821):
    wide = p2_codes("　")
    first = (wide + p2_codes("きょう") + bytes.fromhex("1D12 0300") + p2_codes("は") + bytes.fromhex("0111")
             + p2_codes("あめ") + bytes.fromhex("0611"))
    second = p2_codes("あした") + bytes.fromhex("1F11") + p2_codes("も")
    scene = first + bytes.fromhex("0311") + second + bytes.fromhex("0311")
    window = bytearray(0x20)
    struct.pack_into("<H", window, 0x04, kind)
    struct.pack_into("<I", window, 0x0C, P2_TEXT_AT + reading)
    return sparse({0x80018000: struct.pack("<I", 0xAC4A0010), 0x80020000: struct.pack("<I", word),
                   0x80030000: struct.pack("<I", 0x8FB3002C), P2_SCENE: struct.pack("<I", P2_TEXT_AT),
                   P2_WINDOW: struct.pack("<I", P2_OBJECT_AT), P2_UP: struct.pack("<I", up),
                   P2_TEXT_AT: scene + bytes(64), P2_OBJECT_AT: bytes(window)}), len(first)


def p2_reader(read):
    r = rd.Reader(read, platform="ps1", game_id="SLPS-02100",
                  data={"tbl": (ROOT / "games/ps1/SLPS-02100.tbl").read_bytes()})
    r.load(bundle(ROOT / "games/ps1/SLPS-02100.lua"), "SLPS-02100.lua")
    r.take()
    return r


def test_persona_2_the_message_the_game_is_reading():
    read, first = p2_memory(4)
    with p2_reader(read) as r:
        r.frame(1)
        r.read = p2_memory(first)[0]                    # resting on the first message's end code
        r.frame(2)
        (event,) = r.take("text")
        assert event["text"] == "きょうは\nあめ"         # the wide space at the start and the codes are gone
        r.read = p2_memory(first + 4)[0]                # into the second
        r.frame(3)
        (event,) = r.take("text")
        assert event["text"] == "あした" + p2_gap() + "も"
        r.read = p2_memory(first + 4, up=0)[0]          # the window goes: nothing, and the message again after
        r.frame(4)
        r.read = p2_memory(first + 4)[0]
        r.frame(5)
        assert len(r.take("text")) == 1
        r.read = p2_memory(first + 4, kind=0x1234)[0]   # another kind of window
        r.frame(6)
        assert r.take("text") == []


def p2_gap() -> str:
    from yomikata import tbl
    return tbl.parse((ROOT / "games/ps1/SLPS-02100.tbl").read_text(encoding="utf-8")).chars[bytes.fromhex("1F11")]


def test_persona_2_another_build_and_a_missing_table():
    with rd.Reader(p2_memory(0, word=0x12345678)[0], platform="ps1", game_id="SLPS-02100",
                   data={"tbl": b"0001=a\n"}) as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-02100.lua"))
    with rd.Reader(p2_memory(0)[0], platform="ps1", game_id="SLPS-02100") as r:
        with pytest.raises(rd.ScriptError, match="SLPS-02100.tbl is missing"):
            r.load(bundle(ROOT / "games/ps1/SLPS-02100.lua"))


def test_tbl_module():
    source = "# a comment\n0100=学\n0101=校\n/FFFF\n*FFFE\n;another\nbad line\n"
    with rd.Reader(sparse({}), data={"tbl": source.encode()}) as r:
        r.load(bundle_text('local tbl = require("lib.tbl")\nT = tbl.parse(reader.data("tbl"))\n'
                           'function trim(s) return tbl.trim_wide(s) end'))
        assert r.eval('T.chars["\\x01\\x00"], T.chars["\\x01\\x01"], T.ends["\\xFF\\xFF"], T.newlines["\\xFF\\xFE"]') == \
            "学\t校\ttrue\ttrue"
        assert r.eval('trim("　　あ　い　"), trim("　"), trim("")') == "あ　い\t\t"
