"""The harness's record of calls (scratch record/calls.jsonl), summed by game: how many calls a script took."""

from __future__ import annotations

import json
from pathlib import Path


def summary(path: Path) -> list[dict]:
    """A row for each game, in the order first met: calls, the runs of the harness they came in, how many
    failed, when the first and the last were made, the seconds the tools ran, and the calls by tool. Calls made
    with no game loaded go under no game (None)."""
    games: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue                            # a line cut short by a harness that was stopped
        if not isinstance(entry, dict) or "tool" not in entry:
            continue
        row = games.setdefault(entry.get("game"), {"game": entry.get("game"), "calls": 0, "sessions": set(), "failed": 0,
                                                   "first": entry.get("at"), "last": entry.get("at"), "seconds": 0.0,
                                                   "tools": {}})
        row["calls"] += 1
        row["sessions"].add(entry.get("session"))
        row["failed"] += 0 if entry.get("ok", True) else 1
        row["last"] = entry.get("at") or row["last"]
        row["seconds"] += float(entry.get("seconds") or 0)
        row["tools"][entry["tool"]] = row["tools"].get(entry["tool"], 0) + 1
    return [{**row, "sessions": len(row["sessions"]), "seconds": round(row["seconds"], 1),
             "tools": dict(sorted(row["tools"].items(), key=lambda kv: (-kv[1], kv[0])))} for row in games.values()]
