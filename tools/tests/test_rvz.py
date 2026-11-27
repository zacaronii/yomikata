"""`yomikata rvz`: made-up GameCube and Wii discs in made-up RVZ and WIA images, their files listed and read."""

import json
import lzma
import struct

import pytest

from yomikata import rvz
from yomikata.cli import main

zstandard = pytest.importorskip("zstandard")
CHUNK = 0x8000


def fst(entries: list, shift: int = 0) -> bytes:
    """A file system table: entries (name, offset, size) for files, (name, None, end) for a directory whose
    entries run up to index `end` (the one after its last; the root is 0)."""
    names, table = bytearray(), bytearray(struct.pack(">III", 0x01000000, 0, len(entries) + 1))
    for name, offset, size in entries:
        at = len(names)
        names += name.encode() + b"\0"
        if offset is None:
            table += struct.pack(">III", 0x01000000 | at, 0, size)
        else:
            table += struct.pack(">III", at, offset >> shift, size)
    return bytes(table + names)


def gc_disc() -> bytes:
    """A GameCube disc of three chunks: its header (the id, the FST's place at 0x424), the FST at 0x2000, a.txt at
    0x3000, and dir/b.bin from near the first chunk's end into the third."""
    disc = bytearray(3 * CHUNK)
    disc[0:6] = b"ZZZJ01"
    table = fst([("a.txt", 0x3000, 7), ("dir", None, 4), ("b.bin", CHUNK - 8, CHUNK + 16)])
    struct.pack_into(">II", disc, 0x424, 0x2000, len(table))
    disc[0x2000:0x2000 + len(table)] = table
    disc[0x3000:0x3007] = b"made up"
    for k in range(CHUNK + 16):
        disc[CHUNK - 8 + k] = k % 251
    return bytes(disc)


def image(disc: bytes, kind: str, compression: int, groups_data: list, raw: list, parts: list = (),
          disc_type: int = 1, props: bytes = b"") -> bytes:
    """A WIA or RVZ image: its two headers, the partition entries, the raw data and group tables, the groups.
    `groups_data` is a list of (stored bytes, compressed flag, packed size)."""
    def pack(data):
        if compression == rvz.ZSTD:
            return zstandard.ZstdCompressor().compress(data)
        if compression == rvz.LZMA2:
            return lzma.compress(data, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 20}])
        return data
    body = bytearray()
    at = 0x200
    part_off = at
    part_bytes = b"".join(bytes(16) + b"".join(struct.pack(">IIII", *seg) for seg in p) for p in parts)
    at += len(part_bytes)
    raw_table = pack(b"".join(struct.pack(">QQII", *r) for r in raw))
    raw_off, at = at, at + len(raw_table)
    group_entries, stored, data_at = [], bytearray(), (at + 0x100 + 3) // 4 * 4
    for data, compressed, packed in groups_data:
        blob = pack(data) if compressed else data
        off = data_at + len(stored)
        flag = 0x80000000 if (compressed and kind == "RVZ") else 0
        group_entries.append(struct.pack(">III", off // 4, flag | len(blob), packed) if kind == "RVZ"
                             else struct.pack(">II", off // 4, len(blob)))
        stored += blob + bytes(-len(blob) % 4)
    group_table = pack(b"".join(group_entries))
    group_off = at
    disc_t = (struct.pack(">IIiI", disc_type, compression, 0, CHUNK) + disc[:0x80]
              + struct.pack(">IIQ", len(parts), 48, part_off) + bytes(20)
              + struct.pack(">IQIIQI", len(raw), raw_off, len(raw_table), len(groups_data), group_off, len(group_table))
              + bytes([len(props)]) + props.ljust(7, b"\0"))
    head = (kind.encode() + b"\x01" + struct.pack(">III", 0x01000000, 0x00030000, len(disc_t)) + bytes(20)
            + struct.pack(">QQ", len(disc), 0) + bytes(20))
    out = bytearray(data_at + len(stored))
    out[0:len(head)] = head
    out[0x48:0x48 + len(disc_t)] = disc_t
    out[part_off:part_off + len(part_bytes)] = part_bytes
    out[raw_off:raw_off + len(raw_table)] = raw_table
    out[group_off:group_off + len(group_table)] = group_table
    out[data_at:] = stored
    return bytes(out)


def rvz_packed(data: bytes, padding_at: int, padding: int) -> bytes:
    """`data` packed as RVZ packs a group: what comes before `padding_at` as it is, then a run of `padding` bytes
    of padding (a seed of 68 bytes; read back as zeros), then the rest."""
    head, tail = data[:padding_at], data[padding_at + padding:]
    return (struct.pack(">I", len(head)) + head + struct.pack(">I", 0x80000000 | padding) + bytes(68)
            + struct.pack(">I", len(tail)) + tail)


def test_a_gamecube_disc_in_rvz(tmp_path):
    disc = bytearray(gc_disc())
    disc[2 * CHUNK + 0x100:2 * CHUNK + 0x200] = bytes(0x100)        # the padding, as it is read back
    third = bytes(disc[2 * CHUNK:])
    groups = [(bytes(disc[0:CHUNK]), True, 0),                     # compressed
              (bytes(disc[CHUNK:2 * CHUNK]), False, 0),            # stored as it is
              (rvz_packed(third, 0x100, 0x100), True, len(rvz_packed(third, 0x100, 0x100)))]
    path = tmp_path / "made-up.rvz"
    path.write_bytes(image(bytes(disc), "RVZ", rvz.ZSTD, groups, [(0x80, len(disc) - 0x80, 0, 3)]))
    d = rvz.open_disc(path)
    assert d.read(0, 6) == b"ZZZJ01" and d.read(2 * CHUNK + 0x100, 4) == bytes(4)
    found = {f.path: f for f in rvz.files(d)}
    assert set(found) == {"a.txt", "dir/b.bin"}
    assert rvz.content(d, found["a.txt"]) == b"made up"
    assert rvz.content(d, found["dir/b.bin"]) == bytes(k % 251 for k in range(CHUNK + 16))      # across groups


def test_a_gamecube_disc_in_wia_with_lzma2_and_the_command(tmp_path, capsys):
    disc = gc_disc()
    groups = [(disc[k * CHUNK:(k + 1) * CHUNK], True, 0) for k in range(3)]
    path = tmp_path / "made-up.wia"
    path.write_bytes(image(disc, "WIA", rvz.LZMA2, groups, [(0x80, len(disc) - 0x80, 0, 3)], props=bytes([16])))
    assert main(["rvz", str(path), "--only", "dir/*", "--extract", str(tmp_path / "out")]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows == [{"path": "dir/b.bin", "offset": CHUNK - 8, "size": CHUNK + 16}]
    assert (tmp_path / "out/dir/b.bin").read_bytes() == bytes(k % 251 for k in range(CHUNK + 16))
    assert main(["rvz", str(tmp_path / "missing.rvz")]) == 1


def test_a_wii_partition_kept_decrypted_without_hashes(tmp_path):
    """A Wii disc whose one partition's data begins at sector 0x40: four groups of a sector each (0x7C00 bytes of
    data: a chunk of 0x8000 on the disc), each after its (empty) list of hash exceptions; offsets in the
    partition's FST divided by 4."""
    data = bytearray(4 * rvz.SECTOR_DATA)
    data[0:6] = b"ZZZJ01"
    table = fst([("msg.bin", 0x8000, 5)], shift=2)
    struct.pack_into(">II", data, 0x424, 0x2000 >> 2, len(table))
    data[0x2000:0x2000 + len(table)] = table
    data[0x8000:0x8005] = b"hello"
    per = rvz.SECTOR_DATA
    groups = [(b"\0\0" + bytes(data[k * per:(k + 1) * per]), True, 0) for k in range(4)]
    head = bytearray(0x80)
    head[0:6] = b"ZZZJ01"
    head[0x18:0x1C] = b"\x5d\x1c\x9e\xa3"
    disc = bytes(head) + bytes(0x80)
    path = tmp_path / "made-up-wii.rvz"
    path.write_bytes(image(disc, "RVZ", rvz.ZSTD, groups, [], parts=[[(0x40, 4, 0, 4), (0, 0, 0, 0)]], disc_type=2))
    d = rvz.open_disc(path)
    found = rvz.files(d)
    assert [(f.path, f.offset, f.partition) for f in found] == [("P0/msg.bin", 0x8000, 0)]
    assert rvz.content(d, found[0]) == b"hello"
