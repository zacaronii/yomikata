"""The nightly regression check (`yomikata regress`, docs/regress.md): every merged entry's bar screens read again
with the catalog's current script, against what they read before.

A bar screen is a harness snap whose .json has rows read off its picture (`ocr`, from snap_lines). They are kept
apart from the harness's snaps, in scratch's regress/keep/<id>/, whole, so that a snap written later under the
same name (or a scratch folder cleared) can't take a bar away: the harness never writes there. Each night the
kept snaps are read again offline, as `compare` with `reread` reads them (the script ticked over the still snap,
its regions beside it, the cartridge from the game's file where the platform has one), and each one's agreement
with the rows read off its picture, and its text's hash, are held against a baseline (regress/baseline.json):
the first reading of each. Only names, numbers and hashes are written: never the game's text.

A screen that agreed (AGREE or more) and no longer does, or a script that no longer loads over a snap, is a
fall: the report says which and the exit status is 1. A reading that changed but agrees as well as before or
better is noted and becomes the baseline; a fall stays out of it until it is accepted (`--accept <id>`)."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import snapfile
from .check import agreement
from .romfont import text_hash

AGREE = 0.8
FRAMES = 40                  # ticks over each snap, as compare's
KEEP, BASELINE, IDS = "keep", "baseline.json", "ids.json"


@dataclass
class Row:
    """One kept bar screen, read again."""
    id: str
    snap: str
    agreement: float | None      # None: the script didn't load over it
    hash: str
    length: int
    error: str | None = None


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    fell: list[tuple[Row, dict]] = field(default_factory=list)
    changed: list[tuple[Row, dict]] = field(default_factory=list)
    new: list[Row] = field(default_factory=list)
    kept: int = 0
    skipped: dict[str, str] = field(default_factory=dict)     # game path -> why it isn't checked

    @property
    def failed(self) -> bool:
        return bool(self.fell)


def bar_snaps(snaps: Path) -> dict[str, list[str]]:
    """The harness's snaps with rows read off their picture, by the game's path: what a bar was measured on."""
    out: dict[str, list[str]] = {}
    for meta_path in sorted(snaps.glob("*.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stem = meta_path.name[:-len(".json")]
        if meta.get("ocr") and meta.get("game") and (snaps / f"{stem}.ram.gz").is_file():
            out.setdefault(meta["game"], []).append(stem)
    return out


def _script(catalog: Path, release) -> Path | None:
    path = catalog / "games" / release.platform / f"{release.id}.lua"
    return path if path.is_file() else None


def keep(scratch: Path, games: Path, catalog: Path, identify=None) -> tuple[int, dict[str, str]]:
    """Copies the bar snaps of every game the catalog has a script for into regress/keep/<id>/, whole (a snap kept
    as a difference from another is written out in full), with its .json and regions. One already kept is left
    as it is. Gives how many were copied, and the games not checked, with why."""
    from .identify import identify as identify_game
    identify = identify or identify_game
    folder, ids_path = scratch / "regress", scratch / "regress" / IDS
    ids = json.loads(ids_path.read_text(encoding="utf-8")) if ids_path.is_file() else {}
    copied, skipped = 0, {}
    snaps = scratch / "snaps"
    for game, stems in bar_snaps(snaps).items():
        if game not in ids:
            try:
                release = identify(games / game)
                ids[game] = [release.platform, release.id]
            except Exception as e:                # a game not in the library any more, or not identified
                skipped[game] = f"not identified: {e}"
                continue
        platform, gid = ids[game]
        if not (catalog / "games" / platform / f"{gid}.lua").is_file():
            skipped[game] = "no merged script"
            continue
        into = folder / KEEP / gid
        into.mkdir(parents=True, exist_ok=True)
        lost = []
        for stem in stems:
            if (into / f"{stem}.json").is_file():
                continue
            try:
                parts = [(part, snapfile.read(part)) for part in sorted(snaps.glob(f"{stem}.*.gz"))
                         if "." not in part.name[len(stem) + 1:-len(".gz")]]      # not a savestate kept beside it
            except snapfile.SnapError:
                lost.append(stem)                 # kept as a difference from a snap that is gone: can't be read
                continue
            for part, data in parts:
                snapfile.write(into / part.name, data, None)
            for side in (f"{stem}.json", f"{stem}.png"):
                if (snaps / side).is_file():
                    shutil.copyfile(snaps / side, into / side)
            copied += 1
        if lost:
            skipped[f"{game} ({len(lost)} snaps)"] = (f"{len(lost)} bar snaps can't be read (each kept as a "
                                                       f"difference from a snap that is gone): {', '.join(lost[:5])}"
                                                       + (" …" if len(lost) > 5 else ""))
    folder.mkdir(parents=True, exist_ok=True)
    ids_path.write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
    return copied, skipped


def _rom(games: Path, game: str, platform: str) -> bytes | None:
    """The game's file as a cartridge, for the platforms whose script may read one; None for the others."""
    from .harness.machine import CARTRIDGE, NATIVE_CARTRIDGE
    if platform not in (*CARTRIDGE, *NATIVE_CARTRIDGE, "sfc"):
        return None
    path = games / game
    if path.suffix.lower() == ".zip":
        import zipfile
        with zipfile.ZipFile(path) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            return z.read(max(members, key=lambda m: m.file_size)) if members else None
    return path.read_bytes() if path.is_file() else None


def reread(scratch: Path, games: Path, catalog: Path, frames: int = FRAMES) -> list[Row]:
    """Every kept bar screen read again with the catalog's current script."""
    from .run import run
    folder = scratch / "regress"
    ids_path = folder / IDS
    ids = json.loads(ids_path.read_text(encoding="utf-8")) if ids_path.is_file() else {}
    by_id = {gid: (game, platform) for game, (platform, gid) in ids.items()}
    rows = []
    for into in sorted(p for p in (folder / KEEP).glob("*") if p.is_dir()):
        gid = into.name
        if gid not in by_id:
            continue
        game, platform = by_id[gid]
        script = catalog / "games" / platform / f"{gid}.lua"
        stems = sorted(p.name[:-len(".json")] for p in into.glob("*.json"))
        if not script.is_file() or not stems:
            continue
        try:
            rom = _rom(games, game, platform)
        except OSError:
            rom = None
        readings = run(script, [into / f"{s}.ram.gz" for s in stems], frames=frames, rom=rom)
        for stem, reading in zip(stems, readings):
            meta = json.loads((into / f"{stem}.json").read_text(encoding="utf-8"))
            said = "\n".join(part for e in reading.story() for part in (e.get("speaker") or "", e["text"]) if part)
            rows.append(Row(gid, stem, None if reading.error else round(agreement(said, meta.get("ocr") or []), 2),
                            text_hash(said.encode("utf-8")), len(said), reading.error))
    return rows


def judge(rows: list[Row], baseline: dict, accept: tuple[str, ...] = ()) -> tuple[Report, dict]:
    """The rows against the baseline: what fell, what changed, what is new; and the baseline to keep."""
    report, out = Report(rows=rows), dict(baseline)
    for row in rows:
        key = f"{row.id}/{row.snap}"
        now = {"agreement": row.agreement, "hash": row.hash, "length": row.length}
        before = baseline.get(key)
        if before is None:
            report.new.append(row)
            out[key] = now
            continue
        was = before.get("agreement")
        fell = row.agreement is None and was is not None or \
            (was is not None and was >= AGREE and (row.agreement is None or row.agreement < AGREE))
        if fell and row.id not in accept:
            report.fell.append((row, before))
            continue
        if row.hash != before.get("hash") or row.agreement != was:
            report.changed.append((row, before))
        out[key] = now
    return report, out


def markdown(report: Report, day: str) -> str:
    """The night's report: names, counts and agreements; never the text."""
    games: dict[str, list[Row]] = {}
    for row in report.rows:
        games.setdefault(row.id, []).append(row)
    fell = {(r.id, r.snap) for r, _ in report.fell}
    changed = {(r.id, r.snap) for r, _ in report.changed}
    lines = [f"# Regression check, {day}", "",
             f"{len(report.rows)} bar screens of {len(games)} games read again; {len(report.fell)} fell, "
             f"{len(report.changed)} changed, {len(report.new)} new; {report.kept} snaps newly kept.", "",
             "| Game | Screens | Agreeing | Changed | Fell |", "|---|---|---|---|---|"]
    for gid, rows in sorted(games.items()):
        agreeing = sum(1 for r in rows if r.agreement is not None and r.agreement >= AGREE)
        lines.append(f"| {gid} | {len(rows)} | {agreeing} | {sum((r.id, r.snap) in changed for r in rows)} "
                     f"| {sum((r.id, r.snap) in fell for r in rows)} |")
    for title, items in (("Fell", report.fell), ("Changed", report.changed)):
        if items:
            lines += ["", f"## {title}", ""]
            for row, before in items:
                now = "didn't load" if row.agreement is None else f"{row.agreement:.2f}"
                lines.append(f"- {row.id} {row.snap}: {before.get('agreement')} -> {now}"
                             f"{', text changed' if row.hash != before.get('hash') else ''}")
    blind = [(gid, rows) for gid, rows in sorted(games.items()) if all(r.length == 0 for r in rows)]
    if blind:
        lines += ["", "## Read nothing over a still snap", "",
                  "Their scripts say nothing over these snaps (a fall can't show; a change still does):", ""]
        for gid, rows in blind:
            why = next((r.error for r in rows if r.error), None)
            lines.append(f"- {gid}: {why or 'loads, and says nothing'}")
    if report.skipped:
        lines += ["", "## Not checked", ""] + [f"- {Path(g).name}: {why}" for g, why in sorted(report.skipped.items())]
    return "\n".join(lines) + "\n"


def regress(scratch: Path, games: Path, catalog: Path, accept: tuple[str, ...] = (), frames: int = FRAMES,
            day: str | None = None, identify=None) -> Report:
    """Keeps new bar snaps, reads every kept one again, writes the report and the baseline."""
    folder = scratch / "regress"
    kept, skipped = keep(scratch, games, catalog, identify)
    rows = reread(scratch, games, catalog, frames)
    base_path = folder / BASELINE
    baseline = json.loads(base_path.read_text(encoding="utf-8")) if base_path.is_file() else {}
    report, after = judge(rows, baseline, accept)
    report.kept, report.skipped = kept, skipped
    base_path.write_text(json.dumps(after, indent=1, sort_keys=True), encoding="utf-8")
    day = day or dt.date.today().isoformat()
    (folder / f"{day}.md").write_text(markdown(report, day), encoding="utf-8")
    return report


def summary(report: Report) -> str:
    """One line for a notifier: counts and the fallen snaps' names, no text."""
    names = ", ".join(f"{r.id}/{r.snap}" for r, _ in report.fell[:20])
    more = f" and {len(report.fell) - 20} more" if len(report.fell) > 20 else ""
    return f"{len(report.fell)} bar screens fell of {len(report.rows)}: {names}{more}"
