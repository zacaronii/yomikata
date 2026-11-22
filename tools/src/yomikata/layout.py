"""Reference reader for an entry's `layouts`: what the game shows, read from memory (docs/format.md, Layouts).

Clients can compare their output with `read_layout()`, or run tools/tests/layout-vectors. Memory is any
`read(address, size) -> bytes`; `Ram` wraps a snapshot. Nothing here hooks or writes: a layout only says where to look."""

from __future__ import annotations

import gzip
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from . import tbl
from .cleanup import apply

INT = {"u8": "B", "u16": "H", "u32": "I", "s8": "b", "s16": "h", "s32": "i"}
RAM_BASE = {"ps1": 0x80000000, "psp": 0x08000000, "ps2": 0x00000000, "nds": 0x02000000, "gba": 0x02000000}
CODEC = {"shift_jis": "cp932", "euc_jp": "euc_jp", "utf16le": "utf-16-le", "utf16be": "utf-16-be", "utf8": "utf-8"}


class LayoutError(ValueError):
    pass


@dataclass
class Ram:
    """A snapshot of memory starting at `base`. Reads outside it raise LayoutError."""
    base: int
    data: bytes

    @classmethod
    def load(cls, path: Path, base: int) -> "Ram":
        raw = Path(path).read_bytes()
        return cls(base, gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)

    def __call__(self, address: int, size: int) -> bytes:
        start = address - self.base
        if start < 0 or start + size > len(self.data):
            raise LayoutError(f"0x{address:08X}+{size} is outside memory")
        return self.data[start:start + size]


def _num(s: str) -> int:
    return int(s, 16)


def resolve(spec: dict, read, endian: str = "little") -> int:
    """The address a field names: fixed, or base then pointer + offset for each offset."""
    if "address" in spec:
        return _num(spec["address"])
    addr = _num(spec["base"])
    for off in spec["offsets"]:
        (ptr,) = struct.unpack("<I" if endian == "little" else ">I", read(addr, 4))
        addr = ptr + _num(off)
    return addr


def read_int(spec: dict, read, endian: str = "little", index: int = 0) -> int:
    """The number a spec names; `index` picks one of a run of them, each right after the last."""
    fmt = INT[spec.get("type", "u8")]
    size = struct.calcsize(fmt)
    address = resolve(spec, read, endian) + index * size
    (value,) = struct.unpack(("<" if endian == "little" else ">") + fmt, read(address, size))
    return value


def read_flag(spec: dict, read, endian: str = "little") -> bool:
    value = read_int(spec, read, endian)
    if "mask" in spec:                              # only these bits count (of the value as stored, so never negative)
        value &= spec["mask"]
    return value == spec["equals"] if "equals" in spec else value != 0


def _code_len(data: bytes, i: int, codes: dict | None) -> int:
    """Bytes in the code starting at i, or 0 when none of `codes` starts there. The longest lead wins."""
    leads = [lead for lead in codes or () if data.startswith(lead, i)]
    return codes[max(leads, key=len)] if leads else 0


def _char_len(data: bytes, i: int, charset: str, unit: int, codes: dict | None = None) -> int:
    """Bytes in the character starting at i (so terminators are only matched at character boundaries)."""
    n = _code_len(data, i, codes)
    if n:
        return n                                    # a code and its arguments are one character
    b = data[i]
    if charset == "shift_jis":
        return 2 if 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC else 1
    if charset == "euc_jp":
        return 3 if b == 0x8F else 2 if b >= 0x8E else 1
    if charset == "utf8":
        return 4 if b >= 0xF0 else 3 if b >= 0xE0 else 2 if b >= 0xC0 else 1
    if charset in ("utf16le", "utf16be"):
        hi = charset == "utf16le"                   # where a unit's high byte is
        pair = i + 3 < len(data) and 0xD8 <= data[i + hi] <= 0xDB and 0xDC <= data[i + 2 + hi] <= 0xDF
        return 4 if pair else 2
    return unit


def _cut(data: bytes, charset: str, unit: int, terminator: bytes | None, chars: int | None = None,
         codes: dict | None = None) -> bytes:
    i = count = 0
    while i < len(data) and (chars is None or count < chars):
        if terminator and data[i:i + len(terminator)] == terminator:
            break
        n = _char_len(data, i, charset, unit, codes)
        if i + n > len(data):                       # a character cut off by max_bytes or the stride
            break
        i += n
        count += 1
    return data[:i]


def _page(data: bytes, charset: str, unit: int, mark: bytes, cursor: int, codes: dict | None = None) -> bytes:
    """The page a script cursor is on. `data` is split at each `mark` that starts a character; the page is the
    last one with anything in it that starts at or before the cursor (an offset into data). A cursor outside the
    text is on its last page: a game may drop its cursor once the last page is typed out."""
    pages, start, i = [], 0, 0
    while i < len(data):
        if data[i:i + len(mark)] == mark:
            pages.append((start, i))
            start = i = i + len(mark)
        else:
            i += _char_len(data, i, charset, unit, codes)
    pages.append((start, len(data)))
    inside = 0 <= cursor <= len(data)
    full = [(a, b) for a, b in pages if b > a and (a <= cursor or not inside)]
    return data[slice(*full[-1])] if full else b""


def _script(both: bytes, size: int, unit: int, mark: bytes, ends: set[bytes], breaks: set[bytes],
            codes: dict | None, rows: bool = False) -> list[bytes]:
    """The page a script's reading position is on, as far as it has been read: in one piece, or with `rows` a
    piece for each of its texts. `both` is `size` bytes of script up to the position, then what follows it, as
    long as `mark`. A text starts after a mark and stops at an end code; a mark right after an end code that has
    no page break before it carries the page on. Nothing when the position isn't in a text, or a text is about
    to start a new page."""
    data, ahead = both[:size], both[size:]
    code = lambda i: data[i:i + unit] if i >= 0 else b""
    joins = lambda q: code(q - unit) in ends and code(q - 2 * unit) not in breaks      # a mark at q carries on
    p, start = len(data), None
    if ahead == mark and not joins(p):
        return []
    if code(p - unit) in ends:                      # the text has just ended
        p -= unit
    while True:
        while p >= len(mark) and data[p - len(mark):p] != mark and code(p - unit) not in ends:
            p -= unit
        if p < len(mark) or data[p - len(mark):p] != mark:
            break                                   # out of what was read, or a second end code: not a text
        start, q = p, p - len(mark)
        if not joins(q):
            break
        p = q - unit                                # on through the text before
    if start is None:
        return []
    out, i = [bytearray()], start
    while i + unit <= len(data):
        n = _code_len(data, i, codes)
        if not n and code(i) in ends and data.startswith(mark, i + unit):
            i += unit + len(mark)                   # where two texts of the page join
            if rows:
                out.append(bytearray())
            continue
        out[-1] += data[i:i + (n or unit)]
        i += n or unit
    return [bytes(piece) for piece in out]


def _char(code: bytes, charset: str) -> str:
    """One character. Bytes that aren't one are U+FFFD, whatever a codec would make of them: the only single
    bytes from 0x80 up that mean anything are Shift-JIS's half-width kana."""
    if len(code) == 1 and code[0] >= 0x80 and not (charset == "shift_jis" and 0xA1 <= code[0] <= 0xDF):
        return "\ufffd"
    try:
        return code.decode(CODEC[charset])
    except UnicodeDecodeError:
        return "\ufffd"


def _decode(data: bytes, charset: str, entry: dict, table: tbl.Table | None, codes: dict | None = None) -> str:
    """Character by character (the boundaries `_cut` uses), so a bad byte costs one U+FFFD in every client,
    not whatever a platform's decoder does about it."""
    if charset == "table":
        if table is None:
            raise LayoutError("no table to decode with")
        return tbl.decode(data, table, entry.get("unit", 2), entry.get("controls"), codes).rstrip("\n")
    out, i = [], 0
    while i < len(data):
        n = _code_len(data, i, codes)
        if not n:                                   # a code is nothing in the text
            n = _char_len(data, i, charset, 1)
            out.append(_char(data[i:i + n], charset))
        i += n
    return "".join(out)


def read_text(spec: dict, read, entry: dict, table: tbl.Table | None = None, endian: str = "little") -> str:
    if "when" in spec and not read_flag(spec["when"], read, endian):
        return ""                                   # what the memory holds isn't on screen now
    charset = spec["charset"]
    unit = entry.get("unit", 2) if charset == "table" else 1
    term = bytes.fromhex(spec["terminator"]) if "terminator" in spec else None
    codes = {bytes.fromhex(lead): n for lead, n in spec.get("codes", {}).items()}
    start = resolve(spec, read, endian)
    if "script" in spec:
        if table is None:
            raise LayoutError("no table to decode with")
        at = read_int(spec["script"]["offset"], read, endian)
        size = max(0, min(spec["max_bytes"], at))
        means = entry.get("controls", {})
        ends = table.end | {bytes.fromhex(c) for c, what in means.items() if what == "end"}
        breaks = {bytes.fromhex(c) for c, what in means.items() if what in ("page", "clear")}
        mark = bytes.fromhex(spec["script"]["open"])
        texts = []
        if size:                                    # at or before the script's start there is nothing to read
            texts = _script(read(start + at - size, size + len(mark)), size, unit, mark, ends, breaks, codes,
                            spec["script"].get("texts") == "rows")
        text = "\n".join(_decode(raw, charset, entry, table, codes) for raw in texts)
        return apply(apply(text, spec.get("cleanup", [])), entry.get("cleanup", []))
    rows = spec.get("rows", {"count": 1, "stride": spec["max_bytes"]})
    lines = []
    for r in range(rows["count"]):
        size, chars = min(spec["max_bytes"], rows["stride"]), None
        for length, index in ((spec.get("length"), 0), (rows.get("lengths"), r)):
            if length is None:
                continue
            n = max(0, read_int(length, read, endian, index))
            if length.get("counts") == "characters":
                chars = n if chars is None else min(chars, n)
            else:
                size = min(size, n)
        raw = _cut(read(start + r * rows["stride"], size), charset, unit, term, chars, codes)
        if "pages" in spec:
            cursor = read_int(spec["pages"]["cursor"], read, endian) - start
            raw = _page(raw, charset, unit, bytes.fromhex(spec["pages"]["break"]), cursor, codes)
        lines.append(_decode(raw, charset, entry, table, codes))
    if "rows" in spec:
        while lines and not lines[-1]:
            lines.pop()
    text = "\n".join(lines)
    return apply(apply(text, spec.get("cleanup", [])), entry.get("cleanup", []))      # its own, then the entry's


def version_for(entry: dict, release_id: str | None) -> str | None:
    """For a reader that can't hash the executable: the `exe_sha1` of the only version listed for its release id
    (a version without `id` belongs to the entry's own). None when the id has several versions, or none."""
    mine = [v for v in entry.get("versions", []) if v.get("id", entry.get("id")) == release_id]
    return mine[0].get("exe_sha1") if len(mine) == 1 else None


_WHERE = {"address", "base", "offsets"}
_NUMBER = _WHERE | {"type", "equals", "mask"}
_LENGTH = _WHERE | {"type", "counts"}
_TEXT = _WHERE | {"charset", "max_bytes", "terminator", "length", "rows", "pages", "script", "codes", "when",
                  "cleanup"}
_HEX = re.compile(r"([0-9A-Fa-f]{2}){1,4}")
_FIELDS = {"message": "text", "choices": "text", "speaker": "either", "shown": "number", "waiting": "number",
           "choosing": "number", "choice_count": "number", "choice_cursor": "number"}
_OTHER = {"versions", "endian", "poll", "speaker_names", "notes", "when", "tables"}


def _number_ok(spec, keys=_NUMBER) -> bool:
    return isinstance(spec, dict) and set(spec) <= keys and spec.get("type", "u8") in INT \
        and spec.get("counts", "bytes") in ("bytes", "characters")


_BYTES = re.compile(r"([0-9A-Fa-f]{2})+")


def _text_ok(spec) -> bool:
    if not isinstance(spec, dict) or not set(spec) <= _TEXT or spec.get("charset") not in (*CODEC, "table"):
        return False
    rows, pages, codes, script = spec.get("rows", {}), spec.get("pages"), spec.get("codes"), spec.get("script")
    if script is not None and not (
            spec["charset"] == "table" and not set(spec) & {"rows", "pages", "terminator", "length"}
            and isinstance(script, dict) and {"open", "offset"} <= set(script) <= {"open", "offset", "texts"}
            and script.get("texts", "rows") == "rows"
            and isinstance(script["open"], str) and _BYTES.fullmatch(script["open"])
            and _number_ok(script["offset"], _WHERE | {"type"})):
        return False
    if codes is not None and not (isinstance(codes, dict) and codes and all(
            isinstance(lead, str) and _HEX.fullmatch(lead) and type(n) is int and len(lead) // 2 <= n <= 16
            for lead, n in codes.items())):
        return False
    if pages is not None and not ("rows" not in spec and isinstance(pages, dict) and set(pages) == {"break", "cursor"}
                                  and isinstance(pages["break"], str) and _BYTES.fullmatch(pages["break"])
                                  and _number_ok(pages["cursor"], _WHERE | {"type"})):
        return False
    return set(rows) <= {"count", "stride", "lengths"} \
        and all(_number_ok(n, _LENGTH) for n in (spec.get("length"), rows.get("lengths")) if n is not None) \
        and ("when" not in spec or _number_ok(spec["when"]))


def _switch_ok(spec) -> bool:
    return isinstance(spec, dict) and set(spec) == {"by", "sets"} and _number_ok(spec["by"], _WHERE | {"type"}) \
        and isinstance(spec["sets"], dict) and bool(spec["sets"]) and all(
            isinstance(n, str) and re.fullmatch(r"-?[0-9]{1,10}", n) and isinstance(paths, list) and paths
            and all(isinstance(t, str) for t in paths) for n, paths in spec["sets"].items())


def understood(layout: dict) -> bool:
    """Whether this reader knows every key in the layout. A newer key can change what the others mean (a text
    without its `when` would show what is no longer on screen), so a reader leaves such a layout alone."""
    if not set(layout) <= set(_FIELDS) | _OTHER or layout.get("endian", "little") not in ("little", "big"):
        return False
    if "when" in layout and not _number_ok(layout["when"]):
        return False
    if "tables" in layout and not _switch_ok(layout["tables"]):
        return False
    for key, kind in _FIELDS.items():
        if key in layout:
            spec = layout[key]
            is_text = isinstance(spec, dict) and "charset" in spec
            if not (_text_ok(spec) if kind == "text" or (kind == "either" and is_text) else _number_ok(spec)):
                return False
    return True


def pick(entry: dict, exe_sha1: str | None = None, release_id: str | None = None) -> dict | None:
    """The layout for this version: one that lists it, else one that lists no versions. Without a hash, the
    version is the only one listed for `release_id`, if there is just one. Layouts with keys this reader
    doesn't know are passed over."""
    exe_sha1 = exe_sha1 or version_for(entry, release_id)
    general = None
    for lay in entry.get("layouts", []):
        if not understood(lay):
            continue
        if exe_sha1 and exe_sha1 in lay.get("versions", []):
            return lay
        if not lay.get("versions") and general is None:
            general = lay
    return general


def read_layout(layout: dict, entry: dict, read, table: tbl.Table | None = None, load=None) -> dict:
    """Everything the layout names, as plain values. A field that can't be read is left out and listed in `errors`.
    While the layout's own `when` is false nothing else is read: texts are empty, flags false, numbers left out.
    `table` is the entry's tables. A layout with its own `tables` reads with `load(paths)` instead: the entry's
    table files and then the set's."""
    endian = layout.get("endian", "little")
    out: dict = {}
    errors: dict[str, str] = {}

    if "when" in layout:
        try:
            live = read_flag(layout["when"], read, endian)
        except (LayoutError, struct.error, KeyError) as e:
            return {"errors": {"when": str(e)}}
        if not live:
            out = {name: "" for name in ("message", "speaker") if "charset" in layout.get(name, {})}
            out.update({name: False for name in ("waiting", "choosing") if name in layout})
            if "choices" in layout:
                out["choices"] = []
            return out

    def attempt(name, fn):
        try:
            out[name] = fn()
        except (LayoutError, struct.error, KeyError) as e:
            errors[name] = str(e)

    if "tables" in layout:                          # the set its number names after the entry's, or no table
        try:
            more = layout["tables"]["sets"].get(str(read_int(layout["tables"]["by"], read, endian)))
        except (LayoutError, struct.error):
            more = None
        table = load(entry.get("tables", []) + more) if more and load else None
    if "message" in layout:
        attempt("message", lambda: read_text(layout["message"], read, entry, table, endian))
    if "speaker" in layout:
        sp = layout["speaker"]
        if "charset" in sp:
            attempt("speaker", lambda: read_text(sp, read, entry, table, endian))
        else:
            def speaker():
                n = read_int(sp, read, endian)
                return layout.get("speaker_names", {}).get(str(n), n)
            attempt("speaker", speaker)
    for name in ("shown", "choice_count", "choice_cursor"):
        if name in layout:
            attempt(name, lambda name=name: read_int(layout[name], read, endian))
    for name in ("waiting", "choosing"):
        if name in layout:
            attempt(name, lambda name=name: read_flag(layout[name], read, endian))
    if "choices" in layout:
        def choices():
            text = read_text(layout["choices"], read, entry, table, endian)
            rows = text.split("\n") if text else []
            return rows[:max(0, out["choice_count"])] if "choice_count" in out else rows
        attempt("choices", choices)
    if "message" in out and "shown" in out:
        out["on_screen"] = out["message"].replace("\n", "")[:max(0, out["shown"])]
    if errors:
        out["errors"] = errors
    return out
