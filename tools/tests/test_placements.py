"""`yomikata placements`: a made-up stage archive's placed actors, doors and starting points, read as targets."""

import struct

import pytest

from yomikata import placements


def dzx(chunks: list[tuple[bytes, list[tuple[str, float, float, float]]]]) -> bytes:
    """A made-up .dzr: its chunk list, then each chunk's entries (a name, parameters, x, y, z, the rest zero)."""
    head = bytearray(struct.pack(">I", len(chunks)))
    body = bytearray()
    at = 4 + 12 * len(chunks)
    for tag, entries in chunks:
        size = placements.chunk_size(tag)
        head += struct.pack(">4sII", tag, len(entries), at + len(body))
        for name, x, y, z in entries:
            entry = name.encode().ljust(8, b"\0") + bytes(4) + struct.pack(">3f", x, y, z)
            body += entry.ljust(size, b"\0")
    return bytes(head + body)


def rarc_of(files: dict[str, bytes]) -> bytes:
    """A made-up RARC: its header, one directory node, the file entries, the names, then the data."""
    names = bytearray(b".\0..\0")
    entries, data = bytearray(), bytearray()
    for k, (name, body) in enumerate(files.items()):
        at = len(names)
        names += name.encode() + b"\0"
        entries += struct.pack(">HHIII", k, 0, (0x11 << 24) | at, len(data), len(body)) + bytes(4)
        data += body + bytes(-len(body) % 32)
    nodes_at, entries_at = 0x20, 0x20                 # offsets from 0x20: the entries right after the info block
    names_at = entries_at + len(entries)
    data_at = 0x40 + len(entries) + len(names)
    head = b"RARC" + struct.pack(">IIIIII", 0, data_at - 0x20 + 0x20, data_at - 0x20, len(data), 0, 0) + bytes(4)
    head = head.ljust(0x20, b"\0")
    info_block = struct.pack(">IIIIII", 1, nodes_at, len(files), entries_at, len(names), names_at).ljust(0x20, b"\0")
    return bytes(head + info_block + entries + names + data)


def test_a_room_s_placements_and_yaz0():
    room = dzx([(b"ACTR", [("Pig", 10.0, 1.0, 20.0)]), (b"ACT0", [("Ab1", -5.5, 2.0, 7.25)]),
                (b"TGDR", [("KNOB00D", 100.0, 0.0, -50.0)]), (b"PLYR", [("Link", 1.0, 0.0, 1.0)]),
                (b"RPAT", [])])
    found = placements.dzx(room)
    assert [(e["chunk"], e["name"], e["x"], e["z"]) for e in found] == [
        ("ACTR", "Pig", 10.0, 20.0), ("ACT0", "Ab1", -5.5, 7.25), ("TGDR", "KNOB00D", 100.0, -50.0),
        ("PLYR", "Link", 1.0, 1.0)]
    assert [e["name"] for e in placements.select(found, ["A*"], [])] == ["Ab1"]
    assert [e["name"] for e in placements.select(found, [], ["TGDR"])] == ["KNOB00D"]
    # Yaz0: three bytes as they are, then a copy of six from three back.
    packed = b"Yaz0" + (9).to_bytes(4, "big") + bytes(8) + bytes([0b11100000]) + b"abc" + bytes([(6 - 2) << 4, 3 - 1])
    assert placements.yaz0(packed) == b"abcabcabc"
    with pytest.raises(placements.PlacementError):
        placements.dzx(b"\xff\xff\xff\xff")


def test_an_archive_s_room_file():
    archive = rarc_of({"room.dzr": dzx([(b"ACT0", [("Ab1", 3.0, 0.0, 4.0)])]), "model.bdl": b"made up"})
    entries = placements.from_archive(archive)
    assert entries == [{"chunk": "ACT0", "name": "Ab1", "x": 3.0, "y": 0.0, "z": 4.0, "file": "room.dzr"}]
