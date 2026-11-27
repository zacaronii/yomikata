"""Where a game puts its people, doors and things, read off its disc (`yomikata placements`): targets in the
game's own world coordinates for walk_3d and meet, instead of guessing them.

The first reader is for the GameCube Zelda stages (Wind Waker; Twilight Princess keeps the same form): a stage
archive (RARC, often packed with Yaz0) holds stage.dzs, a room archive room.dzr; each is a list of chunks (a
four-letter tag, how many entries, where they start), and the placing chunks give each entry an eight-byte name
and its x, y and z as floats: actors (ACTR, and ACT0-ACTb for the actors of a scene or a time of day), scaled
objects (SCOB, SCO0-SCOb), doors (TGDR, DOOR), the player's starting points (PLYR), treasure (TRES) and others.
Names are the game's own actor names (a person is most often two or three letters and a digit); nothing of the
game's text is read.
"""

from __future__ import annotations

import fnmatch
import struct

# Bytes of an entry, by chunk (the layers ACT0-ACTb and SCO0-SCOb are as ACTR and SCOB).
SIZES = {b"ACTR": 0x20, b"PLYR": 0x20, b"TGOB": 0x20, b"TRES": 0x20, b"SCOB": 0x24, b"TGSC": 0x24,
         b"TGDR": 0x24, b"DOOR": 0x24}


class PlacementError(ValueError):
    pass


def yaz0(data: bytes) -> bytes:
    """Nintendo's Yaz0 packing undone: a code byte, eight flags (high first): 1 a byte as it is, 0 a copy (two
    bytes: the length less two in the high four bits, or 0 and a third byte with the length less 0x12; the distance
    less one in the twelve others)."""
    if data[:4] != b"Yaz0":
        raise PlacementError("not Yaz0")
    size, out, p = int.from_bytes(data[4:8], "big"), bytearray(), 16
    try:
        while len(out) < size:
            code = data[p]
            p += 1
            for bit in range(8):
                if len(out) >= size:
                    break
                if code & (0x80 >> bit):
                    out.append(data[p])
                    p += 1
                    continue
                b1, b2 = data[p], data[p + 1]
                p += 2
                back, n = ((b1 & 0x0F) << 8 | b2) + 1, b1 >> 4
                if n == 0:
                    n = data[p] + 0x12
                    p += 1
                else:
                    n += 2
                if back > len(out):
                    raise PlacementError("a copy reaches back before the start")
                for _ in range(n):
                    out.append(out[-back])
    except IndexError:
        raise PlacementError("the Yaz0 stream ends early") from None
    return bytes(out)


def rarc(data: bytes) -> dict[str, bytes]:
    """The files of a RARC archive (a Yaz0-packed one unpacked first), by name."""
    if data[:4] == b"Yaz0":
        data = yaz0(data)
    if data[:4] != b"RARC":
        raise PlacementError("not a RARC archive")
    data_at = struct.unpack_from(">I", data, 0x0C)[0] + 0x20
    _, _, entries, entries_at, _, names_at = struct.unpack_from(">IIIIII", data, 0x20)
    entries_at += 0x20
    names_at += 0x20
    out = {}
    for k in range(entries):
        _, _, kind_name, at, size = struct.unpack_from(">HHIII", data, entries_at + 20 * k)
        if (kind_name >> 24) & 0x02:                     # a directory
            continue
        name_at = names_at + (kind_name & 0xFFFFFF)
        name = data[name_at:data.index(b"\0", name_at)].decode("ascii", "replace")
        out[name] = data[data_at + at:data_at + at + size]
    return out


def chunk_size(tag: bytes) -> int | None:
    if tag in SIZES:
        return SIZES[tag]
    if tag[:3] == b"ACT" and tag[3:] in b"0123456789ab":
        return 0x20
    if tag[:3] == b"SCO" and tag[3:] in b"0123456789ab":
        return 0x24
    return None


def dzx(data: bytes) -> list[dict]:
    """The placed entries of a stage.dzs or room.dzr: each one's chunk, name and x, y, z."""
    if len(data) < 4:
        raise PlacementError("too short for a list of chunks")
    count = struct.unpack_from(">I", data, 0)[0]
    if count > 1024 or 4 + 12 * count > len(data):
        raise PlacementError("not a list of chunks")
    out = []
    for k in range(count):
        tag, n, at = struct.unpack_from(">4sII", data, 4 + 12 * k)
        size = chunk_size(tag)
        if size is None or at + n * size > len(data):
            continue
        for e in range(n):
            entry = data[at + e * size:at + (e + 1) * size]
            x, y, z = struct.unpack_from(">3f", entry, 12)
            out.append({"chunk": tag.decode("ascii", "replace"),
                        "name": entry[:8].split(b"\0")[0].decode("ascii", "replace"),
                        "x": round(x, 2), "y": round(y, 2), "z": round(z, 2)})
    return out


def from_archive(data: bytes) -> list[dict]:
    """Every placed entry of the .dzr and .dzs files in an archive, each with the file it came from."""
    out = []
    for name, body in sorted(rarc(data).items()):
        if name.endswith((".dzr", ".dzs")):
            out += [{**e, "file": name} for e in dzx(body)]
    return out


def select(entries: list[dict], names: list[str], chunks: list[str]) -> list[dict]:
    return [e for e in entries if (not names or any(fnmatch.fnmatch(e["name"], n) for n in names))
            and (not chunks or any(fnmatch.fnmatch(e["chunk"], c) for c in chunks))]
