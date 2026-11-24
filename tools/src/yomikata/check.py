"""Checking an entry's layout against snapshots: what it reads from each, beside what the screen showed.

One line per snapshot goes to a page (`report.html`, with the frame where the snapshot has one) for looking through
by eye, and the counts go to the caller: how many snapshots, how many with text, how many agree with the lines a
reader saw, how many characters didn't decode. The page shows the game's text and frames: it is for a scratch
folder, never for the catalog. The counts hold no text and are what a pull request quotes."""

from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from . import layout, tbl

SPACE = re.compile(r"\s+")


@dataclass
class Row:
    name: str
    read: dict                      # what read_layout gave
    seen: list[str] | None          # the reader's lines; None when the snapshot has none (not the same as no text)
    screen: Path | None
    agreement: float | None = None  # 0..1 between all the text read and the lines, spaces aside; None without lines

    @property
    def message(self) -> str:
        return self.read.get("message") or ""

    @property
    def text(self) -> str:
        """All the text the layout read: the speaker's name, the message, a choice's answers."""
        speaker = self.read.get("speaker")
        parts = [speaker if isinstance(speaker, str) else "", self.message, *(self.read.get("choices") or [])]
        return "\n".join(p for p in parts if p)


@dataclass
class Result:
    rows: list[Row] = field(default_factory=list)

    def counts(self, enough: float = 0.8) -> dict:
        with_lines = [r for r in self.rows if r.seen]
        empty = [r for r in self.rows if r.seen == []]
        return {
            "snapshots": len(self.rows),
            "with a message": sum(1 for r in self.rows if r.message),
            "with a choice's answers": sum(1 for r in self.rows if r.read.get("choices")),
            "fields that couldn't be read": sum(len(r.read.get("errors", {})) for r in self.rows),
            "characters that didn't decode": sum(r.message.count("�") for r in self.rows),
            "with a reader's lines": len(with_lines),
            f"of those, agreeing {round(enough * 100)}% or more": sum(1 for r in with_lines if (r.agreement or 0) >= enough),
            "marked as showing no text": len(empty),
            "of those, reading nothing": sum(1 for r in empty if not r.text),
        }


def agreement(message: str, lines: list[str]) -> float:
    """How much of what the reader saw is in the text read, as a share of the reader's characters, spaces aside. A
    reader's lines hold misreads and may hold more than the message window, so this is a guide, not a verdict."""
    want, got = SPACE.sub("", "".join(lines)), SPACE.sub("", message)
    if not want:
        return 1.0 if not got else 0.0
    return sum(m.size for m in SequenceMatcher(None, want, got, autojunk=False).get_matching_blocks()) / len(want)


def sidecars(ram: Path) -> tuple[dict, Path | None]:
    """A snapshot's .json and frame: `<name>.json` and `<name>.screen.png` (an inbox) or `<name>.png` (a replay)."""
    stem = ram.name.split(".")[0]
    meta_path = ram.with_name(stem + ".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    for name in (stem + ".screen.png", stem + ".png"):
        if ram.with_name(name).is_file():
            return meta, ram.with_name(name)
    return meta, None


def check(entry: dict, snapshots: list[Path], root: Path, base: int | None = None, version: str | None = None) -> Result:
    table = tbl.load([root / t for t in entry["tables"]]) if entry.get("tables") else None
    load = lambda paths: tbl.load([root / t for t in paths])        # noqa: E731
    out = Result()
    for path in snapshots:
        meta, screen = sidecars(path)
        lays = layout.layouts_for(entry, version or meta.get("exe_sha1"), meta.get("id"))
        if not lays:
            raise ValueError(f"{path.name}: the entry has no layout for that version")
        start = base if base is not None else (int(meta["base_address"], 16) if meta.get("base_address")
                                               else layout.RAM_BASE.get(entry["platform"]))
        if start is None:
            raise ValueError(f"{path.name}: give the address its first byte has (--base)")
        ram = layout.Ram.load(path, start)
        read = layout.read_layout(layout.live(lays, ram), entry, ram, table, load)
        # No lines at all, or lines the reader couldn't read: what the screen showed isn't known.
        unknown = meta.get("ocr_unavailable") or ("ocr" not in meta and not meta.get("no_text"))
        seen = None if unknown else list(meta.get("ocr") or [])
        row = Row(path.name.split(".")[0], read, seen, screen)
        if seen is not None:
            row.agreement = agreement(row.text, seen)
        out.rows.append(row)
    return out


def page(result: Result, title: str) -> str:
    """The rows as one HTML page with the frames inside it, so the folder can be looked at and deleted as one file."""
    def cell(text: str) -> str:
        return html.escape(text).replace("\n", "<br>")
    rows = []
    for r in result.rows:
        img = ""
        if r.screen:
            img = f'<img src="data:image/png;base64,{base64.b64encode(r.screen.read_bytes()).decode()}">'
        others = {k: v for k, v in r.read.items() if k != "message"}
        mark = "" if r.agreement is None else f"{round(r.agreement * 100)}%"
        rows.append(f"<tr><td>{html.escape(r.name[:12])}</td><td>{img}</td><td class=t>{cell(r.message)}</td>"
                    f"<td class=t>{cell(chr(10).join(r.seen)) if r.seen is not None else '<i>no lines</i>'}</td>"
                    f"<td>{mark}</td><td><code>{cell(json.dumps(others, ensure_ascii=False)) if others else ''}</code></td></tr>")
    counts = "".join(f"<li>{html.escape(k)}: {v}</li>" for k, v in result.counts().items())
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title><style>"
            "body{font:14px sans-serif;background:#222;color:#eee}table{border-collapse:collapse}"
            "td,th{border:1px solid #555;padding:4px;vertical-align:top}img{width:384px;image-rendering:pixelated}"
            ".t{font-size:18px;white-space:nowrap}</style>"
            f"<h1>{html.escape(title)}</h1><p>Game text and frames: keep out of the catalog, delete when done.</p><ul>{counts}</ul>"
            "<table><tr><th>snapshot</th><th>screen</th><th>the layout reads</th><th>a reader saw</th><th>agree</th>"
            f"<th>other fields</th></tr>{''.join(rows)}</table>")
