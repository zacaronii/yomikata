"""Finding a game's text in its memory: a known line in every encoding the catalog knows, the stretches that read
as Japanese without knowing a line, and a line whose codes are the game's own (a relative search).

All of it works on bytes, whether live memory or a snap's."""

from __future__ import annotations

import re

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


def string_at(data: bytes, at: int, most: int = 96) -> tuple[str, str] | None:
    """The NUL-ended text that starts at `at`, with its encoding, if those bytes read as text in a standard
    encoding: at least two characters, none of them a control. None otherwise."""
    end = data.find(b"\0", at, at + most)
    raw = data[at:end if end >= 0 else at + most]
    if len(raw) < 2:
        return None
    for kind in ("shift_jis", "euc_jp", "utf8"):
        try:
            text = raw.decode(CODECS[kind])
        except UnicodeDecodeError:
            continue
        if len(text) >= 2 and all(ch.isprintable() or ch in "\n\t" for ch in text):
            return kind, text
    return None


def kana_rule(characters: str, codes: list[bytes]) -> str | None:
    """What the codes of some kana have in common, when the game numbers them in the order of the fifty sounds
    (the plain kana one after the other) or as Unicode does: 'code = place + N', for the rest to be worked out
    from. None when they follow neither, or fewer than two of them are plain hiragana."""
    for name, place in (("the fifty sounds (あ is 0, い is 1, …, ん is 45)", GOJUON.find),
                        ("Unicode's kana (ぁ is 0, あ is 1, ぃ is 2, …)", lambda ch: ord(ch) - 0x3041 if "ぁ" <= ch <= "ん" else -1)):
        for order in ("little", "big"):
            steps = {int.from_bytes(code, order) - place(ch) for ch, code in zip(characters, codes) if place(ch) >= 0}
            if len(steps) == 1 and sum(1 for ch in set(characters) if place(ch) >= 0) >= 2:
                step = steps.pop()
                size = len(codes[0])
                return (f"code = place in {name} + 0x{step:X}" if step >= 0 else f"code = place in {name} - 0x{-step:X}") + \
                       (f", {order}-endian" if size > 1 else "") + \
                       " (it holds for these characters: kana of other rows say whether it holds for all)"
    return None


def _numberings():
    def two(codec):
        def number(ch):
            try:
                raw = ch.encode(codec)
            except UnicodeEncodeError:
                return None
            return int.from_bytes(raw, "big") if len(raw) == 2 else None
        return number

    def jis(ch):
        euc = two("euc_jp")(ch)
        return euc & 0x7F7F if euc is not None and euc >= 0xA1A1 else None

    def kuten(ch):
        code = jis(ch)
        return ((code >> 8) - 0x21) * 94 + (code & 0xFF) - 0x21 if code is not None else None
    return (("JIS", jis), ("Shift-JIS", two("cp932")), ("EUC-JP", two("euc_jp")),
            ("the JIS row and cell counted on (94 a row)", kuten), ("Unicode", ord),
            ("ASCII", lambda ch: ord(ch) if ord(ch) < 0x7F else ord(ch) - 0xFEE0 if 0xFF01 <= ord(ch) <= 0xFF5E else None),
            ("the fifty sounds", lambda ch: GOJUON.index(ch) if ch in GOJUON else None))


def _kind(ch: str) -> str:
    if "ぁ" <= ch <= "ゖ":
        return "hiragana"
    if "ァ" <= ch <= "ヶ":
        return "katakana"
    if ord(ch) < 0x7F or 0xFF01 <= ord(ch) <= 0xFF5E:
        return "letters, digits and ASCII signs"
    if "一" <= ch <= "鿿":
        return "kanji"
    return "signs"


def code_rules(pairs: dict[str, int]) -> list[dict]:
    """What arithmetic ties codes to their characters, kind by kind (hiragana, katakana, letters, kanji, signs)
    and for all kana together: for each standard numbering, whether code - number or code + number is one
    value for every character of the kind. A kind with fewer than two characters says nothing. Where no
    numbering fits a kind but its codes rise or fall with JIS, that is said: a font in JIS order."""
    kinds: dict[str, list[str]] = {}
    for ch in pairs:
        kinds.setdefault(_kind(ch), []).append(ch)
    if "hiragana" in kinds and "katakana" in kinds:
        kinds["kana"] = kinds["hiragana"] + kinds["katakana"]
    out = []
    for kind, chars in kinds.items():
        if len(chars) < 2:
            continue
        row = {"kind": kind, "characters": "".join(chars), "rules": []}
        for name, number in _numberings():
            numbers = [number(ch) for ch in chars]
            if any(n is None for n in numbers):
                continue
            minus = {pairs[ch] - n for ch, n in zip(chars, numbers)}
            plus = {pairs[ch] + n for ch, n in zip(chars, numbers)}
            if len(minus) == 1:
                step = minus.pop()
                row["rules"].append(f"code = {name} {'+' if step >= 0 else '-'} 0x{abs(step):X}")
            if len(plus) == 1:
                row["rules"].append(f"code = 0x{plus.pop():X} - {name}")
        if not row["rules"]:
            jis = dict(_numberings())["JIS"]
            known = sorted((jis(ch), pairs[ch]) for ch in chars if jis(ch) is not None)
            codes = [code for _, code in known]
            if len(known) >= 3 and (codes == sorted(codes) or codes == sorted(codes, reverse=True)):
                row["note"] = ("no arithmetic fits, but the codes " + ("rise" if codes == sorted(codes) else "fall")
                               + " with JIS: numbered as a font in JIS order has them, with characters left out")
            # Some bits of a code may say how it is set, not which character: the same test on the low bits.
            for bits in (15, 14, 13, 12):
                mask = (1 << bits) - 1
                for name, number in _numberings():
                    numbers = [number(ch) for ch in chars]
                    if any(n is None for n in numbers):
                        continue
                    if len({(pairs[ch] & mask) + n for ch, n in zip(chars, numbers)}) == 1:
                        row["rules"].append(f"(code & 0x{mask:X}) = 0x{(pairs[chars[0]] & mask) + numbers[0]:X} - {name}")
                    elif len({(pairs[ch] & mask) - n for ch, n in zip(chars, numbers)}) == 1:
                        step = (pairs[chars[0]] & mask) - numbers[0]
                        row["rules"].append(f"(code & 0x{mask:X}) = {name} {'+' if step >= 0 else '-'} 0x{abs(step):X}")
                if row["rules"]:
                    break
        out.append(row)
    return out


RULE = re.compile(r"^\s*(?:\(?code(?:\s*&\s*0x([0-9A-Fa-f]+)\))?\s*=)?\s*(?:0x([0-9A-Fa-f]+)\s*-\s*(.+?)|(.+?)\s*([+-])\s*0x([0-9A-Fa-f]+))\s*$")


def _characters():
    """Every character a rule may give: JIS's two-byte set and ASCII."""
    out = [chr(n) for n in range(0x21, 0x7F)]
    for lead in range(0xA1, 0xFF):
        for second in range(0xA1, 0xFF):
            try:
                ch = bytes([lead, second]).decode("euc_jp")
            except UnicodeDecodeError:
                continue
            if len(ch) == 1:
                out.append(ch)
    return out


def reader_of(table: dict[bytes, str] | None = None, rules: list[str] | None = None, unit: int = 2,
              big_endian: bool = True, mask: int | None = None):
    """A function from bytes to text for a game's own codes: by a table (code to character), and by rules as
    `code_rules` words them ("code = 0xA420 - JIS", "(code & 0x1FFF) = 0x100 - ASCII", "code = JIS + 0x5CE0").
    `mask` leaves a code's other bits out before the table is asked. A code neither gives is <its hex>."""
    if unit not in (1, 2):
        raise SearchError("unit is 1 or 2")
    order = "big" if big_endian else "little"
    known = {}
    for code, ch in (table or {}).items():
        if len(code) == unit:
            n = int.from_bytes(code, "big")              # a table's codes are written as the bytes lie
            n = int.from_bytes(code, order) if unit > 1 else n
            known[n & mask if mask else n] = ch
    numberings, everything, ways = dict(_numberings()), None, []
    for rule in rules or ():
        m = RULE.match(rule)
        name = (m.group(3) or m.group(4)) if m else None
        if not m or name not in numberings:
            raise SearchError(f"a rule reads like: code = 0xA420 - JIS, or code = JIS + 0x5CE0; the numberings are: "
                              f"{', '.join(numberings)}")
        everything = everything or _characters()
        back = {}
        for ch in everything:
            n = numberings[name](ch)
            if n is not None:
                back.setdefault(n, ch)
        bits = int(m.group(1), 16) if m.group(1) else None
        if m.group(2):
            ways.append((bits, lambda code, k=int(m.group(2), 16), back=back: back.get(k - code)))
        else:
            step = int(m.group(6), 16) * (1 if m.group(5) == "+" else -1)
            ways.append((bits, lambda code, step=step, back=back: back.get(code - step)))

    def read(data: bytes) -> str:
        out = []
        for i in range(0, len(data) - unit + 1, unit):
            code = int.from_bytes(data[i:i + unit], order)
            ch = known.get(code & mask if mask else code)
            for bits, way in ways:
                if ch is None:
                    ch = way(code & bits if bits else code)
            out.append(ch if ch is not None else f"<{code:0{2 * unit}X}>")
        return "".join(out)
    return read


def pattern_at(line: list[int], pattern: str) -> int | None:
    """Where `line` goes as `pattern` says for len(pattern) values on end: the same letter the same value,
    another letter another value, ? any. None when nowhere."""
    n = len(pattern)
    for at in range(len(line) - n + 1):
        letters, used = {}, set()
        for ch, value in zip(pattern, line[at:at + n]):
            if ch == "?":
                continue
            if ch in letters:
                if letters[ch] != value:
                    break
            elif value in used:
                break
            else:
                letters[ch] = value
                used.add(value)
        else:
            return at
    return None


def picture_places(data: bytes, pixels, limit: int = 8) -> list[dict]:
    """Where memory holds a picture as packed rows: `pixels` is rows of small whole numbers (a glyph cut out of
    video memory, 0 for the ground). Tried at 1, 2, 4 and 8 bits a pixel, either end of the byte first, a few
    pixels cut off its left (a row may start inside a byte), rows any number of bytes apart up to 64. The
    picture's values are taken as the font's own, or as ink and ground for 1 bit."""
    import numpy as np
    pixels = np.asarray(pixels, dtype=np.int64)
    if pixels.ndim != 2 or pixels.shape[0] < 3 or pixels.shape[1] < 8:
        raise SearchError("give a picture of at least 3 rows and 8 pixels across")
    ink = (pixels != 0).sum(axis=1)
    top = int(np.argmax(ink[:-2] + ink[1:-1] + ink[2:]))          # the three rows with most in them
    out = []
    for bits in (1, 2, 4, 8):
        values = (pixels != 0).astype(np.int64) if bits == 1 else pixels
        if values.max() >= 1 << bits:
            continue
        per = 8 // bits
        for cut in range(per):
            width = (pixels.shape[1] - cut) // per * per
            if width < 8:
                continue
            rows = values[top:top + 3, cut:cut + width].reshape(3, -1, per)
            for first in ("high", "low"):
                shifts = np.arange(per)[::-1] * bits if first == "high" else np.arange(per) * bits
                packed = [bytes((row << shifts).sum(axis=1).astype(np.uint8)) for row in rows]
                if len(set(packed[0])) < 2:
                    continue
                at = data.find(packed[0])
                while at >= 0 and len(out) < limit:
                    for stride in range(len(packed[0]), 65):
                        if data[at + stride:at + stride + len(packed[1])] == packed[1] and \
                                data[at + 2 * stride:at + 2 * stride + len(packed[2])] == packed[2]:
                            out.append({"at": at - top * stride, "bits": bits, "first": first, "row_bytes": stride,
                                        "cut": cut, "rows_matched": [top, top + 1, top + 2]})
                            break
                    at = data.find(packed[0], at + 1)
    return out


def relative(memories: list[tuple[bytes, str]], unit: int = 1, big_endian: bool = False, order: str = "",
             stride: int = 1, gap: int = 1, limit: int = 20) -> tuple[int, list[tuple[int, dict[str, int]]]]:
    """Where memory could hold a line in codes of the game's own, `unit` bytes each, `stride` cells apart.

    `memories` are moments of the same memory, each with the line on screen then: one place must fit them all.
    A place fits when the same characters have the same code, in a line and from one moment to another, and
    different characters different codes. So one line needs repeated characters, and two moments with lines
    that share characters need none. A line break in a line stands for `gap` cells that aren't looked at; a
    wide space is a character like any other. With `order`, kana must also lie as far apart as in that order,
    counting up or down: "unicode" (as in JIS and Shift-JIS) or "gojuon" (the 46 plain hiragana in a row). Gives how many places
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
    # Kana by kind: hiragana and katakana each keep the order among themselves, but how far the one kind lies
    # from the other is the game's own business.
    groups: dict[bool, list] = {}
    for ch, where in cells_of.items():
        if rank(ch) is not None:
            groups.setdefault(order == "unicode" and rank(ch) >= 0x30A1, []).append((where[0], rank(ch)))
    groups = {k: v for k, v in groups.items() if len(v) > 1}
    if len(same) < 2 and sum(len(v) - 1 for v in groups.values()) < 3:
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
        if groups:                               # as far apart as in the order, upwards or downwards
            up, down = fits.copy(), fits.copy()
            for ranked in groups.values():
                for where, r in ranked[1:]:
                    apart = at(where) - at(ranked[0][0])
                    up &= apart == r - ranked[0][1]
                    down &= apart == ranked[0][1] - r
            fits = up | down
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


def relative_apart(data: bytes, lines: list[str], unit: int = 1, big_endian: bool = False, window: int = 0x10000,
                   limit: int = 10) -> tuple[int, list[tuple[list[int], dict[str, int]]]]:
    """Where one memory could hold several lines in codes of the game's own, each at a place of its own within
    `window` bytes of the first: a scene's lines as they lie in its script. A set of places fits when the same
    character has the same code wherever it stands, in a line and from line to line, and different characters
    different codes. The lines needn't repeat a character, but two of them must share three or more (four with
    one-byte codes is surer). Give stretches the game keeps as one code each: where it may keep kanji or a name
    another way, the kana around them. Gives how many sets fit, and the first: each line's place, and the codes."""
    import numpy as np
    if unit not in (1, 2) or len(lines) < 2:
        raise SearchError("give two or more lines; unit is 1 or 2")
    rows = [[ch for ch in line if not ch.isspace() or ch == "\u3000"] for line in lines]
    if any(len(row) < 2 for row in rows):
        raise SearchError("each line needs two characters or more")
    # The pair of lines that share most characters is looked for first, by those characters' codes.
    share = lambda a, b: [ch for ch in dict.fromkeys(rows[a]) if ch in rows[b]]          # noqa: E731
    first, second = max(((a, b) for a in range(len(rows)) for b in range(a + 1, len(rows))),
                        key=lambda pair: len(share(*pair)))
    shared = share(first, second)[:8 // unit]
    if len(shared) < 3:
        raise SearchError("too little to go by: two of the lines must share three characters or more")
    kind = (">" if big_endian else "<") + ("u1" if unit == 1 else "u2")
    total, sets, seen = 0, [], set()
    for shift in range(unit):
        cells = np.frombuffer(data[shift:len(data) - (len(data) - shift) % unit], dtype=kind).astype(np.int64)

        def fitting(row, known=None, low=0, high=None):
            """Places (in cells) where a row could stand: its repeats agree, and the codes known so far."""
            high = len(cells) if high is None else min(high, len(cells))
            count = high - low - len(row) + 1
            if count <= 0:
                return np.zeros(0, dtype=np.int64)
            at = lambda i: cells[low + i:low + i + count]                               # noqa: E731
            fits = np.ones(count, dtype=bool)
            where: dict[str, int] = {}
            for i, ch in enumerate(row):
                if known and ch in known:
                    fits &= at(i) == known[ch]
                elif ch in where:
                    fits &= at(i) == at(where[ch])
                else:
                    for j in where.values():             # different characters, different codes
                        fits &= at(i) != at(j)
                    where[ch] = i
            return np.flatnonzero(fits) + low

        def key(row, places):
            value = np.zeros(len(places), dtype=np.int64)
            good = np.ones(len(places), dtype=bool)
            taken = []
            for ch in shared:
                code = cells[places + row.index(ch)]
                for other in taken:
                    good &= code != other                # different characters, different codes
                taken.append(code)
                value = (value << (8 * unit)) | code
            return places[good], value[good]

        a_places, a_keys = key(rows[first], fitting(rows[first]))
        b_places, b_keys = key(rows[second], fitting(rows[second]))
        order = np.argsort(b_keys, kind="stable")
        b_places, b_keys = b_places[order], b_keys[order]
        lo, hi = np.searchsorted(b_keys, a_keys, "left"), np.searchsorted(b_keys, a_keys, "right")
        if int((hi - lo).sum()) > 2_000_000:
            raise SearchError("too many places fit two of the lines: give longer lines, or ones that share more")
        span = max(1, window // unit)
        # Every pair of places whose shared characters have the same codes, then those near each other.
        some = np.flatnonzero(hi > lo)
        counts = (hi - lo)[some]
        ps = np.repeat(a_places[some], counts)
        within = np.arange(int(counts.sum())) - np.repeat(np.cumsum(counts) - counts, counts)
        qs = b_places[np.repeat(lo[some], counts) + within]
        near_each_other = np.abs(qs - ps) <= span
        for p, q in zip(ps[near_each_other][:50000].tolist(), qs[near_each_other][:50000].tolist()):
            if True:
                codes: dict[str, int] = {}
                places = {first: p, second: q}
                ok = True
                for row, start in ((rows[first], p), (rows[second], q)):
                    for i, ch in enumerate(row):
                        if codes.setdefault(ch, int(cells[start + i])) != int(cells[start + i]):
                            ok = False
                if not ok or len(set(codes.values())) != len(codes):
                    continue
                for other in range(len(rows)):               # the other lines, near these, with the codes so far
                    if other in places:
                        continue
                    near = fitting(rows[other], codes, max(0, p - span), p + span)
                    found = None
                    for r in near:
                        more = dict(codes)
                        for i, ch in enumerate(rows[other]):
                            more.setdefault(ch, int(cells[int(r) + i]))
                        if len(set(more.values())) == len(more):
                            found, codes = int(r), more
                            break
                    if found is None:
                        ok = False
                        break
                    places[other] = found
                if not ok:
                    continue
                at = tuple(shift + places[k] * unit for k in range(len(rows)))
                if at not in seen:
                    seen.add(at)
                    total += 1
                    if len(sets) < limit:
                        sets.append((list(at), codes))
    return total, sets


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
