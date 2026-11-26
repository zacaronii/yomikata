"""The harness's record of calls (scratch record/calls.jsonl), summed by game: how many calls a script took."""

from __future__ import annotations

from pathlib import Path

from .harness.record import entries as _entries


def summary(path: Path) -> list[dict]:
    """A row for each game, in the order first met: calls, the runs of the harness they came in, how many
    failed, when the first and the last were made, the seconds the tools ran, and the calls by tool. Calls made
    with no game loaded go under no game (None)."""
    games: dict = {}
    for entry in _entries(path.read_text(encoding="utf-8")):    # a call once, also one whose process died
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


def _when(at: str | None):
    from datetime import datetime
    try:
        return datetime.strptime(at or "", "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def trend(path: Path) -> list[dict]:
    """What each game's script cost up to its bar, in the order the games were begun: the calls, the runs of
    the harness and the minutes on the clock from the game's first call to the comparison that met the bar,
    and what came after. The comparison is the first one by scenes whose outcome the record has as met; in a
    record from before outcomes were kept, the last one by scenes (`met` is then None: it isn't known). A
    game never compared by scenes has no bar yet, and its row counts everything so far."""
    games: dict = {}
    for entry in _entries(path.read_text(encoding="utf-8")):
        if entry.get("game"):
            games.setdefault(entry["game"], []).append(entry)
    rows = []
    for game, entries in games.items():
        by_scenes = [i for i, e in enumerate(entries) if e["tool"] == "compare" and "scenes" in (e.get("arguments") or {})]
        met = [i for i in by_scenes if (e := entries[i].get("outcome")) and e.get("bar")]
        known = any(entries[i].get("outcome") for i in by_scenes)
        at = met[0] if met else by_scenes[-1] if by_scenes and not known else None
        upto = entries if at is None else entries[:at + 1]
        first, last = _when(upto[0].get("at")), _when(upto[-1].get("at"))
        outcome = entries[at].get("outcome") if at is not None else None
        rows.append({"game": Path(game).stem, "met": None if at is None or not outcome else True,
                     "compared": at is not None, "calls": len(upto), "runs": len({e.get("session") for e in upto}),
                     "minutes": round((last - first).total_seconds() / 60, 1) if first and last else None,
                     "tool_seconds": round(sum(float(e.get("seconds") or 0) for e in upto), 1),
                     "screens": (outcome or {}).get("screens"), "scenes": (outcome or {}).get("scenes"),
                     "calls_after": len(entries) - len(upto), "begun": entries[0].get("at")})
    return rows


def table(rows: list[dict]) -> str:
    """The trend as a Markdown table, for a pull request's description: a game a row, oldest first."""
    def cell(value):
        return "" if value is None else str(value)
    lines = ["| Game | To the bar: calls | runs | minutes | tool seconds | Screens, scenes | Calls after |",
             "|---|---|---|---|---|---|---|"]
    for row in rows:
        bar = "" if row["compared"] else " (no comparison by scenes yet)"
        screens = f"{row['screens']}, {row['scenes']}" if row["screens"] is not None else "" if not row["compared"] else "not in the record"
        lines.append(f"| {row['game']}{bar} | {row['calls']} | {row['runs']} | {cell(row['minutes'])} | "
                     f"{row['tool_seconds']} | {screens} | {row['calls_after']} |")
    return "\n".join(lines)
