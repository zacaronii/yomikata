import gzip
import json
import struct
from pathlib import Path

import pytest

from yomikata import catalog, layout, tbl
from yomikata.cli import main

REPO = Path(__file__).resolve().parents[2]
BASE = 0x08800000


def ram(size=0x1000) -> bytearray:
    return bytearray(size)


def put(buf: bytearray, addr: int, data: bytes) -> None:
    buf[addr - BASE:addr - BASE + len(data)] = data


def entry_with(lay: dict, **extra) -> dict:
    e = {"id": "ULJM00001", "platform": "psp", "title": "テスト", "encoding": "utf16le", "status": "auto",
         "versions": [{"exe_sha1": "a" * 40}], "layouts": [lay]}
    e.update(extra)
    return e


def test_fixed_message_flags_and_shown():
    m = ram()
    put(m, BASE + 0x100, "こんにちは、世界".encode("utf-16-le") + b"\0\0" + "残り".encode("utf-16-le"))
    put(m, BASE + 0x10, struct.pack("<H", 5))
    put(m, BASE + 0x12, b"\x01")
    put(m, BASE + 0x13, b"\x02")
    lay = {"message": {"address": "0x08800100", "charset": "utf16le", "max_bytes": 64, "terminator": "0000"},
           "shown": {"address": "0x08800010", "type": "u16"},
           "waiting": {"address": "0x08800012"},
           "choosing": {"address": "0x08800013", "equals": 1}}
    out = layout.read_layout(lay, entry_with(lay), layout.Ram(BASE, bytes(m)))
    assert out == {"message": "こんにちは、世界", "shown": 5, "waiting": True, "choosing": False,
                   "on_screen": "こんにちは"}


def test_pointer_chain_rows_and_speaker_names():
    m = ram()
    put(m, BASE + 0x20, struct.pack("<I", BASE + 0x200))          # base -> object
    put(m, BASE + 0x208, struct.pack("<I", BASE + 0x400))         # object+8 -> text block
    for i, line in enumerate(["一行目", "二行目", ""]):
        put(m, BASE + 0x404 + i * 0x20, line.encode("utf-16-le") + b"\0\0")
    put(m, BASE + 0x402, struct.pack("<H", 3))
    lay = {"message": {"base": "0x08800020", "offsets": ["0x8", "0x4"], "charset": "utf16le", "max_bytes": 0x20,
                       "terminator": "0000", "rows": {"count": 3, "stride": 0x20}},
           "speaker": {"base": "0x08800020", "offsets": ["0x8", "0x2"], "type": "u16"},
           "speaker_names": {"3": "苗木"}}
    out = layout.read_layout(lay, entry_with(lay), layout.Ram(BASE, bytes(m)))
    assert out == {"message": "一行目\n二行目", "speaker": "苗木"}


def test_shift_jis_terminator_only_at_character_boundaries():
    # 0x82 0x00 can't occur, but a second byte equal to the terminator's first byte must not end the text early:
    # ソ is 83 5C in Shift-JIS, and 5C is also the terminator used here.
    m = ram()
    put(m, BASE + 0x100, "ソア".encode("cp932") + b"\x5c" + "後".encode("cp932"))
    lay = {"message": {"address": "0x08800100", "charset": "shift_jis", "max_bytes": 16, "terminator": "5C"}}
    assert layout.read_layout(lay, entry_with(lay), layout.Ram(BASE, bytes(m)))["message"] == "ソア"


def test_length_field_cut_character_and_cleanup():
    m = ram()
    put(m, BASE + 0x100, "<C1>あいう".encode("utf-8"))
    put(m, BASE + 0x30, bytes([4 + 3 * 2 + 1]))                   # 4 tag bytes, 2 kana, 1 byte of the third
    lay = {"message": {"address": "0x08800100", "charset": "utf8", "max_bytes": 64,
                       "length": {"address": "0x08800030"},
                       "cleanup": [{"regex": "<C\\d>", "replace": ""}]}}
    assert layout.read_layout(lay, entry_with(lay), layout.Ram(BASE, bytes(m)))["message"] == "あい"


def test_table_charset_and_choices():
    t = tbl.parse("0001=は\n0002=い\n0003=え\n/FFFF\n")
    m = ram()
    put(m, BASE + 0x100, bytes.fromhex("0001 0002 FFFF".replace(" ", "")))
    put(m, BASE + 0x110, bytes.fromhex("0003 FFFF".replace(" ", "")))
    put(m, BASE + 0x40, b"\x02\x01")
    lay = {"choices": {"address": "0x08800100", "charset": "table", "max_bytes": 16, "terminator": "FFFF",
                       "rows": {"count": 4, "stride": 16}},
           "choice_count": {"address": "0x08800040"},
           "choice_cursor": {"address": "0x08800041"}}
    out = layout.read_layout(lay, entry_with(lay, encoding="table", unit=2), layout.Ram(BASE, bytes(m)), t)
    assert out == {"choice_count": 2, "choice_cursor": 1, "choices": ["はい", "え"]}


def test_unreadable_fields_are_reported_not_fatal():
    lay = {"message": {"address": "0x09000000", "charset": "utf16le", "max_bytes": 8},
           "waiting": {"address": "0x08800000"}}
    out = layout.read_layout(lay, entry_with(lay), layout.Ram(BASE, bytes(ram())))
    assert out["waiting"] is False and "message" in out["errors"]


def test_pick_prefers_the_version():
    e = {"layouts": [{"notes": "general"}, {"versions": ["b" * 40], "notes": "b"}]}
    assert layout.pick(e, "b" * 40)["notes"] == "b"
    assert layout.pick(e, "c" * 40)["notes"] == "general"
    assert layout.pick({"layouts": [{"versions": ["b" * 40]}]}, None) is None


def test_pick_without_a_hash_takes_the_only_version_of_the_release():
    lays = [{"notes": "general"}, {"versions": ["b" * 40], "notes": "b"}, {"versions": ["c" * 40], "notes": "c"}]
    e = {"id": "SLPS-00001", "aliases": ["SLPS-00002"], "layouts": lays,
         "versions": [{"exe_sha1": "b" * 40}, {"exe_sha1": "c" * 40, "id": "SLPS-00002"}]}
    assert layout.pick(e, None, "SLPS-00001")["notes"] == "b"          # a version without an id is the entry's own
    assert layout.pick(e, None, "SLPS-00002")["notes"] == "c"
    assert layout.pick(e, "c" * 40, "SLPS-00001")["notes"] == "c"      # a hash, when there is one, decides
    assert layout.pick(e, None, "SLPS-00003")["notes"] == "general"    # an id with no version listed
    e["versions"].append({"exe_sha1": "d" * 40, "note": "rev 1"})
    assert layout.pick(e, None, "SLPS-00001")["notes"] == "general"    # two versions of one id: no guessing
    assert layout.pick({**e, "layouts": lays[1:]}, None, "SLPS-00001") is None
    cart = {"id": "AXXJ", "versions": [{"header_crc32": "0" * 8}], "layouts": lays}
    assert layout.pick(cart, None, "AXXJ")["notes"] == "general"       # no executable hash to name


def test_row_lengths_cut_a_line_written_over_an_older_one():
    m = ram()
    put(m, BASE + 0x100, "あいうえお".encode("cp932").ljust(16, b"\0") + "かきくけこ".encode("cp932"))
    put(m, BASE + 0x30, bytes([2, 3, 1]))
    lay = {"message": {"address": "0x08800100", "charset": "shift_jis", "max_bytes": 16,
                       "rows": {"count": 2, "stride": 16, "lengths": {"address": "0x08800030", "counts": "characters"}},
                       "when": {"address": "0x08800032"}}}
    read = lambda: layout.read_layout(lay, entry_with(lay), layout.Ram(BASE, bytes(m)))["message"]
    assert read() == "あい\nかきく"
    lay["message"]["rows"]["lengths"]["counts"] = "bytes"
    assert read() == "あ\nか"                                      # 2 bytes; 3 bytes end inside the second character
    m[0x32] = 0
    assert read() == ""                                            # the window is closed: the memory is stale


def test_pick_passes_over_a_layout_with_keys_it_does_not_know():
    newer = {"versions": ["b" * 40], "message": {"address": "0x1", "charset": "utf8", "max_bytes": 8, "until": {}}}
    e = {"layouts": [newer, {"notes": "general"}]}
    assert not layout.understood(newer)
    assert layout.pick(e, "b" * 40)["notes"] == "general"
    assert layout.pick({"layouts": [newer]}, "b" * 40) is None
    text = {"address": "0x1", "charset": "utf8", "max_bytes": 8}
    for odd in ({"blink": 1}, {"endian": "middle"}, {"message": {**text, "charset": "ebcdic"}},
                {"message": {**text, "rows": {"count": 1, "stride": 8, "gap": 2}}},
                {"message": {**text, "length": {"address": "0x2", "counts": "words"}}},
                {"waiting": {"address": "0x2", "type": "u64"}}, {"shown": {"address": "0x2", "counts": "bytes"}},
                {"speaker": {**text, "when": {"address": "0x2", "below": 3}}},
                {"message": text, "when": {"address": "0x2", "counts": "bytes"}}, {"message": text, "when": 1},
                {"message": {**text, "pages": {"break": "0C"}}},
                {"message": {**text, "pages": {"break": "0C", "cursor": {"address": "0x2", "equals": 1}}}},
                {"message": {**text, "pages": {"break": "0C", "cursor": {"address": "0x2"}, "from": 1}}},
                {"message": {**text, "rows": {"count": 2, "stride": 4},
                             "pages": {"break": "0C", "cursor": {"address": "0x2"}}}},
                {"message": {**text, "codes": {}}}, {"message": {**text, "codes": {"7": 2}}},
                {"message": {**text, "codes": {"07": "2"}}}, {"message": {**text, "codes": {"0703": 1}}},
                {"message": {**text, "codes": {"07": 17}}}, {"message": {**text, "codes": ["07"]}},
                {"message": {**text, "charset": "table", "codes": {"07": 2}}}):
        assert not layout.understood(odd), odd
    assert layout.understood({"message": {**text, "codes": {"07": 2, "1f03": 3}}})


def test_every_layout_the_schema_accepts_here_is_understood():
    # the reader's key lists and the schema say the same thing twice; the vectors and the catalog use every key
    from layout_vectors import cases
    lays = [v["layout"] for v in cases()]
    lays += [lay for _, e in catalog.entries(REPO) for lay in e.get("layouts", [])]
    assert len(lays) > 30 and all(layout.understood(lay) for lay in lays)
    keys = lambda d: {k for k, v in d.items() for k in ([k] + sorted(keys(v)) if isinstance(v, dict) else [k])}
    used = set().union(*(keys(lay) for lay in lays))
    schema = json.loads((REPO / "schema" / "entry.schema.json").read_text(encoding="utf-8"))
    named = set(schema["properties"]["layouts"]["items"]["properties"])
    for d in ("int", "length", "text"):
        named |= set(schema["$defs"][d]["properties"])
    named |= set(schema["$defs"]["text"]["properties"]["rows"]["properties"])
    assert named - used <= {"regex", "replace", "remap"}, named - used


def test_big_endian_pointers():
    m = ram()
    put(m, BASE + 0x20, struct.pack(">I", BASE + 0x80))
    put(m, BASE + 0x84, struct.pack(">H", 0x1234))
    lay = {"endian": "big", "shown": {"base": "0x08800020", "offsets": ["0x4"], "type": "u16"}}
    assert layout.read_layout(lay, entry_with(lay), layout.Ram(BASE, bytes(m))) == {"shown": 0x1234}


def write_catalog(tmp_path: Path, entry: dict) -> Path:
    (tmp_path / "schema").mkdir()
    (tmp_path / "schema" / "entry.schema.json").write_text(
        (REPO / "schema" / "entry.schema.json").read_text(encoding="utf-8"), encoding="utf-8")
    d = tmp_path / "games" / entry["platform"]
    d.mkdir(parents=True)
    (tmp_path / "tables").mkdir()
    (d / f"{entry['id']}.json").write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    return d / f"{entry['id']}.json"


def test_schema_and_catalog_rules(tmp_path):
    good = {"message": {"address": "0x08800100", "charset": "utf16le", "max_bytes": 64}, "poll": 20,
            "versions": ["a" * 40]}
    write_catalog(tmp_path, entry_with(good))
    assert catalog.validate(tmp_path) == []

    bad = {"message": {"address": "0x08800100", "base": "0x0", "offsets": ["0x0"], "charset": "utf16le", "max_bytes": 8},
           "versions": ["b" * 40], "choices": {"address": "0x1", "charset": "table", "max_bytes": 8}}
    (tmp_path / "games" / "psp" / "ULJM00001.json").write_text(json.dumps(entry_with(bad)), encoding="utf-8")
    msgs = " | ".join(str(p) for p in catalog.validate(tmp_path))
    assert "layouts/0/message" in msgs                       # both an address and a pointer chain
    assert "isn't in the entry's versions" in msgs
    assert "a table charset needs the entry's tables" in msgs

    for wrong in ({"length": {"address": "0x1", "counts": "words"}}, {"length": {"address": "0x1", "equals": 1}},
                  {"rows": {"count": 2, "stride": 8, "lengths": {"type": "u8"}}}, {"when": {"address": "0x1", "counts": "bytes"}}):
        lay = {"message": {"address": "0x08800100", "charset": "utf16le", "max_bytes": 8, **wrong}}
        (tmp_path / "games" / "psp" / "ULJM00001.json").write_text(json.dumps(entry_with(lay)), encoding="utf-8")
        assert any("layouts/0/message" in str(p) for p in catalog.validate(tmp_path)), wrong


def test_cli_reads_a_snapshot_with_its_meta(tmp_path, capsys):
    m = ram()
    put(m, BASE + 0x100, "テスト".encode("utf-16-le") + b"\0\0")
    lay = {"message": {"address": "0x08800100", "charset": "utf16le", "max_bytes": 32, "terminator": "0000"},
           "versions": ["a" * 40]}
    entry = write_catalog(tmp_path, entry_with(lay))
    snap = tmp_path / ("f" * 40 + ".ram.gz")
    snap.write_bytes(gzip.compress(bytes(m)))
    snap.with_name("f" * 40 + ".json").write_text(json.dumps({"base_address": "0x08800000", "exe_sha1": "a" * 40}))
    assert main(["layout", str(entry), str(snap), "--root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"message": "テスト"}


def test_catalog_entries_still_valid():
    assert catalog.validate(REPO) == []
