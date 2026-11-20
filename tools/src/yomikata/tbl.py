"""Thingy tables (.tbl), the ROM-hacking format: `0100=学`, `/FFFF` end, `*FFFE` newline.

Keys are hex in memory byte order. Values are short: a table maps codes to characters, never to script text, so
long values are refused (the catalog holds no game text)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_VALUE = 8
LINE = re.compile(r"^([0-9A-Fa-f]{2,8})=(.*)$")


class TableError(ValueError):
    pass


@dataclass
class Table:
    chars: dict[bytes, str] = field(default_factory=dict)
    end: set[bytes] = field(default_factory=set)
    newline: set[bytes] = field(default_factory=set)

    def update(self, other: "Table") -> None:
        self.chars.update(other.chars)
        self.end |= other.end
        self.newline |= other.newline


def parse(text: str, name: str = "table") -> Table:
    t = Table()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r")
        if not line.strip() or line.startswith(("#", ";")):
            continue
        if line[0] in "/*":
            key = line[1:].split()[0] if line[1:].split() else ""
            if not re.fullmatch(r"([0-9A-Fa-f]{2})+", key):
                raise TableError(f"{name}:{n}: bad control line {line!r}")
            (t.end if line[0] == "/" else t.newline).add(bytes.fromhex(key))
            continue
        m = LINE.match(line)
        if not m or len(m.group(1)) % 2:
            raise TableError(f"{name}:{n}: expected HEX=character, got {line!r}")
        if len(m.group(2)) > MAX_VALUE:
            raise TableError(f"{name}:{n}: value longer than {MAX_VALUE} characters (tables map codes, not text)")
        t.chars[bytes.fromhex(m.group(1))] = m.group(2)
    return t


def load(paths: list[Path]) -> Table:
    """Tables applied in order; later ones win."""
    out = Table()
    for p in paths:
        out.update(parse(Path(p).read_text(encoding="utf-8"), str(p)))
    return out


def decode(data: bytes, table: Table, unit: int) -> str:
    out, i = [], 0
    while i + unit <= len(data):
        code = data[i:i + unit]
        if code in table.end:
            out.append("\n\n")
        elif code in table.newline:
            out.append("\n")
        else:
            out.append(table.chars.get(code, "�"))
        i += unit
    return "".join(out)
