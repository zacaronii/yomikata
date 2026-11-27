"""Running a script over snapshots (docs/script-api.md, docs/inbox.md): what it emits from each.

A snapshot is one moment, so by default each gets a reader of its own: the script is loaded, ticked a few
frames over that memory, and what it emitted is the snapshot's reading. With `sequence` the snapshots are
consecutive moments of one run (a `replay`'s snaps): one reader, one tick each.

What comes out is the game's text: for a terminal or a scratch folder, never for the catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import reader as rd
from . import snapfile
from .bundle import bundle
from .check import sidecars
from .layout import RAM_BASE

STORY_CHANNELS = ("dialogue", "choice")
CHANNELS = (*STORY_CHANNELS, "interface")

# What a host calls a platform, where it isn't the catalog's name.
PLATFORM_ALIASES = {"ngc": "gc", "psx": "ps1", "snes": "sfc", "genesis": "md", "megadrive": "md"}
RAM_BASES = {**RAM_BASE, "wii": 0x80000000}
# Other addresses the same main memory answers at.
# The same memory at other addresses: the PS1's past its cache and from 0; the Dreamcast's main 16 MB from
# 0x0C000000 (its physical address) and past the cache.
MIRRORS = {"ps1": (0x00000000, 0xA0000000), "dc": (0x0C000000, 0xAC000000),
           "fc": (0x0800, 0x1000, 0x1800), "fds": (0x0800, 0x1000, 0x1800)}   # 2 KB, four times to 0x1FFF


def _load(path: Path) -> bytes:
    return snapfile.read(path)


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
    """The regions kept beside a snapshot (`<name>.<region>.gz`: video memory, and others a platform has)."""
    stem = snapshot.name.split(".")[0]
    out = {}
    for path in sorted(snapshot.parent.glob(f"{stem}.*.gz")):
        region = path.name[len(stem) + 1:-len(".gz")]
        if region not in ("ram", "state") and "." not in region:        # not a savestate kept beside it
            out[region] = _load(path)
    return out


def with_regions(read, platform: str | None, regions: dict[str, bytes]):
    """A read over main memory that answers at a region's own addresses from the region, for the regions a
    platform keeps at console addresses (a Game Boy's `io` at 0xFF00; harness/machine.py's region_starts)."""
    from .harness.machine import region_starts
    starts = {name: start for name, start in region_starts(platform or "").items() if name in regions}
    if not starts:
        return read

    def both(address: int, size: int) -> bytes:
        # A region first, at its own addresses: a core's main memory may run past them with something else there
        # (mGBA's Game Boy work RAM is 32 KB from 0xC000, and its last 256 bytes aren't the I/O registers).
        for name, start in starts.items():
            at = address - start
            if 0 <= at < len(regions[name]):
                return regions[name][at:at + size]
        return read(address, size)
    return both


def _memory(script: Path, snapshot: Path, platform: str | None, base: int | None, rom: bytes | None = None):
    """A snapshot's memory as a read, with its platform and its .json; with `rom`, the game's file, its
    cartridge answers where the snapshot has nothing (harness/machine.py's rom_reader)."""
    meta, _ = sidecars(snapshot)
    platform = platform_of(script, platform, meta)
    start = base if base is not None else (int(meta["base_address"], 16) if meta.get("base_address")
                                           else RAM_BASES.get(platform))
    if start is None:
        raise ValueError(f"{snapshot.name}: give the address its first byte has (--base) or its platform")
    read = with_regions(rd.memory(start, _load(snapshot), MIRRORS.get(platform, ())), platform, _regions(snapshot))
    if rom is not None:
        from .harness.machine import rom_reader, with_rom
        read = with_rom(read, rom_reader(platform or "", rom))
    return read, platform, meta


def _open(script: Path, snapshot: Path, platform: str | None, base: int | None, limits: dict,
          rom: bytes | None = None) -> rd.Reader:
    read, platform, meta = _memory(script, snapshot, platform, base, rom)
    return rd.Reader(read, platform=platform, game_id=meta.get("id") or script.stem,
                     build_id=meta.get("exe_sha1") or None, regions=_regions(snapshot), data=data_files(script),
                     **limits)


def run(script: Path, snapshots: list[Path], *, platform: str | None = None, base: int | None = None,
        frames: int = 2, sequence: bool = False, dump: bool = False, rom: bytes | None = None, hold: int = 1,
        **limits) -> list[Reading]:
    """What the script emits over each snapshot (with `rom`, the game's file, its cartridge too). With `dump`, not what is on screen but everything the game
    has in memory there, as the script's own `dump` function says it (docs/script-api.md): the way to read
    through a place's whole text with the script's decoder and tables."""
    source = bundle(script).encode("utf-8")
    out = [Reading(path.name.split(".")[0]) for path in snapshots]
    if dump:
        for path, reading in zip(snapshots, out):
            with _open(script, path, platform, base, limits, rom) as reader:
                try:
                    reader.load(source, script.name)
                    reader.take()
                    if reader.eval("return type(dump)") != "function":
                        raise rd.ScriptError("the script has no dump function")
                    reader.eval("dump()")
                except rd.ScriptError as e:
                    reading.error = str(e)
                reading.events = reader.take()
        return out
    if sequence:
        with _open(script, snapshots[0], platform, base, limits, rom) as reader:
            try:
                reader.load(source, script.name)
            except rd.ScriptError as e:
                out[0].error, out[0].events = str(e), reader.take()
                return out[:1]
            tick = 0
            for n, (path, reading) in enumerate(zip(snapshots, out)):
                if n:
                    reader.read = _memory(script, path, platform, base, rom)[0]
                    reader.regions = _regions(path)
                # One tick each; the last held for `hold` ticks (a script that says a page once it has stood).
                for _ in range(max(1, hold) if n == len(snapshots) - 1 else 1):
                    tick += 1
                    reader.frame(tick)
                reading.events = reader.take()
        return out
    for path, reading in zip(snapshots, out):
        with _open(script, path, platform, base, limits, rom) as reader:
            try:
                reader.load(source, script.name)
                for n in range(frames):
                    reader.frame(n + 1)
            except rd.ScriptError as e:
                reading.error = str(e)
            reading.events = reader.take()
    return out
