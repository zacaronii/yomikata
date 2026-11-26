"""Scratch's record of calls (record/calls.jsonl) and the games' budgets (record/budgets.json), for the harness
and for `yomikata play`, which both write into it. `yomikata calls` reads it.

A line is written with one write() to a file opened for appending: the system puts each such write at the
file's end whole, so two writers (two harnesses, or a harness and `yomikata play`) never mix their lines."""
from __future__ import annotations

import json
import os
from pathlib import Path


def path(scratch: Path) -> Path:
    return Path(scratch) / "record" / "calls.jsonl"


def append(scratch: Path, entry: dict) -> None:
    """Adds a line for a call. A record that can't be written is no reason for a call to fail."""
    data = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        folder = Path(scratch) / "record"
        folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(folder / "calls.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except OSError:
        pass


def counts(scratch: Path) -> tuple[int, dict]:
    """The record's size and its calls by game: every line with a tool is a call, whatever wrote it."""
    record = path(scratch)
    try:
        size = record.stat().st_size
        text = record.read_text(encoding="utf-8")
    except OSError:
        return 0, {}
    out: dict = {}
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and "tool" in entry:
            out[entry.get("game")] = out.get(entry.get("game"), 0) + 1
    return size, out


def budget_of(scratch: Path, game: str | None) -> int | None:
    """The most calls a game may have in the record, or None: no budget."""
    try:
        budgets = json.loads((Path(scratch) / "record" / "budgets.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    limit = budgets.get(game) if isinstance(budgets, dict) and game else None
    return limit if isinstance(limit, int) and not isinstance(limit, bool) else None
