"""The catalog's scripts and lib/ modules, run by the reader runtime on made-up memory. No game's text is here:
the messages below are written for the tests, in the form the scripts' notes describe."""

import hashlib
import json
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


# ---- every script of the catalog, and what is published of it ----

from yomikata import catalog                    # noqa: E402
from yomikata.bundle import header              # noqa: E402

SCRIPTS = catalog.scripts(ROOT)


def test_the_catalog_has_scripts_and_they_are_in_order():
    assert SCRIPTS and catalog.validate_scripts(ROOT) == []
    assert catalog.stale(ROOT) == []            # index.json and scripts/ are what `yomikata index` writes


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.stem)
def test_each_script_bundles_and_loads(path):
    """On memory with nothing in it a script must stop with its own "another build" error: so it parses, its
    modules and data files are found, it gets as far as its build check, and that check works."""
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))["scripts"][path.stem]
    published = (ROOT / index["path"]).read_text(encoding="utf-8")
    assert published == bundle(path, ROOT)
    assert hashlib.sha256(published.encode("utf-8")).hexdigest() == index["sha256"]
    assert header(published) == rd.header(published) == header(path.read_text(encoding="utf-8"))
    assert index["builds"] and index["platform"] == path.parent.name
    assert 1 <= index["runtime"] <= rd.script_level()
    assert catalog.SCRIPT_ID.fullmatch(path.stem)                # keyed as the entries are: by release id
    data = {}
    for name, entry in index["data"].items():
        raw = (ROOT / entry["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
        data[name] = raw
    with rd.Reader(sparse({}), platform=path.parent.name, game_id=path.stem, data=data) as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(published, path.name)
        r.frame(1)
        assert not [e for e in r.take("text")]


def write_script(root, name="games/gc/GXXJ01.lua", body="reader.log('x')", lines=None):
    lines = lines if lines is not None else ["title あ (A)", "reads the window", "version 1.00", "build GXXJ01-00", "no hooks"]
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"-- reader: {line}\n" for line in lines) + "\n" + body + "\n", encoding="utf-8")
    return path


def test_a_script_out_of_form_is_told(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/pages.lua").write_text("return {}", encoding="utf-8")
    write_script(tmp_path)
    assert catalog.validate_scripts(tmp_path) == []
    write_script(tmp_path, body='local p = require("lib.pages")\nlocal t = reader.data("tbl")')
    assert [p.message for p in catalog.validate_scripts(tmp_path)] == ['reader.data("tbl") needs games/gc/GXXJ01.tbl']
    (tmp_path / "games/gc/GXXJ01.tbl").write_text("0001=a\n", encoding="utf-8")
    assert catalog.validate_scripts(tmp_path) == []
    for body, lines, said in (
            ("reader.hook(0x80003100, function() end)", None, "reader.hook"),
            ("mem.w32(0x80000000, 1)", None, "never writes"),
            ('require("lib.nothing")', None, "lib.nothing"),
            ("reader.log('x')", ["title あ (A)", "reads x", "version 1", "no hooks"], "reader: build"),
            ("reader.log('x')", ["title あ (A)", "reads x", "version 1", "build X-00", "no hooks", "runtime two"], "whole number"),
            ("reader.log('x')", ["title あ (A)", "reads x", "version 1", "build X-00"], "reader: no hooks")):
        write_script(tmp_path, body=body, lines=lines)
        assert any(said in p.message for p in catalog.validate_scripts(tmp_path)), said
    write_script(tmp_path)
    write_script(tmp_path, name="games/gc/my game.lua")
    assert any("named after" in p.message for p in catalog.validate_scripts(tmp_path))


def test_the_index_lists_scripts_and_publishes_them_with_their_modules(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/pages.lua").write_text("return { n = 1 }", encoding="utf-8")
    script = write_script(tmp_path, body='local p = require("lib.pages")\nlocal t = reader.data("tbl")',
                          lines=["title あ (A)", "reads the window", "version 1.00", "build GXXJ01-00",
                                 "build GXXJ01-01", "no hooks", "needs vram"])
    (tmp_path / "games/gc/GXXJ01.tbl").write_text("0001=a\n", encoding="utf-8")
    assert catalog.stale(tmp_path) == ["index.json", "scripts/gc/GXXJ01.lua"]
    assert catalog.write_index(tmp_path) is True and catalog.stale(tmp_path) == []
    listed = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))["scripts"]["GXXJ01"]
    published = (tmp_path / "scripts/gc/GXXJ01.lua").read_text(encoding="utf-8")
    assert listed["path"] == "scripts/gc/GXXJ01.lua" and listed["platform"] == "gc" and listed["runtime"] == 1
    assert listed["sha256"] == hashlib.sha256(published.encode("utf-8")).hexdigest()
    assert listed["builds"] == ["GXXJ01-00", "GXXJ01-01"] and listed["needs"] == ["vram"]
    assert listed["title"] == "あ (A)" and listed["reads"] == "the window"
    assert listed["data"]["tbl"]["path"] == "games/gc/GXXJ01.tbl"
    assert published.startswith("-- reader: title あ (A)\n") and '__module["lib.pages"]' in published
    # A module changes: the published script is out of date, though its own source isn't.
    (tmp_path / "lib/pages.lua").write_text("return { n = 2 }", encoding="utf-8")
    assert catalog.stale(tmp_path) == ["index.json", "scripts/gc/GXXJ01.lua"]
    assert catalog.write_index(tmp_path) is True and catalog.write_index(tmp_path) is False
    # A script that is gone takes its published file and its place in the index with it.
    script.unlink()
    assert catalog.stale(tmp_path) == ["index.json", "scripts/gc/GXXJ01.lua"]
    catalog.write_index(tmp_path)
    assert not (tmp_path / "scripts/gc/GXXJ01.lua").exists()
    assert "scripts" not in json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))


def test_header_lines_are_read_as_the_runtime_reads_them():
    source = ("-- reader: title 架空の物語 (A Made-up Story)\n--reader:   version 1.00\n-- reader: build 0F1E\n"
              "-- reader: no   hooks\n-- reader: no layout the window's place isn't found\n-- reader: needs vram oam\n"
              "\n-- a comment\n-- reader:\nlocal x = 1\n-- reader: api 3\n")
    assert header(source) == rd.header(source)
    assert ("no hooks", "") in header(source) and ("needs", "vram oam") in header(source)


# ---- games/ps1/SLPS-01100.lua ----

TOD_TEXT, TOD_READING, TOD_STATE = 0x801C1870, 0x801BEA34, 0x801BEA3A


def tod_memory(reading: int | None, state: int = 3, call: int = 0x0C03DFA3):
    pages = ["せんいん\n「みまわりも　たいくつですね」", "かんちょう\n「そういうな」", "「もうすぐだ」"]
    stretch = b"\x0c".join(p.encode("cp932") for p in pages) + b"\0"
    starts = [0, len(pages[0].encode("cp932")) + 1]
    at = 0 if reading is None else TOD_TEXT + starts[reading] + 4 if reading < 2 else TOD_TEXT + len(stretch) - 4
    return sparse({0x800B0840: struct.pack("<I", call), 0x80161B20: struct.pack("<I", 0x0C0586CF),
                   0x80123B38: struct.pack("<I", 0x0C04A19D), TOD_TEXT: stretch + bytes(32),
                   TOD_READING: struct.pack("<I", at), TOD_STATE: struct.pack("<H", state)}), pages


def test_tales_of_destiny_the_page_the_game_is_on():
    read, pages = tod_memory(0)
    with rd.Reader(read, platform="ps1", game_id="SLPS-01100") as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-01100.lua"), "SLPS-01100.lua")
        r.take()
        seen = []
        for frame, (reading, state) in enumerate([(0, 3), (0, 4), (1, 3), (1, 4), (2, 3), (None, 3), (None, 0), (0, 3)], 1):
            r.read = tod_memory(reading, state)[0]
            r.frame(frame)
            seen += [(frame, e["text"]) for e in r.take("text")]
        # Each page once as the window gets to it; with the pointer at 0 the stretch's last page stays; after
        # the window has gone the first page is said again.
        assert seen == [(1, pages[0]), (3, pages[1]), (5, pages[2]), (8, pages[0])]
    with rd.Reader(tod_memory(0, call=0x0C000000)[0], platform="ps1", game_id="SLPS-01100") as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-01100.lua"))


# ---- games/ps1/SLPS-02785.lua (Tsumi's engine, lib/persona2.lua) ----

def test_persona_2_batsu_reads_as_tsumi_does_with_its_own_codes():
    from yomikata import tbl
    table = tbl.parse((ROOT / "games/ps1/SLPS-02785.tbl").read_text(encoding="utf-8"))
    back = {}
    for code, ch in table.chars.items():
        back.setdefault(ch, code)
    codes = lambda s: b"".join(back[ch] for ch in s)        # noqa: E731
    # A control with three argument words inside the first message, a name by code, then a second message.
    first = codes("きょう") + bytes.fromhex("2814 0100 0200 0300") + codes("は") + bytes.fromhex("2011 0111") + codes("あめ")
    scene = first + bytes.fromhex("0311") + codes("あした") + bytes.fromhex("0311")

    def memory(reading, state=0x44, word=0x0C008999):
        window = bytearray(0x20)
        struct.pack_into("<H", window, 0x04, 0x5290)
        struct.pack_into("<I", window, 0x0C, 0x80150000 + reading)
        window[0x14] = state
        return sparse({0x80022A90: struct.pack("<I", word), 0x8003236C: struct.pack("<I", 0x0C00CC16),
                       0x8004A498: struct.pack("<I", 0x0C011C00), 0x800EC014: struct.pack("<I", 0x80150000),
                       0x8007CEA0: struct.pack("<I", 0x80170000), 0x80150000: scene + bytes(64),
                       0x80170000: bytes(window)})

    data = {"tbl": (ROOT / "games/ps1/SLPS-02785.tbl").read_bytes()}
    with rd.Reader(memory(len(first)), platform="ps1", game_id="SLPS-02785", data=data) as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-02785.lua"), "SLPS-02785.lua")
        r.take()
        r.frame(1)
        r.read = memory(len(first) + 4)
        r.frame(2)
        r.read = memory(len(first) + 4, state=0x40)         # the bit that says a message is up is clear
        r.frame(3)
        r.read = memory(len(first) + 4)
        r.frame(4)
        assert [e["text"] for e in r.take("text")] == ["きょうは\nあめ", "あした", "あした"]
    with rd.Reader(memory(0, word=1), platform="ps1", game_id="SLPS-02785", data=data) as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-02785.lua"))


def test_a_scripts_table_is_its_entrys_tables_in_order(tmp_path):
    root = tmp_path
    (root / "schema").mkdir()
    (root / "schema/entry.schema.json").write_text((ROOT / "schema/entry.schema.json").read_text(encoding="utf-8"), encoding="utf-8")
    for name, text in (("tables/ps1/A.tbl", "0001=a\n"), ("tables/ps1/B.tbl", "0002=b\n")):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    (root / "games/ps1").mkdir(parents=True)
    (root / "games/ps1/ZQ-0001.json").write_text(json.dumps({
        "id": "ZQ-0001", "platform": "ps1", "title": "つくりもの", "encoding": "table", "status": "partial",
        "tables": ["tables/ps1/A.tbl", "tables/ps1/B.tbl"], "unit": 2}), encoding="utf-8")
    (root / "games/ps1/ZQ-0001.tbl").write_text("0001=a\n0002=b\n", encoding="utf-8")
    assert catalog.validate(root) == []
    (root / "games/ps1/ZQ-0001.tbl").write_text("0001=a\n0002=c\n", encoding="utf-8")
    assert ["one after another" in p.message for p in catalog.validate(root)] == [True]


# ---- games/ps1/SLPS-03292.lua ----

def mo2_memory(rows=(), name="", answers=(), choosing=0, count=0, cursor=-1, mode=1, word=0x0C006FE2):
    def block(lines, n):
        out = bytearray(44 * n)
        for i, line in enumerate(lines):
            raw = line.encode("cp932")
            out[44 * i:44 * i + len(raw)] = raw
        return bytes(out)
    return sparse({0x80024D20: struct.pack("<I", word), 0x80036470: struct.pack("<I", 0x0C010F1E),
                   0x80044B50: struct.pack("<I", 0x0C0102C9), 0x8009115C: struct.pack("<I", mode),
                   0x8010B5C2: name.encode("cp932") + bytes(44 - len(name.encode("cp932"))) + block(rows, 3),
                   0x8010BC22: struct.pack("<Bb", choosing, cursor), 0x8010BCA0: bytes([count]),
                   0x8010BCAC: block(answers, 10)})


def test_memories_off_2nd_rows_name_and_a_choice():
    full = "あ" * 22                                             # a full row has no ending zero
    with rd.Reader(mo2_memory(), platform="ps1", game_id="SLPS-03292") as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-03292.lua"), "SLPS-03292.lua")
        r.take()
        steps = [mo2_memory([full, "つづき"], "はかせ"), mo2_memory([full, "つづき"], "はかせ"),
                 mo2_memory(["どうする？"], "", ["いく", "いかない", "まえのこたえ"], choosing=1, count=2),
                 mo2_memory(["どうする？"], "", ["いく", "いかない", "まえのこたえ"], choosing=1, count=2, cursor=1),
                 mo2_memory(["そうか"], "はかせ", ["いく", "いかない", "まえのこたえ"], count=2),
                 mo2_memory(["そうか"], "はかせ", mode=4), mo2_memory(["そうか"], "はかせ")]
        events = []
        for frame, read in enumerate(steps, 1):
            r.read = read
            r.frame(frame)
            events += [(frame, kind, e) for kind, e in r.take() if kind in ("text", "cursor")]
        texts = [(f, e["channel"], e["speaker"], e["text"]) for f, k, e in events if k == "text"]
        assert texts == [(1, "dialogue", "はかせ", full + "\nつづき"), (3, "dialogue", "", "どうする？"),
                         (3, "choice", "", "いく\nいかない"), (5, "dialogue", "はかせ", "そうか"),
                         (7, "dialogue", "はかせ", "そうか")]             # after the movie the line is said again
        cursors = [(f, e.get("index", "closed")) for f, k, e in events if k == "cursor"]
        assert cursors == [(3, -1), (4, 1), (5, "closed")]
    with rd.Reader(mo2_memory(word=1), platform="ps1", game_id="SLPS-03292") as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-03292.lua"))


# ---- games/ps1/SLPS-02296.lua ----

def mo_memory(rows=(), lengths=(), name="", named=0, shown=1, word=0x0C006A04):
    block = bytearray(57 * 3)
    for i, row in enumerate(rows):
        raw = row.encode("cp932")
        block[57 * i:57 * i + len(raw)] = raw
    return sparse({0x800244F4: struct.pack("<I", word), 0x8003B314: struct.pack("<I", 0x0C00FEB1),
                   0x8004AC04: struct.pack("<I", 0x0C012CB3), 0x800F6018: bytes(block),
                   0x800F7220: bytes(list(lengths) + [0] * (3 - len(lengths))), 0x80074BA2: bytes([shown]),
                   0x800F3750: name.encode("cp932") + bytes(16), 0x8007253D: bytes([named])})


def test_memories_off_rows_cut_by_their_lengths_and_the_name_plate():
    with rd.Reader(mo_memory(), platform="ps1", game_id="SLPS-02296") as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-02296.lua"), "SLPS-02296.lua")
        r.take()
        # Rows are written over and not ended: what is left of a longer line stands after the new one.
        steps = [mo_memory(["きょうはいいてんきだ", "そうだね　　　"], [10, 7], "ゆえ", 1),
                 mo_memory(["あしたは？いてんきだ", "そうだね　　　"], [5, 0], "ゆえ", 0),
                 mo_memory(["あしたは？いてんきだ", "そうだね　　　"], [5, 0], "ゆえ", 0, shown=0),
                 mo_memory(["あしたは？いてんきだ"], [5], "ゆえ", 1)]
        seen = []
        for frame, read in enumerate(steps, 1):
            r.read = read
            r.frame(frame)
            seen += [(frame, e["speaker"], e["text"]) for e in r.take("text")]
        assert seen == [(1, "ゆえ", "きょうはいいてんきだ\nそうだね"), (2, "", "あしたは？"), (4, "ゆえ", "あしたは？")]
    with rd.Reader(mo_memory(word=1), platform="ps1", game_id="SLPS-02296") as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-02296.lua"))


# ---- games/ps1/SLPS-03050.lua ----

def toe_memory(line: bytes = b"", name="", state=3, word=0x0C02131F):
    return sparse({0x8002BA74: struct.pack("<I", word), 0x8003D020: struct.pack("<I", 0x0C00CE26),
                   0x8004A060: struct.pack("<I", 0x0C0126DF), 0x801F7878: line + bytes(512 - len(line)),
                   0x800D3686: struct.pack("<H", state), 0x800D367C: struct.pack("<I", 0x801E0000),
                   0x801E0000: name.encode("cp932") + bytes(8)})


def test_tales_of_eternia_a_line_with_codes_whose_second_byte_is_zero():
    sj = lambda s: s.encode("cp932")        # noqa: E731
    # Bold on (07 01), a word, bold off (07 00: the zero is the code's, not the line's end), a colour (03 02).
    line = sj("やっぱり") + b"\x07\x01" + sj("おまえ") + b"\x07\x00" + sj("か") + b"\x03\x02" + sj("！") + b"\x00" + sj("のこり")
    with rd.Reader(toe_memory(), platform="ps1", game_id="SLPS-03050") as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-03050.lua"), "SLPS-03050.lua")
        r.take()
        steps = [toe_memory(line, "つくりもの", state=1), toe_memory(line, "つくりもの"), toe_memory(line, "つくりもの"),
                 toe_memory(line, "つくりもの", state=2), toe_memory(sj("べつのぎょう") + b"\x00", "ほかのひと")]
        seen = []
        for frame, read in enumerate(steps, 1):
            r.read = read
            r.frame(frame)
            seen += [(frame, e["speaker"], e["text"]) for e in r.take("text")]
        assert seen == [(2, "つくりもの", "やっぱりおまえか！"), (5, "ほかのひと", "べつのぎょう")]
    with rd.Reader(toe_memory(word=1), platform="ps1", game_id="SLPS-03050") as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-03050.lua"))


# ---- games/psp/NPJH50372.lua ----

def dr_memory(rows=(), word=0x0E2047EF):
    block = bytearray(108 * 5)
    for i, row in enumerate(rows):
        raw = row.encode("utf-16-le")
        block[108 * i:108 * i + len(raw)] = raw
    return sparse({0x088411DC: struct.pack("<I", word), 0x08873528: struct.pack("<I", 0x0E21C67C),
                   0x0889F514: struct.pack("<I", 0x0E20126D), 0x08C9A264: bytes(block)})


def test_danganronpa_the_windows_rows():
    full = "あ" * 54                                             # a full row has no ending zero
    with rd.Reader(dr_memory(), platform="psp", game_id="NPJH50372") as r:
        r.load(bundle(ROOT / "games/psp/NPJH50372.lua"), "NPJH50372.lua")
        r.take()
        seen = []
        for frame, rows in enumerate([[full, "つづき"], [full, "つづき"], [], ["つぎ", "", "みっつめ"], [full, "つづき"]], 1):
            r.read = dr_memory(rows)
            r.frame(frame)
            seen += [(frame, e["text"]) for e in r.take("text")]
        assert seen == [(1, full + "\nつづき"), (4, "つぎ\n\nみっつめ"), (5, full + "\nつづき")]
    with rd.Reader(dr_memory(word=1), platform="psp", game_id="NPJH50372") as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/psp/NPJH50372.lua"))


# ---- games/ps1/SLPS-00097.lua ----

def sui_memory(rows=(), is_open=1, mark=0, window=0x80189A90, word=0x0B2D0B6F):
    from yomikata import tbl
    table = tbl.parse((ROOT / "games/ps1/SLPS-00097.tbl").read_text(encoding="utf-8"))
    back = {}
    for code, ch in table.chars.items():
        back.setdefault(ch, code)
    block = bytearray(back["　"] * 80)                        # four rows of twenty blank cells
    for i, row in enumerate(rows):
        raw = b"".join(back[ch] for ch in row)
        block[40 * i:40 * i + len(raw)] = raw
    record = bytearray(0x20)
    record[0x08] = is_open
    struct.pack_into("<I", record, 0x18, 0x80190000)
    return sparse({0x8009A000: struct.pack("<I", word), 0x8009C000: struct.pack("<I", 0xF4D3F491),
                   0x80095000: struct.pack("<I", 0x040107FF), 0x8017DBC0: struct.pack("<I", window),
                   0x80189A90: bytes(record), 0x80190000: bytes(block), 0x801BA436: bytes([mark])})


def test_gensou_suikoden_a_line_is_said_when_it_is_whole():
    data = {"tbl": (ROOT / "games/ps1/SLPS-00097.tbl").read_bytes()}
    with rd.Reader(sui_memory(window=0), platform="ps1", game_id="SLPS-00097", data=data) as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-00097.lua"), "SLPS-00097.lua")
        r.take()
        seen, frame = [], 0

        def run(read, frames=1):
            nonlocal frame
            r.read = read
            for _ in range(frames):
                frame += 1
                r.frame(frame)
                seen.extend((frame, e["text"]) for e in r.take("text"))
        run(sui_memory(window=0), 3)                                  # before the first window: nothing to read
        run(sui_memory(["はかせ", "「どう"]), 5)                        # typing
        run(sui_memory(["はかせ", "「どうした"]), 5)
        run(sui_memory(["はかせ", "「どうした"], mark=24), 3)           # the wait mark: the line is whole
        run(sui_memory(["はかせ", "「どうした"], is_open=0), 2)         # closed; the text stays in memory
        run(sui_memory(["いくぞ"]), 40)                               # a line the game moves on from by itself
        assert seen == [(14, "はかせ\n「どうした"), (49, "いくぞ")]     # thirty frames after it stood still
    with rd.Reader(sui_memory(word=1), platform="ps1", game_id="SLPS-00097", data=data) as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-00097.lua"))


# ---- games/ps1/SLPM-86168.lua ----

def sui2_memory(rows=(), name="", state=0, choosing=0, cursor=0, window=0x8001B540, word=0x0C023835):
    from yomikata import tbl
    table = tbl.parse((ROOT / "games/ps1/SLPM-86168.tbl").read_text(encoding="utf-8"))
    back = {}
    for code, ch in table.chars.items():
        back.setdefault(ch, code)
    cells = lambda s: b"".join(back[ch] for ch in s)        # noqa: E731
    block = bytearray(back["　"] * 92)                   # four rows of twenty-three blank cells
    for i, row in enumerate(rows):
        block[46 * i:46 * i + 2 * len(row)] = cells(row)
    record = bytearray(0xA0)
    record[0x21], record[0x0C], record[0x9F] = state, choosing, cursor
    struct.pack_into("<I", record, 0x64, 0x80190000)
    struct.pack_into("<I", record, 0x80, 0x80191000)
    return sparse({0x8007F764: struct.pack("<I", word), 0x8008FE00: struct.pack("<I", 0x0C024047),
                   0x800BBE5C: struct.pack("<I", 0x0C01D278), 0x8002590C: struct.pack("<I", window),
                   0x8001B540: bytes(record), 0x80190000: bytes(block),
                   0x80191000: cells(name) + back["　"] * 2 + bytes(4)})


def test_gensou_suikoden_2_lines_names_and_a_question():
    data = {"tbl": (ROOT / "games/ps1/SLPM-86168.tbl").read_bytes()}
    with rd.Reader(sui2_memory(window=0xFFD1009E), platform="ps1", game_id="SLPM-86168", data=data) as r:
        r.load(bundle(ROOT / "games/ps1/SLPM-86168.lua"), "SLPM-86168.lua")
        r.take()
        steps = [sui2_memory(window=0xFFD1009E),                          # the title: no address there
                 sui2_memory(["「もうきがえ"], "はかせ"),                   # typing
                 sui2_memory(["「もうきがえたのか」"], "はかせ", state=1),   # waiting for a press
                 sui2_memory(["「もうきがえたのか」"], "はかせ", state=1),
                 sui2_memory(["いく", "いかない"], "はかせ", choosing=1),    # the answers, in the same rows
                 sui2_memory(["いく", "いかない"], "はかせ", choosing=1, cursor=1),
                 sui2_memory(window=0)]
        events = []
        for frame, read in enumerate(steps, 1):
            r.read = read
            r.frame(frame)
            events += [(frame, kind, e) for kind, e in r.take() if kind in ("text", "cursor")]
        texts = [(f, e["channel"], e["speaker"], e["text"]) for f, k, e in events if k == "text"]
        assert texts == [(3, "dialogue", "はかせ", "「もうきがえたのか」"), (5, "choice", "", "いく\nいかない")]
        assert [(f, e.get("index", "closed")) for f, k, e in events if k == "cursor"] == [(5, 0), (6, 1), (7, "closed")]
    with rd.Reader(sui2_memory(word=1), platform="ps1", game_id="SLPM-86168", data=data) as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPM-86168.lua"))


# ---- games/ps1/SCPS-10088.lua ----

def boku_memory(page: int | None, word=0x0C014DA1):
    from yomikata import tbl
    table = tbl.parse((ROOT / "games/ps1/SCPS-10088.tbl").read_text(encoding="utf-8"))
    back = {}
    for code, ch in table.chars.items():
        back.setdefault(ch, code)
    cells = lambda s: b"".join(back[ch] for ch in s)        # noqa: E731
    new_line, end_page, end_text = bytes.fromhex("0180"), bytes.fromhex("0280"), bytes.fromhex("0080")
    first = cells("ちち「こんにちは") + new_line + cells("　よろしく」")
    script = first + end_page + bytes.fromhex("3412") + cells("ぼく「はい」") + end_text
    at = [0x80118000, 0x80118000 + len(first) + 4][page] if page is not None else 0
    return sparse({0x8001E6B8: struct.pack("<I", word), 0x8003AF74: struct.pack("<I", 0x0C005779),
                   0x8004E718: struct.pack("<I", 0x0C0138C7), 0x800359EC: struct.pack("<I", at),
                   0x80118000: script + bytes(600)})


def test_boku_no_natsuyasumi_the_page_the_pointer_is_on():
    data = {"tbl": (ROOT / "games/ps1/SCPS-10088.tbl").read_bytes()}
    with rd.Reader(boku_memory(None), platform="ps1", game_id="SCPS-10088", data=data) as r:
        r.load(bundle(ROOT / "games/ps1/SCPS-10088.lua"), "SCPS-10088.lua")
        r.take()
        seen = []
        for frame, page in enumerate([None, 0, 0, 1, None, 1], 1):
            r.read = boku_memory(page)
            r.frame(frame)
            seen += [(frame, e["text"]) for e in r.take("text")]
        # A row after the first loses its wide space; the word after a page's end isn't text.
        assert seen == [(2, "ちち「こんにちは\nよろしく」"), (4, "ぼく「はい」"), (6, "ぼく「はい」")]
    with rd.Reader(boku_memory(0, word=1), platform="ps1", game_id="SCPS-10088", data=data) as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SCPS-10088.lua"))


# ---- lib/inplace.lua, against the layout reader it follows ----

def test_inplace_page_reads_as_the_layout_reader_does():
    """Made-up scripts of marks, texts, end codes, page breaks and codes with arguments, read at every position
    by lib/inplace.lua and by layout.py's `_script`: the two must give the same pieces. Also with `rest`, where
    a position a few codes past an end code is read as just past it."""
    import random
    from yomikata import layout
    rng = random.Random(7)
    end, page, arg = bytes.fromhex("FFFF"), bytes.fromhex("FFFC"), bytes.fromhex("FFF9")
    lua = ('local inplace = require("lib.inplace")\n'
           'local game = { unit = 2, ends = { ["\\xFF\\xFF"] = true }, breaks = { ["\\xFF\\xFC"] = true },\n'
           '               newlines = {}, silent = {}, sizes = { ["\\xFF\\xF9"] = 4 } }\n'
           'local function unhex(h) return (h:gsub("%x%x", function(p) return string.char(tonumber(p, 16)) end)) end\n'
           'function page(both, size, mark, rows, rest)\n'
           '    local out = {}\n'
           '    game.rest = rest\n'
           '    for i, piece in ipairs(inplace.page(unhex(both), size, game, unhex(mark), rows)) do\n'
           '        out[i] = (piece:gsub(".", function(c) return string.format("%02x", c:byte()) end))\n'
           '    end\n'
           '    return #out .. ":" .. table.concat(out, "|")\n'
           'end')
    checked = 0
    with rd.Reader(sparse({})) as r:
        r.load(bundle_text(lua))
        for mark, rows, rest in ((bytes.fromhex("8000"), False, 0), (bytes.fromhex("000001"), True, 0),
                                 (bytes.fromhex("8000"), False, 3)):
            for _ in range(40):
                script = bytearray()
                for _ in range(rng.randint(2, 6)):
                    script += bytes(rng.choice((0x12, 0x34, 0x00)) for _ in range(2 * rng.randint(0, 2)))     # instructions
                    script += mark
                    for _ in range(rng.randint(0, 5)):
                        script += rng.choice((b"\x01\x02", b"\x03\x04", page, arg + b"\x00\x1e", b"\x05\x06"))
                    script += end * rng.choice((1, 1, 2))
                    if rng.random() < 0.3:
                        script += mark + b"\x07\x08" + end                                                # carries on
                for size in range(0, len(script) + 1, 1 if len(mark) == 3 else 2):
                    both = bytes(script[:size + len(mark)])
                    want = layout._script(both, size, 2, mark, {end}, {page}, {arg: 4}, rows, rest) if size else []
                    got = r.eval(f'page("{both.hex()}", {size}, "{mark.hex()}", {"true" if rows else "false"}, {rest or "nil"})') if size else "0:"
                    assert got == f"{len(want)}:" + "|".join(w.hex() for w in want), (both.hex(), size, mark.hex(), rest)
                    checked += 1
    assert checked > 1500


# ---- games/ps1/SLPS-01001.lua ----

def moon_memory(offset: int, state: int, up: int = 0x80, cursor: int = 0, labels: int = 36, word=0x0C005CF9):
    from yomikata import tbl
    table = tbl.parse((ROOT / "games/ps1/SLPS-01001.kana").read_text(encoding="utf-8"))
    back = {}
    for code, ch in table.chars.items():
        back.setdefault(ch, code)
    cells = lambda s: b"".join(back[ch] for ch in s)        # noqa: E731
    script = (b"\x12\x34" + b"\x80\x00" + cells("きょうは") + b"\xFF\xFD" + cells("あめ") + b"\xFF\xFF"
              + b"\x80\x07" + b"\x00\x00\x01" + cells("いく") + b"\xFF\xFF" + b"\x00\x00\x01" + cells("いかない") + b"\xFF\xFF")
    return sparse({0x80017278: struct.pack("<I", word), 0x80021C74: struct.pack("<I", 0x0C009385),
                   0x8002FD3C: struct.pack("<I", 0x0C00BE00), 0x8008BEF0: struct.pack("<III", 0x80150000, offset, labels),
                   0x8008BF00: struct.pack("<i", state), 0x8008F0E4: bytes([up]), 0x8008E33C: bytes([cursor]),
                   0x80150000: script + bytes(64)}), script


def moon_data():
    return {p.suffix[1:]: p.read_bytes() for p in (ROOT / "games/ps1").glob("SLPS-01001.*")
            if p.suffix not in (".lua", ".json")}


def test_moonlight_syndrome_what_the_page_gains_and_a_choice():
    _, script = moon_memory(0, 0)
    text_end = script.index(b"\xFF\xFF") + 2
    with rd.Reader(moon_memory(0, 0)[0], platform="ps1", game_id="SLPS-01001", data=moon_data()) as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-01001.lua"), "SLPS-01001.lua")
        r.take()
        steps = [(8, 1, 0x80, 0), (12, 2, 0x80, 0),               # typing; then the window waits with one row up
                 (text_end, 1, 0x80, 0), (text_end, 2, 0x80, 0),  # the second row types; the window waits again
                 (len(script), 4, 0, 0), (len(script), 4, 0, 1),  # the choice: the window's flag is down
                 (len(script), 5, 0, 1)]
        events = []
        for frame, (offset, state, up, cursor) in enumerate(steps, 1):
            r.read = moon_memory(offset, state, up, cursor)[0]
            r.frame(frame)
            events += [(frame, kind, e) for kind, e in r.take() if kind in ("text", "cursor")]
        texts = [(f, e["channel"], e["text"]) for f, k, e in events if k == "text"]
        assert texts == [(2, "dialogue", "きょうは"), (4, "dialogue", "あめ"), (5, "choice", "いく\nいかない")]
        assert [(f, e.get("index", "closed")) for f, k, e in events if k == "cursor"] == [(5, 0), (6, 1), (7, "closed")]
    # A chapter the script has no table for: nothing is read. And another build.
    with rd.Reader(moon_memory(12, 2, labels=999)[0], platform="ps1", game_id="SLPS-01001", data=moon_data()) as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-01001.lua"))
        r.take()
        r.frame(1)
        assert r.take("text") == []
    with rd.Reader(moon_memory(0, 0, word=1)[0], platform="ps1", game_id="SLPS-01001", data=moon_data()) as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-01001.lua"))


# ---- games/ps1/SLPS-00102.lua, with lib/twilight.lua ----

TW = {"offset": 0x800A4CEA, "shown": 0x800A4CEE, "state": 0x800A4CE4, "cursor": 0x80094D28}


def tw_memory(text: str = "", read: int | None = None, state: int = 0, cursor: int = 0, past: int = 0,
              chapter: int = 0xAFBF0020, word: int = 0x0C004333):
    """A script buffer with `text` after an instruction and the mark, read up to `read` of its characters (all
    of it and its end code when None) and `past` codes more."""
    from yomikata import tbl
    back = {}
    for name in ("tbl", "ch0"):
        for code, ch in tbl.parse((ROOT / f"games/ps1/SLPS-00102.{name}").read_text(encoding="utf-8")).chars.items():
            back.setdefault(ch, code)
    back["\n"] = bytes.fromhex("0281")
    codes = [back[ch] for ch in text]
    head = b"\x82\x00" + b"\x00\x89"
    body = b"".join(codes) + b"\xFF\xFF"
    at = len(head) + (len(body) if read is None else 2 * read) + 2 * past
    return sparse({0x80011A9C: struct.pack("<I", word), 0x80011A0C: struct.pack("<I", 0x34020004),
                   0x80011934: struct.pack("<I", 0x34020005), 0x8004AA1C: struct.pack("<I", chapter),
                   TW["offset"]: struct.pack("<H", at // 2), TW["shown"]: struct.pack("<H", 1 if text else 0),
                   TW["state"]: struct.pack("<H", state), TW["cursor"]: struct.pack("<H", cursor),
                   0x801FE800: head + body + b"\x82\x00" * 8})


def tw_data():
    return {p.suffix[1:]: p.read_bytes() for p in (ROOT / "games/ps1").glob("SLPS-00102.*")
            if p.suffix not in (".lua", ".json")}


def test_twilight_syndrome_the_page_as_it_grows_and_a_choice():
    line, more = "あい：うえ", "あい：うえ\nいう"
    question = "あ：どこ？\n：うえ\n\n：いえ"
    steps = [(line, 3, 0, 0, 0), (line, None, 0xFFFE, 0, 0),            # typing; then the window waits
             (more, 6, 0, 0, 0), (more, None, 0xFFFE, 0, 0),            # the page gains a row
             (question, None, 0xFFFF, 0, 4), (question, None, 0xFFFF, 1, 4),    # a choice, the game four codes on
             ("", None, 0, 0, 0)]
    with rd.Reader(tw_memory(), platform="ps1", game_id="SLPS-00102", data=tw_data()) as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-00102.lua"), "SLPS-00102.lua")
        r.take()
        events = []
        for frame, (shown, read, state, cursor, past) in enumerate(steps, 1):
            r.read = tw_memory(shown, read, state, cursor, past)
            r.frame(frame)
            events += [(frame, kind, e) for kind, e in r.take() if kind in ("text", "cursor")]
        texts = [(f, e["channel"], e["text"]) for f, k, e in events if k == "text"]
        assert texts == [(2, "dialogue", "あい：うえ"), (4, "dialogue", "いう"),
                         (5, "dialogue", "あ：どこ？"), (5, "choice", "うえ\nいえ")]
        assert [(f, e.get("index", "closed")) for f, k, e in events if k == "cursor"] == [(5, 0), (6, 1), (7, "closed")]
        # A line still typing is said once it has stood still for half a second.
        r.state_loaded()
        for frame in range(8, 48):
            r.read = tw_memory(line, 3, 0)
            r.frame(frame)
        assert [e["text"] for e in r.take("text")] == ["あい："]
    # A chapter the script doesn't know: nothing is read. And another build.
    with rd.Reader(tw_memory(line, None, 0xFFFE, chapter=1), platform="ps1", game_id="SLPS-00102", data=tw_data()) as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-00102.lua"))
        r.take()
        r.frame(1)
        assert r.take("text") == []
    with rd.Reader(tw_memory(word=1), platform="ps1", game_id="SLPS-00102", data=tw_data()) as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-00102.lua"))
    with rd.Reader(tw_memory(), platform="ps1", game_id="SLPS-00102", data={}) as r:
        with pytest.raises(rd.ScriptError, match="missing"):
            r.load(bundle(ROOT / "games/ps1/SLPS-00102.lua"))


# ---- games/ps1/SLPS-00405.lua: the same window, with its own codes ----

def kyu_memory(text: str = "", state: int = 0, cursor: int = 0, past: int = 0, word: int = 0x0C0042F5):
    from yomikata import tbl
    back = {}
    for name in ("tbl", "ch5"):
        for code, ch in tbl.parse((ROOT / f"games/ps1/SLPS-00405.{name}").read_text(encoding="utf-8")).chars.items():
            back.setdefault(ch, code)
    back["\n"] = bytes.fromhex("00A8")
    head = b"\x82\x00" + b"\x00\x89"
    body = b"".join(back[ch] for ch in text) + b"\xFF\xFF"
    return sparse({0x80010634: struct.pack("<I", word), 0x8001065C: struct.pack("<I", 0x0C0050A7),
                   0x80010664: struct.pack("<I", 0x0C004DCD), 0x80041C78: struct.pack("<I", 0xAFB200B8),
                   0x800AAABE: struct.pack("<H", (len(head) + len(body)) // 2 + past),
                   0x800AAAC2: struct.pack("<H", 1 if text else 0), 0x800AAAB8: struct.pack("<H", state),
                   0x800973D4: struct.pack("<H", cursor), 0x801FE800: head + body + b"\x82\x00" * 8})


def kyu_data():
    return {p.suffix[1:]: p.read_bytes() for p in (ROOT / "games/ps1").glob("SLPS-00405.*")
            if p.suffix not in (".lua", ".json")}


def test_twilight_syndrome_kyuumei_hen_a_line_and_a_choice_with_spaces_before_its_colons():
    steps = [("\n：あい", 0xFFFE, 0, 0),                              # the line break before a colon stays
             ("あ：どこ？\n\u3000：うえ\n\u3000\u3000：いえ", 0xFFFF, 1, 5), ("", 0, 0, 0)]
    with rd.Reader(kyu_memory(), platform="ps1", game_id="SLPS-00405", data=kyu_data()) as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-00405.lua"), "SLPS-00405.lua")
        r.take()
        events = []
        for frame, (shown, state, cursor, past) in enumerate(steps, 1):
            r.read = kyu_memory(shown, state, cursor, past)
            r.frame(frame)
            events += [(frame, kind, e) for kind, e in r.take() if kind in ("text", "cursor")]
        assert [(f, e["channel"], e["text"]) for f, k, e in events if k == "text"] == [
            (1, "dialogue", "\n：あい"), (2, "dialogue", "あ：どこ？"), (2, "choice", "うえ\nいえ")]
        assert [(f, e.get("index", "closed")) for f, k, e in events if k == "cursor"] == [(2, 1), (3, "closed")]
    with rd.Reader(kyu_memory(word=0x0C004333), platform="ps1", game_id="SLPS-00405", data=kyu_data()) as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-00405.lua"))


# ---- games/ps1/SLPS-00404.lua ----

def gakkou_cells(text: str) -> bytes:
    """Text as the game's cells: Shift-JIS codes as 16-bit numbers; "\\n" a line break, "|" a page's end, "^" a
    column the game wraps."""
    out = b""
    for ch in text:
        out += {"\n": b"\x0A\x0D", "|": b"\x01\xFF", "^": b"\x03\xFF"}.get(ch) or ch.encode("cp932")[::-1]
    return out


def gakkou_memory(typed: str = "", count: int | None = None, waiting: int = 0, question: str = "", answers=(),
                  listed: int = 0, ready: int = 0, cursor: int = 0, word: int = 0x0C004FBB):
    cells = gakkou_cells(typed)
    memory = {0x80013DD4: struct.pack("<I", word), 0x800249E4: struct.pack("<I", 0x0C010910),
              0x8003933C: struct.pack("<I", 0x0C00E4E9),
              0x80061858: cells + bytes(64), 0x80060D18: struct.pack("<I", len(cells) // 2 if count is None else count),
              0x80060CFC: struct.pack("<I", 1 if typed else 0), 0x80060D00: struct.pack("<I", waiting),
              0x80061058: gakkou_cells(question) + bytes(64),
              0x80060E94: struct.pack("<I", listed), 0x80060DDC: struct.pack("<I", ready),
              0x80060DE8: struct.pack("<I", cursor)}
    records = b""
    for i in range(10):
        at = 0x80120000 + 0x40 * i
        records += struct.pack("<I", at) + bytes(16)
        memory[at] = (answers[i] if i < len(answers) else "old").encode("cp932") + b"\0"
    memory[0x8015B5E0] = records
    return sparse(memory)


def test_gakkou_de_atta_kowai_hanashi_text_as_it_types_and_a_choice():
    first, both = "部室の^ドアを", "部室の^ドアを\n開けた。"
    steps = [dict(typed=both, count=3), dict(typed=both, count=7, waiting=2),      # typing; a wait at the row's end
             dict(typed=both, waiting=3),                                           # the page's end: what it gained
             dict(typed=both + "|", waiting=3),                                     # past its end code: still up
             dict(typed=both + "|行く？", waiting=2), dict(),                       # a new page; the window empties
             dict(typed="行く", question="行く？", answers=("行く",), listed=1),    # a choice's answers being listed
             dict(question="行く？", answers=("行く", "行かない"), listed=2, ready=1),
             dict(question="行く？", answers=("行く", "行かない"), listed=2, ready=1, cursor=1),
             dict(typed="そう。", waiting=3)]
    with rd.Reader(gakkou_memory(), platform="ps1", game_id="SLPS-00404") as r:
        r.load(bundle(ROOT / "games/ps1/SLPS-00404.lua"), "SLPS-00404.lua")
        r.take()
        events = []
        for frame, step in enumerate(steps, 1):
            r.read = gakkou_memory(**step)
            r.frame(frame)
            events += [(frame, kind, e) for kind, e in r.take() if kind in ("text", "cursor")]
        assert [(f, e["channel"], e["text"]) for f, k, e in events if k == "text"] == [
            (2, "dialogue", "部室のドアを"), (3, "dialogue", "開けた。"), (5, "dialogue", "行く？"),
            (8, "choice", "行く\n行かない"), (10, "dialogue", "そう。")]
        assert [(f, e.get("index", "closed")) for f, k, e in events if k == "cursor"] == [(8, 0), (9, 1), (10, "closed")]
        # A question that wasn't typed first is said with its choice; text still typing, once it stands still.
        r.state_loaded()
        r.read = gakkou_memory(question="どっち？", answers=("右", "左"), listed=2, ready=1)
        r.frame(11)
        assert [(e["channel"], e["text"]) for e in r.take("text")] == [("dialogue", "どっち？"), ("choice", "右\n左")]
        r.state_loaded()
        for frame in range(12, 52):
            r.read = gakkou_memory(typed=first, count=2)
            r.frame(frame)
        assert [e["text"] for e in r.take("text")] == ["部室"]
    with rd.Reader(gakkou_memory(word=1), platform="ps1", game_id="SLPS-00404") as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/ps1/SLPS-00404.lua"))

