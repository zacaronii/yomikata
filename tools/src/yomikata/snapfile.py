"""A snapshot's memory on disk: the bytes as they are, gzipped, or gzipped as what differs from another snapshot.

A run of snaps from one session of the harness differ in a few thousand bytes of millions, so all but the first
are kept as their difference from it: the same file name and the same gzip around it, read through `read` here
by everything that takes snapshots. A snapshot from an inbox is never written this way.

The difference is: the line "YKDELTA1", a line of JSON ({"base": the other file's name, in the same folder;
"size": the memory's size; "sha1": of the other's bytes}), then for each stretch that differs its offset and
length (two 32-bit little-endian numbers) and its bytes."""

from __future__ import annotations

import gzip
import hashlib
import json
import struct
from pathlib import Path

MAGIC = b"YKDELTA1\n"
JOIN = 16               # stretches closer than this are written as one


class SnapError(ValueError):
    pass


def read(path: Path) -> bytes:
    """A snapshot file's bytes, whichever way it is kept."""
    path = Path(path)
    raw = path.read_bytes()
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    if not data.startswith(MAGIC):
        return data
    line, _, rest = data[len(MAGIC):].partition(b"\n")
    head = json.loads(line)
    other = path.with_name(Path(head["base"]).name)
    if not other.is_file():
        raise SnapError(f"{path.name} is kept as what differs from {head['base']}, which isn't beside it")
    base = read(other)
    if base.startswith(MAGIC) or len(base) != head["size"] or hashlib.sha1(base).hexdigest() != head["sha1"]:
        raise SnapError(f"{path.name} is kept as what differs from {head['base']}, which has changed since")
    out, at = bytearray(base), 0
    while at < len(rest):
        offset, length = struct.unpack_from("<II", rest, at)
        out[offset:offset + length] = rest[at + 8:at + 8 + length]
        at += 8 + length
    return bytes(out)


def write(path: Path, data: bytes, base: tuple[str, bytes] | None = None) -> bool:
    """Writes a snapshot file, gzipped: as what differs from `base` (another file's name in the same folder, and
    its bytes) when that is given, is of the same size and makes it smaller; else whole. Says whether it was
    written as a difference."""
    import numpy as np
    path = Path(path)
    if base is not None and len(base[1]) == len(data) and Path(base[0]).name != path.name:
        where = np.flatnonzero(np.frombuffer(data, dtype=np.uint8) != np.frombuffer(base[1], dtype=np.uint8))
        if len(where) < len(data) // 4:
            parts = [MAGIC, json.dumps({"base": Path(base[0]).name, "size": len(data),
                                        "sha1": hashlib.sha1(base[1]).hexdigest()}).encode(), b"\n"]
            if len(where):
                cuts = np.flatnonzero(np.diff(where) > JOIN)
                starts = np.concatenate(([where[0]], where[cuts + 1]))
                ends = np.concatenate((where[cuts], [where[-1]])) + 1
                for a, b in zip(starts.tolist(), ends.tolist()):
                    parts += [struct.pack("<II", a, b - a), data[a:b]]
            path.write_bytes(gzip.compress(b"".join(parts), 1))
            return True
    path.write_bytes(gzip.compress(data, 1))
    return False


def expand(path: Path) -> bool:
    """Rewrites a snapshot kept as a difference as a whole one. Says whether there was anything to do."""
    path = Path(path)
    raw = path.read_bytes()
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    if not data.startswith(MAGIC):
        return False
    path.write_bytes(gzip.compress(read(path), 1))
    return True
