"""Finding a game's text in its memory: a known line in every encoding the catalog knows, the stretches that read
as Japanese without knowing a line, and a line whose codes are the game's own (a relative search).

All of it works on bytes, whether live memory or a snap's."""

from __future__ import annotations

from .. import tbl
from ..scan import MIN_RUN, SJIS, UTF8, UTF16, _decoded, _japanese, _kana

CODECS = {"shift_jis": "cp932", "euc_jp": "euc_jp", "utf16le": "utf-16-le", "utf16be": "utf-16-be", "utf8": "utf-8"}
ENCODINGS = (*CODECS, "sjis_le16", "table")
GOJUON = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
ORDERS = ("unicode", "gojuon")


class SearchError(ValueError):
    pass


def needles(text: str, wanted: list[str] | None = None, table: str | None = None) -> dict[str, bytes]:
    """The text as bytes in each encoding that has all its characters. `sjis_le16` is Shift-JIS kept as 16-bit
    little-endian numbers (the code's second byte first); `table` is the game's own, given as a table's text."""
    out = {}
    for name in wanted or [n for n in ENCODINGS if n != "table" or table]:
        if name not in ENCODINGS:
            raise SearchError(f"encodings are: {', '.join(ENCODINGS)}")
        try:
            if name == "table":
                if not table:
                    raise SearchError("give the table's text to search with it")
                back = {}
                for code, ch in tbl.parse(table).chars.items():
                    back.setdefault(ch, code)
                out[name] = b"".join(back[ch] for ch in text)
            elif name == "sjis_le16":
                codes = [ch.encode("cp932") for ch in text]
                out[name] = b"".join(code[::-1] if len(code) == 2 else code + b"\0" for code in codes)
            else:
                out[name] = text.encode(CODECS[name])
        except (UnicodeEncodeError, KeyError):
            pass                                    # a character this encoding hasn't got
        except tbl.TableError as e:
            raise SearchError(str(e)) from None
    return {name: needle for name, needle in out.items() if needle}


def places(data: bytes, needle: bytes, limit: int) -> list[int]:
    hits, at = [], data.find(needle)
    while at >= 0 and len(hits) < limit:
        hits.append(at)
        at = data.find(needle, at + 1)
    return hits


def pieces(text: str, size: int) -> list[str]:
    """The text's stretches of `size` characters between its spaces and line breaks, each once: a line that
    isn't found whole (codes between its characters, a misread) may still be found in parts."""
    out = []
    for word in text.split():
        for i in range(0, max(1, len(word) - size + 1)):
            part = word[i:i + size]
            if len(part) == size and part not in out:
                out.append(part)
    return out


def runs(data: bytes, least: int = MIN_RUN, limit: int = 100) -> list[tuple[int, str, str]]:
    """Stretches that read as Japanese with a fair share of kana, longest first: (offset, encoding, text).
    Shift-JIS, UTF-16 (little-endian), UTF-8, and Shift-JIS kept as 16-bit little-endian numbers."""
    found = []
    for name, pattern, codec, step in (("shift_jis", SJIS, "cp932", 1), ("utf16le", UTF16, "utf-16-le", 2),
                                       ("utf8", UTF8, "utf-8", 1)):
        found += [(at, end, name, text) for at, end, text in _decoded(pattern, data, codec, step)]
    for shift in (0, 1):                            # cells at even addresses, and at odd ones
        body = data[shift:len(data) - (len(data) - shift) % 2]
        swapped = bytearray(len(body))
        swapped[0::2], swapped[1::2] = body[1::2], body[0::2]
        found += [(at + shift, end + shift, "sjis_le16", text)
                  for at, end, text in _decoded(SJIS, bytes(swapped), "cp932") if at % 2 == 0]
    good = [hit for hit in found
            if _japanese(hit[3]) >= least and _kana(hit[3]) >= 0.3 * _japanese(hit[3]) and len(set(hit[3])) >= 4]
    # The same bytes can read as text in two ways (kana as 16-bit cells read one byte on are kana again): the
    # longer reading of a stretch is kept.
    kept: list[tuple[int, int, str, str]] = []
    for hit in sorted(good, key=lambda h: (-len(h[3]), h[0])):
        if not any(hit[0] < other[1] and other[0] < hit[1] for other in kept):
            kept.append(hit)
    return [(at, name, text) for at, _, name, text in kept[:limit]]


def relative(memories: list[tuple[bytes, str]], unit: int = 1, big_endian: bool = False, order: str = "",
             stride: int = 1, gap: int = 1, limit: int = 20) -> tuple[int, list[tuple[int, dict[str, int]]]]:
    """Where memory could hold a line in codes of the game's own, `unit` bytes each, `stride` cells apart.

    `memories` are moments of the same memory, each with the line on screen then: one place must fit them all.
    A place fits when the same characters have the same code, in a line and from one moment to another, and
    different characters different codes. So one line needs repeated characters, and two moments with lines
    that share characters need none. A line break in a line stands for `gap` cells that aren't looked at; a
    wide space is a character like any other. With `order`, kana must also lie as far apart as in that order:
    "unicode" (as in JIS and Shift-JIS) or "gojuon" (the 46 plain hiragana in a row). Gives how many places
    fit, and the first of them with the code each would give each character."""
    import numpy as np
    if unit not in (1, 2, 4) or stride < 1 or gap < 0:
        raise SearchError("unit is 1, 2 or 4; stride is 1 or more")
    if order and order not in ORDERS:
        raise SearchError(f"order is one of: {', '.join(ORDERS)}")
    if not memories or len({len(data) for data, _ in memories}) != 1:
        raise SearchError("the moments must be of the same memory")
    # Each character's cells: (which moment, how many cells from the line's first).
    cells_of: dict[str, list[tuple[int, int]]] = {}
    span = 0
    for k, (_, text) in enumerate(memories):
        at = 0
        for ch in text.strip("\n"):
            if ch == "\n":
                at += gap
            elif ch in " \t\r":
                continue
            else:
                cells_of.setdefault(ch, []).append((k, at))
                at += 1
        span = max(span, (at - 1) * stride + 1)
    count_chars = sum(len(v) for v in cells_of.values())
    if count_chars < 4:
        raise SearchError("give at least 4 characters")
    same = [(where[0], other) for where in cells_of.values() for other in where[1:]]

    def rank(ch):
        if order == "unicode":
            return ord(ch) if "ぁ" <= ch <= "ヶ" else None
        if order == "gojuon":
            return GOJUON.index(ch) if ch in GOJUON else None
        return None
    ranked = [(where[0], rank(ch)) for ch, where in cells_of.items() if rank(ch) is not None]
    ranked = [(w, r) for w, r in ranked if order != "unicode" or (r >= 0x30A1) == (ranked[0][1] >= 0x30A1)]
    if len(same) < 2 and len(ranked) < 4:
        raise SearchError("too little to go by: a line with repeated characters, two moments whose lines share "
                          "characters, or kana and an order")
    kind = {1: "u1", 2: "u2", 4: "u4"}[unit]
    total, hits, seen = 0, [], set()
    size = len(memories[0][0])
    for shift in range(unit):
        cut = size - (size - shift) % unit
        cells = [np.frombuffer(data[shift:cut], dtype=(">" if big_endian else "<") + kind).astype(np.int64)
                 for data, _ in memories]
        count = len(cells[0]) - span + 1
        if count <= 0:
            continue
        at = lambda where: cells[where[0]][where[1] * stride:where[1] * stride + count]      # noqa: E731
        fits = np.ones(count, dtype=bool)
        for one, other in same:
            fits &= at(one) == at(other)
        for where, r in ranked[1:]:
            fits &= at(where) - at(ranked[0][0]) == r - ranked[0][1]
        firsts = [where[0] for where in cells_of.values()]
        fits &= at(firsts[0]) != at(firsts[-1]) if len(firsts) > 1 else fits      # a stretch of one value fits any repeats
        for start in np.nonzero(fits)[0][:5000]:
            codes = {ch: int(cells[where[0][0]][start + where[0][1] * stride]) for ch, where in cells_of.items()}
            where = shift + int(start) * unit
            # The same cells read a byte or more on can fit as well (a filler between them): the first is kept.
            if len(set(codes.values())) == len(codes) and not any(where - k in seen for k in range(1, unit)):
                seen.add(where)
                total += 1
                if len(hits) < limit:
                    hits.append((where, codes))
    return total, hits


def build_words(base: int, moments: list[bytes], platform: str, others: list[bytes] = (), want: int = 3):
    """What in memory could tell this build of the game from another: the same in every moment given (power-on,
    the title, play) and, with `others` (another game or build), different in each of those.

    Where the console keeps the program in memory, words that are calls (a jump-and-link on a MIPS console, a
    branch on a PowerPC one): they hold addresses of the program, which move from build to build. Elsewhere
    (a cartridge, whose program isn't in work RAM), the longest stretches that stay and aren't one value.
    Gives [(address, bytes)], spread through memory, and how many candidates there were."""
    import numpy as np
    if len(moments) < 2 or len({len(m) for m in moments}) != 1 or any(len(o) != len(moments[0]) for o in others):
        raise SearchError("give two or more moments of the same memory")
    first = np.frombuffer(moments[0], dtype=np.uint8)
    stays = np.ones(len(first), dtype=bool)
    for other in moments[1:]:
        stays &= np.frombuffer(other, dtype=np.uint8) == first
    # A call, and to somewhere the console's memory is: data that only looks like one mostly isn't.
    jal = lambda low, high: lambda w: ((w >> 26) == 3) & ((w & 0x03FFFFFF) >= low) & ((w & 0x03FFFFFF) < high)     # noqa: E731
    calls = {"ps1": ("<u4", jal(0x4000, 0x80000)), "psp": ("<u4", jal(0x2200000, 0x2800000)),
             "ps2": ("<u4", jal(0x40000, 0x800000)), "gc": (">u4", lambda w: (w >> 26) == 18),
             "wii": (">u4", lambda w: (w >> 26) == 18)}.get(platform)
    if calls:
        size = len(first) // 4 * 4
        words = np.frombuffer(moments[0][:size], dtype=calls[0])
        good = stays[:size].reshape(-1, 4).all(axis=1) & calls[1](words) & ((words & 0x03FFFFFF) != 0)
        for other in others:
            good &= np.frombuffer(other[:size], dtype=calls[0]) != words
        where = np.nonzero(good)[0]
        if len(where) == 0:
            return [], 0
        picks = [where[int(i)] for i in np.linspace(0, len(where) - 1, min(want, len(where))).round()]
        return [(base + 4 * int(i), moments[0][4 * int(i):4 * int(i) + 4]) for i in dict.fromkeys(picks)], int(len(where))
    plain = stays & (first != 0) & (first != 0xFF)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], plain.view(np.int8), [0]))))
    runs = [(int(b - a), int(a)) for a, b in zip(edges[::2], edges[1::2]) if b - a >= 8]
    runs = [(n, a) for n, a in runs if len(set(moments[0][a:a + n])) >= 4
            and all(o[a:a + n] != moments[0][a:a + n] for o in others)]
    runs.sort(reverse=True)
    return [(base + a, moments[0][a:a + min(n, 16)]) for n, a in runs[:want]], len(runs)
