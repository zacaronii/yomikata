"""Scratch's record of calls (record/calls.jsonl) and the games' budgets (record/budgets.json), for the harness
and for `yomikata play`, which both write into it. `yomikata calls` reads it.

A line is written with one write() to a file opened for appending: the system puts each such write at the
file's end whole, so two writers (two harnesses, or a harness and `yomikata play`) never mix their lines.

A call is written twice: when it begins (`begin`: its tool and game, `ok` null) and when it has its answer
(`finish`: the whole line), both with the same `call` id. `entries` reads them as one. A call whose process
died before its answer (out of memory, or a core given up on) has only the first line, and is still a call:
it reads as one that failed, with `died` set. Lines from before ids were kept are each a call, as they were."""
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


def begin(scratch: Path, entry: dict, call: str) -> None:
    """The first line of a call, written before it does anything: the call is in the record even if the
    process dies in it."""
    append(scratch, {**entry, "ok": None, "call": call, "begun": True})


def finish(scratch: Path, entry: dict, call: str) -> None:
    """The line of a call that has its answer: what `entries` keeps in place of the first."""
    append(scratch, {**entry, "call": call})


def entries(text: str) -> list[dict]:
    """The record's calls, a dict each, in the order they began: a call's two lines read as its second (which
    keeps the first's game when it has none), a call with its first line only as one that died."""
    out: list = []
    where: dict = {}
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue                            # a line cut short by a harness that was stopped
        if not isinstance(entry, dict) or "tool" not in entry:
            continue
        call = entry.get("call")
        if call is None:
            out.append(entry)
        elif entry.get("begun"):
            where[call] = len(out)
            out.append({**entry, "ok": False, "died": True})
        elif call in where:
            first = out[where.pop(call)]
            game = entry.get("game") or first.get("game")
            first.clear()
            first.update({**entry, "game": game})
        else:
            out.append(entry)
    for entry in out:
        entry.pop("begun", None)
    return out


def counts(scratch: Path) -> tuple[int, dict]:
    """The record's size and its calls by game (`entries`): a call is counted once, whether it finished or
    died, whatever wrote it."""
    record = path(scratch)
    try:
        size = record.stat().st_size
        text = record.read_text(encoding="utf-8")
    except OSError:
        return 0, {}
    out: dict = {}
    for entry in entries(text):
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
