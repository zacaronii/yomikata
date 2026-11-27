"""`yomikata nitro`: a made-up DS card's files listed and taken out, and LZ77 (0x10, 0x11) undone."""

import json
import struct
import zipfile

import pytest

from yomikata import nitro
from yomikata.cli import main


def made_up_card() -> bytes:
    """A card with a root holding a.bin and a folder font/ holding b.dat (packed, 0x10), an ARM9 program, and one
    ARM9 overlay: the header, then the name table, the allocation table, the overlay table, then the files."""
    packed = lz10_literal(b"made up glyphs " * 3)
    names = {0: b"a.bin", 1: b"b.dat"}
    bodies = [b"made up data", packed, b"ARM9 program", b"overlay zero"]
    # The name table: two directory entries (root, font/), then each one's list.
    root_list = bytes([len(names[0])]) + names[0] + bytes([0x80 | 4]) + b"font" + struct.pack("<H", 0xF001) + b"\0"
    font_list = bytes([len(names[1])]) + names[1] + b"\0"
    dirs = 16
    fnt = (struct.pack("<IHH", dirs, 0, 2) + struct.pack("<IHH", dirs + len(root_list), 1, 0xF000)
           + root_list + font_list)
    fnt_at = 0x200
    fat_at = fnt_at + len(fnt) + (-len(fnt) % 4)
    ovt_at = fat_at + 8 * len(bodies)
    files_at = ovt_at + 32
    card = bytearray(files_at + sum(len(b) + 16 for b in bodies))
    card[0:12] = b"MADE UP GAME"
    card[0x0C:0x10] = b"ZZZJ"
    starts, at = [], files_at
    for body in bodies:
        card[at:at + len(body)] = body
        starts.append((at, at + len(body)))
        at += len(body) + 16
    card[fnt_at:fnt_at + len(fnt)] = fnt
    for k, (s, e) in enumerate(starts):
        struct.pack_into("<II", card, fat_at + 8 * k, s, e)
    struct.pack_into("<IIII", card, 0x40, fnt_at, len(fnt), fat_at, 8 * len(bodies))
    struct.pack_into("<I", card, 0x20, starts[2][0])
    struct.pack_into("<I", card, 0x2C, starts[2][1] - starts[2][0])
    struct.pack_into("<II", card, 0x50, ovt_at, 32)
    struct.pack_into("<I", card, ovt_at, 0)                 # overlay number 0
    struct.pack_into("<I", card, ovt_at + 0x18, 3)          # its file number
    return bytes(card)


def lz10_literal(body: bytes) -> bytes:
    """An LZ77 0x10 stream with every byte as it is."""
    out = bytearray([0x10]) + len(body).to_bytes(3, "little")
    for k in range(0, len(body), 8):
        out += b"\0" + body[k:k + 8]
    return bytes(out)


def lz10_pack(body: bytes) -> bytes:
    """A small greedy LZ77 0x10 packer: the longest earlier match of 3 to 18 bytes within 4 KB, else a byte."""
    out, i = bytearray([0x10]) + len(body).to_bytes(3, "little"), 0
    while i < len(body):
        flags_at, flags = len(out), 0
        out.append(0)
        for bit in range(8):
            if i >= len(body):
                break
            best, back = 0, 0
            for d in range(1, min(i, 0x1000) + 1):
                n = 0
                while n < 18 and i + n < len(body) and body[i + n - d] == body[i + n]:
                    n += 1
                if n > best:
                    best, back = n, d
            if best >= 3:
                flags |= 0x80 >> bit
                out += bytes([(best - 3) << 4 | (back - 1) >> 8, (back - 1) & 0xFF])
                i += best
            else:
                out.append(body[i])
                i += 1
        out[flags_at] = flags
    return bytes(out)


def test_lz10_copies_from_what_is_already_out():
    # "ab", then a copy of length 6 from 2 back: "abababab"; a flag byte 0b00100000 (the third block is a copy).
    stream = bytes([0x10, 8, 0, 0, 0b00100000]) + b"ab" + bytes([(6 - 3) << 4, 2 - 1])
    assert nitro.lz10(stream) == (b"abababab", len(stream))
    assert nitro.lz10(lz10_literal(b"plain"))[0] == b"plain"
    with pytest.raises(nitro.NitroError):
        nitro.lz10(bytes([0x10, 8, 0, 0, 0b10000000, 0x30, 0x05]))    # reaches back before the start


def test_lz11_reads_its_three_forms_of_copy():
    # "xy", then copies of the three forms from 2 back: 3 bytes (two-byte form), 0x11 (three-byte), 0x111 (four).
    three = bytes([((3 - 1) << 4) | 0, 2 - 1])
    long_ = bytes([0x00 | ((0x11 - 0x11) >> 4), (((0x11 - 0x11) & 0xF) << 4) | 0, 2 - 1])
    longer = bytes([0x10 | ((0x111 - 0x111) >> 12), ((0x111 - 0x111) >> 4) & 0xFF, (((0x111 - 0x111) & 0xF) << 4) | 0, 1])
    size = 2 + 3 + 0x11 + 0x111
    stream = bytes([0x11]) + size.to_bytes(3, "little") + bytes([0b00111000]) + b"xy" + three + long_ + longer
    out, took = nitro.lz11(stream)
    assert out == (b"xy" * size)[:size] and took == len(stream)
    # A size of 0 in the header: the size follows in four bytes.
    big = bytes([0x11, 0, 0, 0]) + (2).to_bytes(4, "little") + b"\0" + b"ok"
    assert nitro.lz11(big)[0] == b"ok"


def test_files_and_streams_of_a_made_up_card():
    card = made_up_card()
    found = {f.path: f for f in nitro.files(card)}
    assert set(found) == {"a.bin", "font/b.dat", "arm9.bin", "overlay9/0000.bin"}
    a = found["a.bin"]
    assert card[a.offset:a.offset + a.size] == b"made up data"
    b = card[found["font/b.dat"].offset:][:found["font/b.dat"].size]
    assert nitro.packing(b) == "lz10" and nitro.unpack(b) == b"made up glyphs " * 3
    assert nitro.packing(b"made up data") is None
    assert [f.path for f in nitro.select(list(found.values()), ["font/*"])] == ["font/b.dat"]
    # An archive of two packed sections behind a small table: both found, each where it starts.
    one, two = lz10_pack(b"first section " * 4), lz10_pack(b"second one " * 5)
    assert nitro.unpack(one) == b"first section " * 4
    archive = struct.pack("<I", 2) + bytes(12) + one + bytes(-len(one) % 4) + two
    assert [(at, n) for at, _, n in nitro.streams(archive)] == [(16, 56), (16 + len(one) + (-len(one) % 4), 55)]
    with pytest.raises(nitro.NitroError):
        nitro.files(bytes(0x200))


def test_the_nitro_command_lists_and_takes_out(tmp_path, capsys):
    card = tmp_path / "Made Up (Japan).zip"
    with zipfile.ZipFile(card, "w") as z:
        z.writestr("made-up.nds", made_up_card())
    assert main(["nitro", str(card), "--only", "font/*", "--extract", str(tmp_path / "out"), "--unpack"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows == [{"path": "font/b.dat", "id": 1, "offset": rows[0]["offset"], "size": rows[0]["size"],
                     "packed": "lz10", "unpacked": 45}]
    assert (tmp_path / "out/font/b.dat").read_bytes() == b"made up glyphs " * 3
    assert main(["nitro", str(tmp_path / "missing.nds")]) == 1
