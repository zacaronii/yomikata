"""`yomikata ctr`: a made-up 3DS card image's ExeFS and RomFS files, listed and read; an encrypted one refused."""
import json
import struct

import pytest

from yomikata import ctr
from yomikata.cli import main


def _name(s: str) -> bytes:
    raw = s.encode("utf-16-le")
    return struct.pack("<I", len(raw)) + raw + bytes(-len(raw) % 4)


def romfs(tree: dict) -> bytes:
    """A RomFS: IVFC header (master hash 0x20, level 3 blocks of 0x1000), level 3 with its tables. `tree` maps
    a directory's name to a dict (its contents) and a file's name to its bytes."""
    dirs, files, data = bytearray(), bytearray(), bytearray()

    def add_dir(name: str, parent: int, content: dict) -> int:
        at = len(dirs)
        dirs.extend(struct.pack("<6I", parent, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0))
        dirs[at + 0x14:at + 0x18] = b""
        dirs[at + 0x14:at + 0x14] = _name(name)
        first_file, last_file, first_dir, last_dir = 0xFFFFFFFF, None, 0xFFFFFFFF, None
        for key, value in content.items():
            if isinstance(value, dict):
                continue
            f = len(files)
            files.extend(struct.pack("<IIQQI", at, 0xFFFFFFFF, len(data), len(value), 0xFFFFFFFF) + _name(key))
            data.extend(value + bytes(-len(value) % 16))
            if last_file is None:
                first_file = f
            else:
                struct.pack_into("<I", files, last_file + 4, f)
            last_file = f
        struct.pack_into("<I", dirs, at + 0x0C, first_file)
        for key, value in content.items():
            if not isinstance(value, dict):
                continue
            d = add_dir(key, at, value)
            if last_dir is None:
                first_dir = d
            else:
                struct.pack_into("<I", dirs, last_dir + 4, d)
            last_dir = d
        struct.pack_into("<I", dirs, at + 0x08, first_dir)
        return at
    add_dir("", 0, tree)
    head = 0x28
    dir_at, file_at = head, head + len(dirs)
    data_at = -(-(file_at + len(files)) // 16) * 16
    level3 = struct.pack("<10I", head, 0, 0, dir_at, len(dirs), 0, 0, file_at, len(files), data_at)
    level3 += bytes(dirs) + bytes(files)
    level3 += bytes(data_at - len(level3)) + bytes(data)
    ivfc = bytearray(0x60)
    ivfc[0:4] = b"IVFC"
    struct.pack_into("<I", ivfc, 0x08, 0x20)
    struct.pack_into("<I", ivfc, 0x4C, 12)
    return bytes(ivfc) + bytes(0x1000 - 0x60) + level3


def card(tmp_path, encrypted: bool = False):
    exefs = bytearray(0x200)
    exefs[0:16] = struct.pack("<8sII", b".code", 0, 6)
    exefs[16:32] = struct.pack("<8sII", b"icon", 16, 4)
    exefs += b"CODE!!" + bytes(10) + b"ICON"
    exefs += bytes(-len(exefs) % 0x200)
    rom = romfs({"top.txt": b"hello", "msg": {"a.gmd": b"GMD\0one", "b.gmd": b"GMD\0two"}, "font": {"f.tex": b"TEX"}})
    rom += bytes(-len(rom) % 0x200)
    ncch = bytearray(0x200)
    ncch[0x100:0x104] = b"NCCH"
    ncch[0x150:0x160] = b"CTR-P-AXXJ".ljust(16, b"\0")
    ncch[0x18F] = 0x00 if encrypted else 0x04
    struct.pack_into("<II", ncch, 0x1A0, 1, len(exefs) // 0x200)
    struct.pack_into("<II", ncch, 0x1B0, 1 + len(exefs) // 0x200, len(rom) // 0x200)
    part = bytes(ncch) + bytes(exefs) + rom
    head = bytearray(0x4000)
    head[0x100:0x104] = b"NCSD"
    struct.pack_into("<II", head, 0x120, 0x4000 // 0x200, len(part) // 0x200)
    path = tmp_path / "made-up.3ds"
    path.write_bytes(bytes(head) + part)
    return path


def test_a_cards_files_listed_and_read(tmp_path):
    path = card(tmp_path)
    with ctr.Card(path) as c:
        assert c.code == "CTR-P-AXXJ"
        found = {f.path: c.read(f) for f in c.files()}
    assert found == {"exefs/.code": b"CODE!!", "exefs/icon": b"ICON", "romfs/top.txt": b"hello",
                     "romfs/msg/a.gmd": b"GMD\0one", "romfs/msg/b.gmd": b"GMD\0two", "romfs/font/f.tex": b"TEX"}
    with ctr.Card(path) as c:
        assert [f.path for f in ctr.select(c.files(), ["romfs/msg/*"])] == ["romfs/msg/a.gmd", "romfs/msg/b.gmd"]
    with pytest.raises(ctr.CtrError, match="encrypted"):
        ctr.Card(card(tmp_path, encrypted=True))
    (tmp_path / "not.3ds").write_bytes(bytes(0x400))
    with pytest.raises(ctr.CtrError, match="NCSD"):
        ctr.Card(tmp_path / "not.3ds")


def test_the_command(tmp_path, capsys):
    path = card(tmp_path)
    out = tmp_path / "out"
    assert main(["ctr", str(path), "--only", "romfs/msg/*", "--extract", str(out)]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["path"] for r in rows] == ["romfs/msg/a.gmd", "romfs/msg/b.gmd"] and rows[0]["size"] == 7
    assert (out / "romfs/msg/b.gmd").read_bytes() == b"GMD\0two"
    assert main(["ctr", str(tmp_path / "missing.3ds")]) == 1
