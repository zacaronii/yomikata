import json
import shutil
import struct
from pathlib import Path

import pytest

from yomikata import catalog, tbl
from yomikata.identify import identify, serial_from_boot

REPO = Path(__file__).resolve().parents[2]


def dir_record(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
    body = bytes([0]) + struct.pack("<I", lba) + struct.pack(">I", lba) + struct.pack("<I", size) + \
        struct.pack(">I", size) + bytes(7) + bytes([2 if is_dir else 0, 0, 0]) + struct.pack("<H", 1) + \
        struct.pack(">H", 1) + bytes([len(name)]) + name
    if len(body) % 2 == 0:
        body += b"\0"
    return bytes([len(body) + 1]) + body


def make_ps1(path: Path, raw: bool) -> None:
    """A minimal disc: PVD at 16, root directory at 18, SYSTEM.CNF at 19, the exe at 20."""
    cnf = b"BOOT = cdrom:\\SLPS_012.34;1\r\nTCB = 4\r\n"
    exe = b"PS-X EXE" + bytes(100)
    root = dir_record(b"\0", 18, 2048, True) + dir_record(b"\1", 18, 2048, True) + \
        dir_record(b"SYSTEM.CNF;1", 19, len(cnf), False) + dir_record(b"SLPS_012.34;1", 20, len(exe), False)
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:156 + 34] = dir_record(b"\0", 18, 2048, True)[:34]
    sectors = [bytes(2048)] * 16 + [bytes(pvd), bytes(2048), root.ljust(2048, b"\0"), cnf.ljust(2048, b"\0"),
                                    exe.ljust(2048, b"\0")]
    with open(path, "wb") as f:
        for s in sectors:
            if raw:                                   # Mode 2 Form 1: 12 sync + 4 header + 8 subheader + data + EDC/ECC
                f.write(b"\x00" + b"\xff" * 10 + b"\x00" + b"\0\0\0\x02" + bytes(8) + s + bytes(280))
            else:
                f.write(s)


@pytest.mark.parametrize("raw", [True, False])
def test_identify_a_ps1_disc(tmp_path, raw):
    img = tmp_path / ("disc.bin" if raw else "disc.iso")
    make_ps1(img, raw)
    if raw:
        (tmp_path / "disc.cue").write_text('FILE "disc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
        img = tmp_path / "disc.cue"
    r = identify(img)
    assert (r.platform, r.id, r.exe) == ("ps1", "SLPS-01234", "SLPS_012.34") and len(r.exe_sha1) == 40


def test_boot_file_in_a_subfolder(tmp_path):
    cnf = b"BOOT = cdrom:\\MGS\\SLPM_861.11;1\r\n"
    exe = b"PS-X EXE" + bytes(40)
    sub = dir_record(b"\0", 21, 2048, True) + dir_record(b"\1", 18, 2048, True) + \
        dir_record(b"SLPM_861.11;1", 20, len(exe), False)
    root = dir_record(b"\0", 18, 2048, True) + dir_record(b"\1", 18, 2048, True) + \
        dir_record(b"MGS", 21, 2048, True) + dir_record(b"SYSTEM.CNF;1", 19, len(cnf), False)
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:190] = dir_record(b"\0", 18, 2048, True)[:34]
    img = tmp_path / "mgs.iso"
    img.write_bytes(b"".join([bytes(2048)] * 16 + [bytes(pvd), bytes(2048), root.ljust(2048, b"\0"),
                                                   cnf.ljust(2048, b"\0"), exe.ljust(2048, b"\0"), sub.ljust(2048, b"\0")]))
    r = identify(img)
    assert (r.id, r.exe) == ("SLPM-86111", "SLPM_861.11") and r.exe_sha1


def test_serials():
    assert serial_from_boot("cdrom:\\SLPS_022.96;1") == ("SLPS-02296", "SLPS_022.96")
    assert serial_from_boot("cdrom0:\\SLPM_655.55;1") == ("SLPM-65555", "SLPM_655.55")


def test_tables():
    t = tbl.parse("# kana\n0001=あ\n0002=い\n0100=学\n/FFFF\n*FEFF\n")
    assert tbl.decode(bytes.fromhex("0001 0002 FEFF 0100 FFFF 9999".replace(" ", "")), t, 2) == "あい\n学\n\n�"
    # An entry's controls: a page is a blank line, a newline a line break, anything else nothing.
    controls = {"F001": "wait", "F002": "page", "f003": "newline"}
    assert tbl.decode(bytes.fromhex("0001F0010002F0020100F0030001"), t, 2, controls) == "あい\n\n学\nあ"
    with pytest.raises(tbl.TableError):
        tbl.parse("0001=これは台詞の一行なので表には入らない")
    with pytest.raises(tbl.TableError):
        tbl.parse("001=あ")


def test_the_repository_itself_is_valid():
    assert catalog.validate(REPO) == []
    idx = catalog.build_index(REPO)
    assert "SLPS-01100" in idx["entries"]


def test_validation_catches_mistakes(tmp_path):
    root = tmp_path / "cat"
    shutil.copytree(REPO / "schema", root / "schema")
    (root / "games" / "ps1").mkdir(parents=True)
    (root / "tables").mkdir()
    good = {"id": "SLPS-00001", "platform": "ps1", "title": "テスト", "encoding": "shift_jis", "status": "auto"}
    (root / "games/ps1/SLPS-00001.json").write_text(json.dumps(good))
    assert catalog.validate(root) == []
    (root / "games/ps1/SLPS-00002.json").write_text(json.dumps({**good, "id": "SLPS-00002", "aliases": ["SLPS-00001"]}))
    (root / "games/ps1/SLPS-00003.json").write_text(json.dumps({**good, "encoding": "table", "status": "verified"}))
    (root / "tables/font.png").write_bytes(b"x")
    msgs = " | ".join(str(p) for p in catalog.validate(root))
    assert "SLPS-00001 is also used" in msgs
    assert "games/<platform>/<id>.json" in msgs                     # id doesn't match its file name
    assert "tables" in msgs and "only .tbl files" in msgs
    (root / "tables/kana.tbl").write_text("0001=あ\n", encoding="utf-8")
    (root / "games/ps1/SLPS-00004.json").write_text(json.dumps({**good, "id": "SLPS-00004", "tables": ["tables/kana.tbl"]}))
    assert "SLPS-00004.json: tables need a unit" in " | ".join(str(p) for p in catalog.validate(root))
    (root / "games/ps1/SLPS-00004.json").unlink()
    (root / "tables/kana.tbl").unlink()
    (root / "games/ps1/SLPS-00003.json").unlink()
    catalog.write_index(root)
    idx = json.loads((root / "index.json").read_text())
    assert idx["entries"]["SLPS-00001"]["path"] == "games/ps1/SLPS-00001.json"


def test_scan_finds_script_runs_per_encoding_and_ignores_kanji_lists():
    import os
    from yomikata.scan import ScanResult, compressed, runs
    line = "「そんな学園への入学資格は２つしかない。」と彼は言った。"
    for codec, enc in (("cp932", "shift_jis"), ("utf-16-le", "utf16le"), ("utf-8", "utf8")):
        pad = b"\x00\x01" * 100                           # fixed, so the run can't be extended by chance
        found = {k: v[0] for k, v in runs(pad + line.encode(codec) + pad).items()}
        assert found[enc] >= 20 and sum(found.values()) == found[enc], (enc, found)
    assert runs("一二三四五六七八九十百千万".encode("cp932"))["shift_jis"][0] == 0      # a font list, not script
    for noise in ("ャ" * 40, "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほ", "う" * 30 + "ああ"):
        assert runs(noise.encode("cp932"))["shift_jis"][0] == 0, noise
    assert compressed(os.urandom(100_000)) and not compressed(line.encode("utf-8") * 3000)
    assert ScanResult(chars={"shift_jis": 5000, "utf16le": 10, "utf8": 0}).encoding() == "shift_jis"
    assert ScanResult(chars={"shift_jis": 50}, compressed_bytes=80, scanned_bytes=100).encoding() == "compressed"
    assert ScanResult(chars={"shift_jis": 50}, compressed_bytes=1, scanned_bytes=100).encoding() == "unknown"


def test_single_byte_hiragana_is_read_and_drafted(tmp_path):
    from yomikata import kana
    from yomikata.cleanup import apply
    from yomikata.draft import draft
    from yomikata.scan import ScanResult, _runs
    line = "「ボクはいったいどんな顔をしているんだろう、それでもがんばってみるよ」"
    stored = "｢ボクﾊｲｯﾀｲﾄﾞﾝﾅ顔ｦｼﾃｲﾙﾝﾀﾞﾛｳ､ｿﾚﾃﾞﾓｶﾞﾝﾊﾞｯﾃﾐﾙﾖ｣"
    assert apply(stored, kana.HIRAGANA_RULES) == line and kana.fold("ﾊﾟﾘ ｳﾞ ﾜﾞ ｰ") == "ぱり ゔ わ゛ ー"
    assert kana.spelling(stored) == (29, 5, 0) and kana.spelling("ﾞｱﾞﾏﾟｱｬｼﾞｬ") == (6, 4, 4)
    pad = b"\x00\x01" * 100
    n, best, single = _runs(pad + stored.encode("cp932") + pad)["shift_jis"]
    assert n == len(line) and best == line and single == 29
    assert _runs(pad + line.encode("cp932") + pad)["shift_jis"][2] == 0
    # Bytes that only fall in the range don't count: too few marks, marks anywhere, the codes in order.
    few = "｢ｿﾚﾊﾎﾝﾄｳﾆｱｯﾀｺﾄﾅﾉｶﾓｼﾚﾅｲﾖ､ｷｯﾄ｣"
    stray = "ﾐﾙﾜﾞﾔﾒﾟﾒﾙﾐﾞﾒﾔﾔﾙﾟﾐﾒﾐﾞﾐﾒﾍﾍﾋﾟﾅﾃﾃﾞﾀｺｺﾉﾐﾟﾙﾜﾞﾔﾆﾊｦﾞｶﾞﾃﾀﾃﾞﾄﾓ"
    for noise in (few, stray, "".join(chr(c) for c in range(0xFF61, 0xFFA0)) * 4):
        assert _runs(pad + noise.encode("cp932") + pad)["shift_jis"][0] == 0, noise
    # A line of two-byte text inside a single-byte one is counted once.
    mixed = stored[:-1] + "、そう言ってボクは外へ出ることにした。" + "｣"
    assert _runs(pad + mixed.encode("cp932") + pad)["shift_jis"][0] == len(kana.fold(mixed))

    assert ScanResult(chars={"shift_jis": 5000}, halfwidth_kana=3000).single_byte_hiragana()
    assert not ScanResult(chars={"shift_jis": 5000}, halfwidth_kana=200).single_byte_hiragana()
    assert not ScanResult(chars={"shift_jis": 500}, halfwidth_kana=400).single_byte_hiragana()
    disc = {"image": "a.chd", "id": "SLPS-00009", "platform": "ps1", "exe": "SLPS_000.09", "exe_sha1": "a" * 40,
            "chars": {"shift_jis": 5000, "utf16le": 0, "utf8": 0}, "encoding": "shift_jis"}
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    (tmp_path / "tables").mkdir()
    draft([{**disc, "single_byte_hiragana": True, "halfwidth_kana": 3000},
           {**disc, "image": "b.chd", "id": "SLPS-00010"}], tmp_path)
    plain, single = (json.loads((tmp_path / f"games/ps1/SLPS-000{n}.json").read_text()) for n in ("10", "09"))
    assert single["cleanup"] == kana.HIRAGANA_RULES and "single bytes" in single["notes"]
    assert single["sources"][0].endswith("3,000 of them hiragana stored as single bytes")
    assert "cleanup" not in plain and "notes" not in plain and plain["sources"][0].endswith("prose-like text")
    assert catalog.validate(tmp_path) == []
