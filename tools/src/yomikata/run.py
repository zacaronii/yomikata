"""Running a script over snapshots (docs/script-api.md, docs/inbox.md): what it emits from each.

A snapshot is one moment, so by default each gets a reader of its own: the script is loaded, ticked a few
frames over that memory, and what it emitted is the snapshot's reading. With `sequence` the snapshots are
consecutive moments of one run (a `replay`'s snaps): one reader, one tick each.

What comes out is the game's text: for a terminal or a scratch folder, never for the catalog."""

from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from pathlib import Path

from . import reader as rd
from .bundle import bundle
from .check import sidecars
from .layout import RAM_BASE

STORY_CHANNELS = ("dialogue", "choice")
CHANNELS = (*STORY_CHANNELS, "interface")

# What a host calls a platform, where it isn't the catalog's name.
PLATFORM_ALIASES = {"ngc": "gc", "psx": "ps1", "snes": "sfc"}
RAM_BASES = {**RAM_BASE, "wii": 0x80000000}
# Other addresses the same main memory answers at.
MIRRORS = {"ps1": (0x00000000, 0xA0000000)}


def _load(path: Path) -> bytes:
    raw = path.read_bytes()
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


@dataclass
class Reading:
    name: str
    events: list[tuple[str, dict]] = field(default_factory=list)
    error: str | None = None        # the script didn't load over this snapshot

    def texts(self) -> list[dict]:
        """Every text emitted, whatever its channel."""
        return [e for kind, e in self.events if kind == "text"]

    def story(self) -> list[dict]:
        """What is read: dialogue and choices. Interface text (menus, names, status screens) is only ever looked
        up on the picture, and a channel a host doesn't know is dropped (docs/script-api.md)."""
        return [e for e in self.texts() if e["channel"] in STORY_CHANNELS]


def platform_of(script: Path, given: str | None, meta: dict) -> str | None:
    name = given or meta.get("platform") or (script.parent.name if script.parent.name in RAM_BASES else None)
    return PLATFORM_ALIASES.get(name, name)


def data_files(script: Path) -> dict[str, bytes]:
    """The script's data files: `<id>.<name>` beside it."""
    stem = script.stem
    return {p.name[len(stem) + 1:]: p.read_bytes() for p in script.parent.glob(stem + ".*")
            if p.is_file() and p.suffix not in (".lua", ".json")}


def _regions(snapshot: Path) -> dict[str, bytes]:
    video = snapshot.with_name(snapshot.name.split(".")[0] + ".vram.gz")
    return {"vram": _load(video)} if video.is_file() else {}


def _memory(script: Path, snapshot: Path, platform: str | None, base: int | None):
    """A snapshot's memory as a read, with its platform and its .json."""
    meta, _ = sidecars(snapshot)
    platform = platform_of(script, platform, meta)
    start = base if base is not None else (int(meta["base_address"], 16) if meta.get("base_address")
                                           else RAM_BASES.get(platform))
    if start is None:
        raise ValueError(f"{snapshot.name}: give the address its first byte has (--base) or its platform")
    return rd.memory(start, _load(snapshot), MIRRORS.get(platform, ())), platform, meta


def _open(script: Path, snapshot: Path, platform: str | None, base: int | None, limits: dict) -> rd.Reader:
    read, platform, meta = _memory(script, snapshot, platform, base)
    return rd.Reader(read, platform=platform, game_id=meta.get("id") or script.stem,
                     build_id=meta.get("exe_sha1") or None, regions=_regions(snapshot), data=data_files(script),
                     **limits)


def run(script: Path, snapshots: list[Path], *, platform: str | None = None, base: int | None = None,
        frames: int = 2, sequence: bool = False, **limits) -> list[Reading]:
    source = bundle(script).encode("utf-8")
    out = [Reading(path.name.split(".")[0]) for path in snapshots]
    if sequence:
        with _open(script, snapshots[0], platform, base, limits) as reader:
            try:
                reader.load(source, script.name)
            except rd.ScriptError as e:
                out[0].error, out[0].events = str(e), reader.take()
                return out[:1]
            for n, (path, reading) in enumerate(zip(snapshots, out)):
                if n:
                    reader.read = _memory(script, path, platform, base)[0]
                    reader.regions = _regions(path)
                reader.frame(n + 1)
                reading.events = reader.take()
        return out
    for path, reading in zip(snapshots, out):
        with _open(script, path, platform, base, limits) as reader:
            try:
                reader.load(source, script.name)
                for n in range(frames):
                    reader.frame(n + 1)
            except rd.ScriptError as e:
                reading.error = str(e)
            reading.events = reader.take()
    return out
