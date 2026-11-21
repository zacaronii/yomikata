"""Reference reader for an entry's `layouts`: what the game shows, read from memory (docs/format.md, Layouts).

Clients can compare their output with `read_layout()`. Memory is any `read(address, size) -> bytes`; `Ram` wraps
a snapshot. Nothing here hooks or writes: a layout only says where to look."""

from __future__ import annotations

import gzip
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


def read_int(spec: dict, read, endian: str = "little") -> int:
    fmt = INT[spec.get("type", "u8")]
    size = struct.calcsize(fmt)
    (value,) = struct.unpack(("<" if endian == "little" else ">") + fmt, read(resolve(spec, read, endian), size))
    return value


def read_flag(spec: dict, read, endian: str = "little") -> bool:
    value = read_int(spec, read, endian)
    return value == spec["equals"] if "equals" in spec else value != 0


def _char_len(data: bytes, i: int, charset: str, unit: int) -> int:
    """Bytes in the character starting at i (so terminators are only matched at character boundaries)."""
    b = data[i]
    if charset == "shift_jis":
        return 2 if 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC else 1
    if charset == "euc_jp":
        return 3 if b == 0x8F else 2 if b >= 0x8E else 1
    if charset == "utf8":
        return 4 if b >= 0xF0 else 3 if b >= 0xE0 else 2 if b >= 0xC0 else 1
    if charset in ("utf16le", "utf16be"):
        return 2
    return unit


def _cut(data: bytes, charset: str, unit: int, terminator: bytes | None) -> bytes:
    i = 0
    while i < len(data):
        if terminator and data[i:i + len(terminator)] == terminator:
            break
        n = _char_len(data, i, charset, unit)
        if i + n > len(data):                       # a character cut off by max_bytes or the stride
            break
        i += n
    return data[:i]


def _decode(data: bytes, charset: str, entry: dict, table: tbl.Table | None) -> str:
    if charset == "table":
        if table is None:
            raise LayoutError("a table charset needs the entry's tables")
        return tbl.decode(data, table, entry.get("unit", 2)).rstrip("\n")
    return data.decode(CODEC[charset], errors="replace")


def read_text(spec: dict, read, entry: dict, table: tbl.Table | None = None, endian: str = "little") -> str:
    charset = spec["charset"]
    unit = entry.get("unit", 2) if charset == "table" else 1
    term = bytes.fromhex(spec["terminator"]) if "terminator" in spec else None
    start = resolve(spec, read, endian)
    rows = spec.get("rows", {"count": 1, "stride": spec["max_bytes"]})
    size = min(spec["max_bytes"], rows["stride"])
    if "length" in spec:
        size = min(size, max(0, read_int(spec["length"], read, endian)))
    lines = []
    for r in range(rows["count"]):
        raw = _cut(read(start + r * rows["stride"], size), charset, unit, term)
        lines.append(_decode(raw, charset, entry, table))
    if "rows" in spec:
        while lines and not lines[-1]:
            lines.pop()
    text = "\n".join(lines)
    return apply(text, spec.get("cleanup", []))


def pick(entry: dict, exe_sha1: str | None = None) -> dict | None:
    """The layout for this version: one that lists it, else one that lists no versions."""
    general = None
    for lay in entry.get("layouts", []):
        if exe_sha1 and exe_sha1 in lay.get("versions", []):
            return lay
        if not lay.get("versions") and general is None:
            general = lay
    return general


def read_layout(layout: dict, entry: dict, read, table: tbl.Table | None = None) -> dict:
    """Everything the layout names, as plain values. A field that can't be read is left out and listed in `errors`."""
    endian = layout.get("endian", "little")
    out: dict = {}
    errors: dict[str, str] = {}

    def attempt(name, fn):
        try:
            out[name] = fn()
        except (LayoutError, struct.error, KeyError) as e:
            errors[name] = str(e)

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
            rows = read_text(layout["choices"], read, entry, table, endian).split("\n")
            return rows[:out["choice_count"]] if "choice_count" in out else rows
        attempt("choices", choices)
    if "message" in out and "shown" in out:
        out["on_screen"] = out["message"].replace("\n", "")[:out["shown"]]
    if errors:
        out["errors"] = errors
    return out
