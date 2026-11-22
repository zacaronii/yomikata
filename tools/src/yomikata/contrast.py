"""Finding a layout's fields in RAM snapshots taken at moments that are known to differ.

`contrast` answers "which byte says so?": given snapshots where something holds (the game waits for a press, a
name plate is up, a choice is open) and snapshots where it doesn't, it lists the bytes that hold one value in all
of the first and never that value in the second. `find_text` answers "where is this string?" for text read off a
frame, in the standard encodings."""

from __future__ import annotations

import numpy as np

from .layout import CODEC


def contrast(same: list[bytes], other: list[bytes]) -> list[tuple[int, int, list[int]]]:
    """(offset, the value in every `same` snapshot, the values seen in `other`), in address order."""
    size = min(map(len, same + other))
    a = np.stack([np.frombuffer(s, dtype=np.uint8, count=size) for s in same])
    b = np.stack([np.frombuffer(s, dtype=np.uint8, count=size) for s in other])
    hits = np.flatnonzero((a == a[0]).all(axis=0) & (b != a[0]).all(axis=0))
    return [(int(h), int(a[0, h]), sorted(set(b[:, h].tolist()))) for h in hits]


def find_text(ram: bytes, text: str, absent: list[bytes] = (), limit: int = 20) -> dict[str, list[int]]:
    """Charset -> offsets where the text stands in that encoding (charsets that can't write it are left out).
    Offsets where an `absent` snapshot has it too are skipped: that leaves the buffers of the moment, not the
    script the game keeps loaded."""
    out = {}
    for charset, codec in CODEC.items():
        try:
            pattern = text.encode(codec)
        except UnicodeEncodeError:
            continue
        at, i = [], ram.find(pattern)
        while i >= 0 and len(at) < limit:
            if not any(other[i:i + len(pattern)] == pattern for other in absent):
                at.append(i)
            i = ram.find(pattern, i + 1)
        if at:
            out[charset] = at
    return out
