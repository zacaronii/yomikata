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


def test_a_ps2_disc_gives_its_build_as_an_emulator_names_it(tmp_path):
    """BOOT2 in SYSTEM.CNF: the serial, and the CRC of the boot program's words XORed together."""
    from yomikata.identify import ps2_crc
    cnf = b"BOOT2 = cdrom0:\\SLPM_655.55;1\r\nVER = 1.00\r\n"
    exe = b"\x7fELF" + bytes.fromhex("01000000 02000000 00000080") + b"\xaa\xbb"       # two bytes past the last word
    root = dir_record(b"\0", 18, 2048, True) + dir_record(b"\1", 18, 2048, True) + \
        dir_record(b"SLPM_655.55;1", 20, len(exe), False) + dir_record(b"SYSTEM.CNF;1", 19, len(cnf), False)
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:190] = dir_record(b"\0", 18, 2048, True)[:34]
    img = tmp_path / "ps2.iso"
    img.write_bytes(b"".join([bytes(2048)] * 16 + [bytes(pvd), bytes(2048), root.ljust(2048, b"\0"),
                                                   cnf.ljust(2048, b"\0"), exe.ljust(2048, b"\0")]))
    r = identify(img)
    want = f"{0x464C457F ^ 1 ^ 2 ^ 0x80000000:08x}"
    assert (r.platform, r.id, r.crc, r.build) == ("ps2", "SLPM-65555", want, f"SLPM-65555-{want}")
    assert ps2_crc(b"") == "00000000" and "crc" not in identify_ps1(tmp_path).as_dict()


def identify_ps1(tmp_path):
    img = tmp_path / "ps1.iso"
    make_ps1(img, False)
    return identify(img)


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


def test_the_shift_jis_cell_table_follows_the_codec():
    # tables/shift-jis-le16.tbl is made by rule: every two-byte cp932 code outside the user-defined area, its
    # second byte first.
    want = {}
    for lead in (*range(0x81, 0xA0), *range(0xE0, 0xF0), *range(0xFA, 0xFD)):
        for trail in (*range(0x40, 0x7F), *range(0x80, 0xFD)):
            try:
                want[bytes([trail, lead])] = bytes([lead, trail]).decode("cp932")
            except UnicodeDecodeError:
                pass
    t = tbl.parse((REPO / "tables" / "shift-jis-le16.tbl").read_text(encoding="utf-8"))
    assert t.chars == want and not t.end and not t.newline


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
    assert "tables" in msgs and "only .glyphs, .read, .tbl files" in msgs
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


# ---- yomikata glyphs: a game's own font out of memory and video memory ----

def test_glyphs_are_drawn_fingerprinted_and_sorted_by_what_is_read(tmp_path, capsys):
    import gzip
    import numpy as np
    from PIL import Image
    from yomikata import cli, glyphs
    video = np.zeros((glyphs.VRAM_HEIGHT, glyphs.VRAM_WIDTH), dtype="<u2")

    def put(u, v, plane, rows):                                   # a glyph's strokes into one plane of the page
        for dy, row in enumerate(rows):
            for dx, ch in enumerate(row):
                value = {".": 0, "#": 1, "+": 2, "s": 3}[ch] << (2 * plane)
                x = u + dx
                video[256 + v + dy, 896 + x // 4] |= value << (4 * (x % 4))
    cross, bar = ["..#..", ".###.", "..#s.", "..+.."], ["#####", ".sss.", ".....", "....."]
    put(0, 10, 0, cross)
    put(0, 10, 1, bar)                                            # two glyphs in one cell, on two planes
    put(40, 30, 0, bar)                                           # the bar again, somewhere else
    ram = bytearray(0x400)
    ram[0x100:0x110] = bytes([0, 10, 5, 0xA0, 0, 10, 5 | 0x40, 0xA0, 40, 30, 5, 0x90, 80, 80, 5, 0xA0])
    ram[0x0F8:0x0FC] = (4).to_bytes(4, "little")
    (tmp_path / "s.ram.gz").write_bytes(gzip.compress(bytes(ram)))
    (tmp_path / "s.vram.gz").write_bytes(gzip.compress(video.tobytes()))
    pixels = glyphs.page(video.tobytes(), 896, 256)
    found = [glyphs.picture(pixels, r, 4) for r in glyphs.records(bytes(ram[0x100:0x110]), 4)]
    assert found[0].tolist() == [[0, 0, 1, 0, 0], [0, 1, 1, 1, 0], [0, 0, 1, 3, 0], [0, 0, 2, 0, 0]]
    marks = [glyphs.fingerprint(g) for g in found]
    assert marks[1] == marks[2] != marks[0] and marks[3] == "0" * 16      # the same picture, the same mark; an empty cell
    (tmp_path / "d.txt").write_text(f"# read so far\n{marks[0]}=十\n", encoding="utf-8")
    args = ["glyphs", str(tmp_path / "s.ram.gz"), "--list", "0x80000100", "--count", "@0x800000F8", "--height", "4",
            "--dictionary", str(tmp_path / "d.txt"), "--sheet", str(tmp_path / "sheet.png")]
    assert cli.main(args) == 0
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert lines[0] == f"000 {marks[0]} 十" and lines[3] == f"003 {'0' * 16} ?"
    assert lines[1].startswith(f"001 {marks[1]} ?  like: 十 0.") and lines[2].startswith(f"002 {marks[1]} ?  like: 十")
    # Likeness, and a character given to two pictures.
    assert glyphs.nearest(found[1], [("十", found[0]), ("一", found[2])])[0] == ("一", 1.0)
    assert glyphs.twice({"a": "十", "b": "一", "c": "十"}) == {"十": ["a", "c"]}
    assert "4 glyphs, 1 in the dictionary, 1 to read" in out.err         # the bar once, though it is there twice
    assert Image.open(tmp_path / "sheet.png").size == (60 + 20 * 8 * 6, 5 * 6)
    (tmp_path / "s.vram.gz").unlink()
    assert cli.main(args) == 1


def test_ff9_places_reads_a_package_as_it_lies_on_the_disc():
    """tools/scripts/ff9_places.py on a made-up package: the place's key as the script works it out, and its
    glyphs' pictures out of the font's TIM image."""
    import importlib.util
    import struct
    import numpy as np
    from yomikata import glyphs
    script = Path(__file__).resolve().parents[1] / "scripts/ff9_places.py"
    spec = importlib.util.spec_from_file_location("ff9_places", script)
    ff9 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ff9)
    records = struct.pack("<HHHH", 8, 0x1020, 8, 0)                    # one message
    block = struct.pack("<HH", 1, 0) + records + b"\x56\x57\xFF"
    listed = bytes([0, 182, 5, 0xA0, 0, 182, 5 | 0x40, 0xA0])           # two glyphs in one cell, on two planes
    words = np.zeros((13, 4), dtype="<u2")
    words[0, 0] = 0x1111 | 0x0440                                      # a stroke on each plane in the top row
    tim = struct.pack("<3I", 0x10, 8, 12) + bytes(8) + struct.pack("<I4H", 12 + words.nbytes, 896, 256 + 182, 4, 13) + words.tobytes()
    font = struct.pack("<7I", 0x10D, 7, 8, 0x1C + len(listed) - 8, 0, 0xFFFFFFFF, 2) + listed + bytes(16) + tim
    whole = struct.pack("<4I", 0x106, 0x21, 8, 8 + len(block)) + block + font

    class Stream:
        def read(self, at, size):
            return (bytes(64) + whole + bytes(64))[at:at + size].ljust(size, b"\0")
    key, messages, pictures, kept = ff9.package(Stream(), 64)
    assert messages == 1 and key == ff9.fnv1a64(listed + records) and kept.startswith(struct.pack("<I", 0x106))
    assert pictures[0][0].tolist() == [1, 1, 1, 1, 0] and pictures[1][0].tolist() == [0, 1, 1, 0, 0]
    assert glyphs.fingerprint(pictures[0]) != glyphs.fingerprint(pictures[1])
    assert ff9.package(Stream(), 60) is None and ff9.fnv1a64(b"") == "cbf29ce484222325"



def test_a_font_in_jis_order_is_checked_by_its_order(tmp_path, capsys):
    from yomikata import glyphs
    font = "亜唖娃阿哀愛挨"                                              # the first kanji of JIS, in order
    rows = glyphs.in_order(list("亜唖") + [None] + list("阿愛") + ["愛", "挨"])       # one unread, one read wrongly
    assert [r["status"] for r in rows] == ["ok", "ok", "unread", "ok", "breaks", "ok", "ok"] or \
        [r["status"] for r in rows] == ["ok", "ok", "unread", "ok", "ok", "breaks", "ok"]
    assert rows[2]["must_be"] == "娃" and rows[2]["allowed"] == 1
    rows = glyphs.in_order(["亜", None, "阿", "哀"])
    assert rows[1]["candidates"] == "唖娃" and "must_be" not in rows[1]
    readings = tmp_path / "readings.txt"
    readings.write_text("# a font\n亜\n唖\n? can't tell\n阿\n挨 looks like it\n愛\n挨\n", encoding="utf-8")
    from yomikata import cli
    assert cli.main(["sorted", str(readings)]) == 0
    out = capsys.readouterr()
    assert out.out == "    2 ? unread must be 娃\n    4 挨 breaks must be 哀\n"         # the wrong reading is put right too
    assert "7 glyphs: 5 in order, 1 that break it, 1 unread; 2 of those the order settles by itself" in out.err
    with pytest.raises(ValueError):
        glyphs.in_order(list(font), "iroha")


def test_disc_text_finds_the_stretches_that_read_through_a_table(tmp_path, capsys):
    from yomikata import disctext
    table = tmp_path / "game.tbl"
    table.write_text("20=あ\n21=い\n22=う\nFA01=山\nFA02=川\n", encoding="utf-8")
    img = tmp_path / "disc.iso"
    make_ps1(img, False)
    with open(img, "ab") as f:
        f.write(bytes(100) + bytes([0x20, 0x21, 0xFA, 0x01, 0x22, 0x20, 0xFA, 0x02, 0x21]) + bytes(2048 - 109))
        f.write(bytes([0x20, 0x21, 0x22]) + bytes(2045))                # too short to count
    found = disctext.scan(img, tbl.load([table]), least=5)
    assert found == [(21 * 2048 + 100, "あい山うあ川い")]
    out = tmp_path / "text.txt"
    from yomikata import cli
    assert cli.main(["disc-text", str(img), "--table", str(table), "--least", "5", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == f"{21 * 2048 + 100:X}\tあい山うあ川い\n"
    assert "1 stretches, 7 characters" in capsys.readouterr().err


def test_glyph_read_puts_a_second_reading_beside_yours(tmp_path, capsys, monkeypatch):
    from yomikata import cli, glyphread
    glyphs = tmp_path / "font.json"
    glyphs.write_text(json.dumps({"02E6": "0000410042002400", "02FD": "0000380006000000", "0106": "ff00810081008100"}))
    pictures = glyphread.pictures(glyphs, 4, 2)
    assert pictures["02E6"].shape == (4, 16) and pictures["02E6"][1, 1] == 1 and pictures["02E6"][0].sum() == 0
    page = glyphread.enlarged(pictures["0106"])
    assert page.size == ((8 + 6) * 8, (4 + 6) * 8) and page.getpixel((0, 0)) == (255, 255, 255)   # trimmed to its 8 columns
    readings = {"02E6": "そ", "02FD": "う", "0106": "口"}
    by_page = {glyphread.enlarged(picture).tobytes(): readings[code] for code, picture in pictures.items()}
    monkeypatch.setattr(glyphread, "second_reader", lambda: (lambda image: by_page[image.tobytes()]))
    mine = tmp_path / "mine.tbl"
    mine.write_text("02E6=そ\n0106=気\n", encoding="utf-8")
    text = tmp_path / "text.txt"
    text.write_text("このように気をつけて\nそうか\n", encoding="utf-8")
    out = tmp_path / "second.txt"
    assert cli.main(["glyph-read", str(glyphs), "--rows", "4", "--mine", str(mine), "--text", str(text), "--out", str(out)]) == 0
    printed = capsys.readouterr()
    assert out.read_text(encoding="utf-8") == "0106\t口\n02E6\tそ\n02FD\tう\n"
    assert "0106 気 differ: the second reader says 口  このように[気]をつけて" in printed.out
    assert "02FD ? unread: the second reader says う" in printed.out and "02E6" not in printed.out
    assert "3 glyphs: 1 read the same two ways, 1 that differ" in printed.err
    assert glyphread.sentences(["あいう", "えあお"], "あ", width=1) == {"あ": ["[あ]い", "え[あ]お"]}


def test_a_scripts_dictionary_of_glyph_pictures_is_checked_by_its_own_form(tmp_path):
    """games/<platform>/<id>.glyphs: a picture's hash (16 hex digits) and the one character it is."""
    (tmp_path / "schema").mkdir()
    (tmp_path / "schema" / "entry.schema.json").write_text(
        (REPO / "schema" / "entry.schema.json").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "games" / "ps1").mkdir(parents=True)
    (tmp_path / "tables").mkdir()
    script = tmp_path / "games" / "ps1" / "SLPS-99999.lua"
    script.write_text("-- reader: title 題 (Title)\n-- reader: reads a line\n-- reader: version 1.0\n"
                      f"-- reader: build {'a' * 40}\n-- reader: no hooks\n-- reader: no layout none\n"
                      'local glyphs = reader.data("glyphs")\n', encoding="utf-8")
    script.with_suffix(".glyphs").write_text("# a note\n006C48ABAE2FDCB3=美\n", encoding="utf-8")
    assert catalog.validate(tmp_path) == []
    script.with_suffix(".glyphs").write_text("006C48ABAE2FDCB3=美\n006c=美\n013C9E2AB8071749=a line\n", encoding="utf-8")
    assert [str(p) for p in catalog.validate(tmp_path)] == [
        "games/ps1/SLPS-99999.glyphs: line 2: expected 16 hex digits, = and one character",
        "games/ps1/SLPS-99999.glyphs: line 3: expected 16 hex digits, = and one character"]


# ---- yomikata unread: the pictures a script logged for want of a reading ----

def test_unread_draws_what_a_script_logged_and_writes_readings_into_its_dictionary(tmp_path, capsys):
    from PIL import Image
    from yomikata import cli, unread
    # Three pictures of 8 by 4 at two bits a pixel (two bytes a row), and one of 4 by 4 at a bit a pixel, as
    # lib/glyphs.lua logs them; the rows they stood in; and a line that isn't what its size says.
    data = [bytes([0x1B, 0xE4] * 4), bytes([0xFF, 0x00] * 4), bytes([0x00, 0x55] * 4), bytes([0x90, 0x60, 0x60, 0x90])]
    marks = ["00AA000000000001", "00AB000000000002", "0C00000000000003", "0D00000000000004"]
    log = tmp_path / "X.log"
    log.write_text("".join(f"glyph {m} {'8x4x2' if len(d) == 8 else '4x4x1'} {d.hex()}\n" for m, d in zip(marks, data))
                   + f"glyph-line {marks[0]} _ {marks[1]}\nglyph-line {marks[0]} _ {marks[1]}\n"
                   + f"other things a host logs\nglyph-line {marks[2]} {marks[0]}\nglyph 0E00000000000005 8x4x2 1b\n",
                   encoding="utf-8")
    pictures = unread.logged(log.read_text(encoding="utf-8"))
    assert list(pictures) == marks                                         # in the order they came up; the short one left out
    assert pictures[marks[0]].pixels().tolist() == [[0, 1, 2, 3, 3, 2, 1, 0]] * 4
    assert pictures[marks[3]].pixels().tolist() == [[1, 0, 0, 1], [0, 1, 1, 0], [0, 1, 1, 0], [1, 0, 0, 1]]
    rows = unread.rows(log.read_text(encoding="utf-8"))
    assert rows == [[marks[0], None, marks[1]], [marks[2], marks[0]]]      # a row once, however often it was logged
    dictionary, sheet = tmp_path / "X.glyphs", tmp_path / "sheet.png"
    dictionary.write_text(f"# made up\n{marks[1]}=二\n", encoding="utf-8")
    args = ["unread", str(log), "--dictionary", str(dictionary), "--sheet", str(sheet)]
    assert cli.main(args) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == [f"{marks[0]} 8x4", f"{marks[2]} 8x4", f"{marks[3]} 4x4"]
    assert "4 pictures logged, 1 in the dictionary, 3 to read" in out.err
    assert Image.open(sheet).size == (16 * 36, 4 * 3 + 16)
    assert Image.open(tmp_path / "sheet-rows.png").size[1] == 2 * (4 * 2 + 26)       # both rows show an unread picture
    # A row read whole gives each of its pictures a character; a reading by a hash's start; one taken out.
    assert cli.main(args + ["--row", "0=一 二", "--add", "0C=三", "0D=四"]) == 0
    assert dictionary.read_text(encoding="utf-8") == f"# made up\n{marks[0]}=一\n{marks[1]}=二\n{marks[2]}=三\n{marks[3]}=四\n"
    assert "4 in the dictionary, 0 to read" in capsys.readouterr().err
    assert cli.main(args + ["--add", "0D="]) == 0 and marks[3] not in dictionary.read_text(encoding="utf-8")
    capsys.readouterr()
    # Refused: a row read to another length, a row that reads a picture otherwise than the dictionary has
    # it, the start of two hashes, two characters for one picture.
    for bad in (["--row", "0=一"], ["--row", "1=三二"], ["--add", "00A=五"], ["--add", "0D=四四"]):
        assert cli.main(args + bad) == 1
    assert "0D" not in dictionary.read_text(encoding="utf-8")
    # The second reader on whole rows: where it gives a character a picture, each picture takes its own.
    whole = unread.second_rows(rows, pictures, reader=lambda page: "一 二" if page.width == 18 * (8 + 2 + 8) else "三…一")
    assert whole == {marks[0]: ["一"], marks[1]: ["二"]}                    # the second row was read to another length
    alone = unread.second({marks[3]: pictures[marks[3]]}, reader=lambda page: "四")
    assert alone == {marks[3]: "四"}
    # A row may have characters as text among its pictures (a script that knows them by code): a reading of
    # the row must have them where they stand, and the pictures take the rest.
    mixed = unread.rows(f"glyph-line =あの {marks[0]} _ =は {marks[2]} =。\n")
    assert mixed == [["=あの", marks[0], None, "=は", marks[2], "=。"]]
    assert unread.row_readings(mixed, {0: "あの一 は三。"}) == {marks[0]: "一", marks[2]: "三"}
    for wrong in ("あの一は三", "あね一は三。"):
        with pytest.raises(ValueError, match="row 0"):
            unread.row_readings(mixed, {0: wrong})
    assert unread.second_rows(mixed, pictures, reader=lambda page: "あの一は三。") == {}       # no font: not read whole
    assert unread.row_sheet({0: mixed[0]}, pictures, {marks[0]}).size[1] == 4 * 2 + 26
    assert unread.starts(marks + ["00AA0F0000000009"]) == {marks[0]: "00AA00", "00AA0F0000000009": "00AA0F", marks[1]: "00AB", marks[2]: "0C00", marks[3]: "0D00"}


def test_mgs_lines_finds_a_list_and_a_call_and_writes_what_unread_reads():
    """tools/scripts/mgs_lines.py on a made-up list of lines and a made-up radio call, each with the pictures
    of its own kanji after it: the lines as marks, and the log `yomikata unread` reads."""
    import importlib.util
    import struct
    from yomikata import unread
    spec = importlib.util.spec_from_file_location("mgs_lines", Path(__file__).resolve().parents[1] / "scripts/mgs_lines.py")
    mgs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mgs)

    def text(*codes):                                                  # codes as the game writes them: high byte first
        return b"".join(bytes([c]) if c < 0x80 else struct.pack(">H", c | 0x8000) for c in codes) + b"\0"

    def record(size_of, start, body):
        body += bytes(-len(body) % 4)
        return struct.pack("<4I", 16 + len(body) if size_of else 0, start, 40, 0) + body
    pictures = [bytes([n]) * 33 + bytes(3) for n in (0x3F, 0x2A, 0x15)]
    # A list of two lines: kana, the fixed font, a space, its own kanji 1 and 2, a word with a reading.
    first = text(0x0128, 0x1C01, 0x1002, 0x20, 0x0129, 0x1C02)
    second = text(0x23, 0x7B, 0x1C01, 0x012A, 0x1002, 0x0201, 0x7D, 0x23, 0x1003)
    listed = record(True, 0, first) + record(False, 90, second)
    listed += bytes(-len(listed) % 4) + pictures[0] + pictures[1]
    data = bytes(64) + listed + bytes(64)
    found = mgs.lists(data)
    assert len(found) == 1 and found[0]["at"] == 64 and found[0]["pictures"] == pictures[:2] and len(found[0]["lines"]) == 2
    fixed = mgs.fixed_font("# made up\n9002=、\n9003=。\n")
    one, two = mgs.fnv(pictures[0]), mgs.fnv(pictures[1])
    assert mgs.marks(found[0]["lines"][0], mgs.OWN, pictures, fixed) == ["=と", one, "=、", "_", "=ど", two]
    assert mgs.marks(found[0]["lines"][1], mgs.OWN, pictures, fixed) == [one, "=な。"]      # the reading left out
    # A call: its frequency, its length, a line after 0xFF 0x01, then its pictures.
    line = b"\xff\x01" + struct.pack(">H", 20) + bytes(6) + text(0x0142, 0x1601, 0x4E | 0, 0x1003)
    call = struct.pack(">H", 14085) + bytes(6) + b"\x80" + struct.pack(">H", 2 + len(line)) + line
    radio = bytes(32) + call + pictures[2] + bytes(40)
    called = mgs.calls(radio)
    assert len(called) == 1 and called[0]["pictures"] == [pictures[2]]
    log = mgs.log(found + called, fixed)
    assert log[0] == f"glyph {one} 12x12x2 {pictures[0].hex()}" and len([x for x in log if x.startswith("glyph ")]) == 3
    assert f"glyph-line {one} =な。" in log and f"glyph-line =と {one} =、 _ =ど {two}" in log
    logged = unread.logged("\n".join(log))
    assert set(logged) == {one, two, mgs.fnv(pictures[2])} and logged[one].width == 12
    assert [one, "=な。"] in unread.rows("\n".join(log))


def test_an_entrys_tuning_is_a_step_for_each_core(tmp_path):
    """`tuning`: for a libretro core's id, the step a player app starts the game at, and optionally the device
    model it was found on. An object of objects; `step` a non-empty string; nothing else in it."""
    root = tmp_path / "cat"
    shutil.copytree(REPO / "schema", root / "schema")
    (root / "games" / "ps2").mkdir(parents=True)
    (root / "tables").mkdir()
    good = {"id": "SLPS-00001", "platform": "ps2", "title": "テスト", "encoding": "unknown", "status": "auto"}
    path = root / "games/ps2/SLPS-00001.json"

    def problems(tuning):
        path.write_text(json.dumps({**good, "tuning": tuning}))
        return [str(p) for p in catalog.validate(root)]
    assert problems({"pcsx2": {"step": "balanced", "measured_on": "A Handheld 2"}}) == []
    assert problems({"pcsx2": {"step": "fast"}, "bsnes_mercury_balanced": {"step": "0"}}) == []      # measured_on is optional
    for bad in ({}, {"pcsx2": "fast"}, {"pcsx2": {}}, {"pcsx2": {"step": ""}}, {"pcsx2": {"step": 2}},
                {"pcsx2": {"step": "fast", "measured_on": 3}}, {"pcsx2": {"step": "fast", "by": "someone"}},
                {"PCSX2 core": {"step": "fast"}}, ["pcsx2"]):
        assert any("tuning" in p for p in problems(bad)), bad


def test_an_entrys_wii_controller_is_one_of_three(tmp_path):
    """`wii_controller`: the Wii controller a game is played with: nunchuk, classic or sideways, nothing else."""
    root = tmp_path / "cat"
    shutil.copytree(REPO / "schema", root / "schema")
    (root / "games" / "wii").mkdir(parents=True)
    (root / "tables").mkdir()
    good = {"id": "RABCDE", "platform": "wii", "title": "テスト", "encoding": "unknown", "status": "auto"}
    path = root / "games/wii/RABCDE.json"

    def problems(controller):
        path.write_text(json.dumps({**good, "wii_controller": controller}))
        return [str(p) for p in catalog.validate(root)]
    for fine in ("nunchuk", "classic", "sideways"):
        assert problems(fine) == [], fine
    for bad in ("Classic", "wiimote", "", 1, None, ["classic"]):
        assert any("wii_controller" in p for p in problems(bad)), bad
