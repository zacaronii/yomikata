"""What an agent can do with a game that is running: play it, look at it, search its memory, compare moments,
keep states, and try a script on it. Each tool is a method here; mcp.py offers them.

Everything it reads or writes on disk lies under one of four folders, and a path that leaves them is refused:
games and cores and the inbox's snapshots (read), and a scratch folder (written). What comes back holds game
text and pictures: for the agent's eyes and the scratch folder, never for the catalog."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import struct
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .. import reader as rd
from .. import snapfile
from ..run import MIRRORS, PLATFORM_ALIASES
from . import record as calls_record
from .machine import BIG_ENDIAN
from . import search

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
BUTTONS = ("a", "b", "x", "y", "l", "r", "l2", "r2", "l3", "r3", "start", "select", "up", "down", "left", "right")
MAX_FRAMES, MAX_READ, MAX_HITS, MAX_EVENTS = 36000, 4096, 200, 300
MAX_INFO = 50          # a script's info-level logs kept in a call's events, so that they can't crowd out its text
MAX_STEPS, MAX_SHOTS, MAX_ROWS = 200, 48, 60
# Consoles whose games keep a place as whole numbers only (no numbers with a point): explore's grid needs whole.
WHOLE_ONLY = ("fc", "fds", "gb", "gbc", "sfc", "pce", "pce-cd")
# Options a core is loaded with unless the call gives its own. mupen64plus_next draws with GLideN64 unless told
# otherwise, and here (Mesa, no window) GLideN64 gives a black picture on many frames once a game has used the
# framebuffer for an effect, until the core is loaded again (Doubutsu no Mori after its name keyboard: 19 of 50
# pages). angrylion, its renderer on the processor, draws every frame. It gives the picture at 640x240. Its
# thread sync left Low, the picture falls some frames behind memory (a page snapped as it stands shows half
# typed); High keeps it with the frame.
CORE_OPTIONS = {"mupen64plus_next_libretro.so": {"mupen64plus-rdp-plugin": "angrylion",
                                                 "mupen64plus-angrylion-sync": "High"}}
MAX_POKE = 256                  # bytes in one write
WARM_FRAMES = 30                # frames a core runs from power-on before `load` takes it to a state
CHUNK = 30                      # frames run between two looks at the clock, when nothing is watched
CALL_SECONDS = 120.0            # how long one call may run the game before it stops and says so
STORY = ("dialogue", "choice")
ADVANCING_MOST_SNAPS = 400      # advancing reads each snap in turn; this many is minutes of work
ADVANCING_MOST_BYTES = 1 << 30  # what advancing may hold at once (the harness's container has 8 GB in all)
ADVANCING_SLICE = 1 << 20       # numbers whose steps advancing works out at once


def advancing_bytes(length: int, unit: int) -> int:
    """About what advancing holds at once over snaps of `length` bytes: a snap as read and the one before it
    (gzip's output and its bytes), the first, last and final values, two 16-bit counts and the flags of each
    number, and one slice's steps in 64 bits with the masks made from them."""
    numbers = length // unit
    return 2 * length + 3 * numbers * unit + 2 * numbers * 2 + numbers + ADVANCING_SLICE * 8 * 4


class HarnessError(ValueError):
    """Something asked for can't be done; the message is for the agent."""


@dataclass
class Roots:
    games: Path
    cores: Path
    inbox: Path
    scratch: Path
    system: Path | None = None      # where a core looks for a BIOS, when it isn't scratch's own system/
    built: Path | None = None       # cores built into the image (patched ones), for a name the cores folder hasn't got

    def core(self, name: str) -> Path:
        """A core's file: in the cores folder, or else among the image's own built cores (tools/cores)."""
        path = self.inside("cores", name)
        if not path.is_file() and self.built is not None:
            built = self.inside("built", name)
            if built.is_file():
                return built
        return path

    def inside(self, root: str, relative: str) -> Path:
        """A path under one of the roots. One that climbs out of it, by .. or by a link, is refused."""
        base = getattr(self, root).resolve()
        path = (base / relative).resolve()
        if path != base and base not in path.parents:
            raise HarnessError(f"{relative} is outside {root}")
        return path


@dataclass
class Snap:
    """A snap of this session: where its memory starts and when it was taken. Its bytes are on disk."""
    base: int
    frame: int


def tool(description: str, **properties):
    """Marks a method as a tool, with what it takes: name -> (JSON type, description, required)."""
    def mark(method):
        method.tool = (description, properties)
        return method
    return mark


class Session:
    def __init__(self, roots: Roots, make_machine=None):
        self.roots = roots
        self.make_machine = make_machine
        self.machine = None
        self.game = None
        self.session_id = uuid.uuid4().hex[:12]    # names this run of the harness in the record of calls
        self.loaded = None                      # what load was last given: repeat loads it again from power-on
        self.once = False                       # a core was loaded that can't be loaded again in this process
        # The way here: where it starts ("power-on", or a state it can't see behind) and every move since, as
        # [frames, buttons, stick] or {"poke": address, "hex": bytes}. A waypoint keeps it.
        self.route: dict = {"base": "power-on", "moves": []}
        self.cursor_at: tuple[int, int, int] | None = None
        self.each_frame = None                  # called after every frame while set (glyph_pairs)
        self.core_hash = None
        self.snaps: dict[str, Snap] = {}
        # The session's first snap, whole on disk: the later ones are kept as what differs from it.
        self.first_snap: tuple[str, bytes, dict[str, bytes]] | None = None
        self.reader: rd.Reader | None = None
        self.script_name = None
        self.layouts: dict[str, dict] = {}
        self.said: list[dict] = []              # what the loaded script has said since the last snap
        self.call_seconds = CALL_SECONDS
        self.allow_poke = False                 # memory writes: only when the harness was started with them on
        self.pokes = 0                          # writes made since the game was loaded
        self.deadline: float | None = None      # set by whoever makes a call (mcp.py); None: no limit
        for folder in ("states", "snaps", "system", "saves", "waypoints"):
            (roots.scratch / folder).mkdir(parents=True, exist_ok=True)

    # ---- helpers

    def _need(self):
        if self.machine is None:
            raise HarnessError("no game is loaded: call load first")
        return self.machine

    def _name(self, name: str) -> str:
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise HarnessError("a name is letters, digits, dots, dashes and underscores (at most 64)")
        return name

    def _buttons(self, buttons) -> set[str]:
        held = set(buttons or ())
        if not held <= set(BUTTONS):
            raise HarnessError(f"buttons are: {', '.join(BUTTONS)}")
        return held

    def _stick(self, stick) -> tuple[float, float]:
        if not stick:
            return (0.0, 0.0)
        x, y = (max(-1.0, min(1.0, float(v))) for v in stick)
        return (x, y)

    def _read(self, address: int, size: int) -> bytes:
        return self._need().read(address, size)

    def _close_reader(self) -> None:
        if self.reader is not None:
            self.reader.close()
        self.reader, self.script_name, self.layouts = None, None, {}

    def _state_file(self, name: str) -> Path:
        return self.roots.inside("scratch", f"states/{self._name(name)}.state.gz")

    def _collect(self, events: list, out: list) -> None:
        for kind, event in events:
            if kind == "layout":
                if event.get("closed"):
                    self.layouts.pop(event["window"], None)
                else:
                    self.layouts[event["window"]] = event
            if kind == "text":
                self.said.append({k: event.get(k) for k in ("channel", "speaker", "text")})
                del self.said[:-MAX_EVENTS]
            if kind == "log" and str(event.get("message", "")).startswith(("glyph ", "glyph-line ")):
                self._keep_glyph(event["message"])
                continue
            if kind == "log" and event.get("level") == "info":
                infos = sum(1 for e in out if e["kind"] == "log" and e.get("level") == "info")
                if infos >= MAX_INFO:
                    continue
            if len(out) < MAX_EVENTS:
                out.append({"kind": kind, "frame": self.machine.frames, **event})

    def _keep_glyph(self, line: str) -> None:
        """A picture the script had no reading for (lib/glyphs.lua logs it), kept in scratch for `yomikata
        unread`: glyphs/<the script's name>.log, a line a picture."""
        try:
            folder = self.roots.scratch / "glyphs"
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / (Path(self.script_name or "script").stem + ".log")).open("a", encoding="utf-8") as log:
                log.write(line + "\n")
        except OSError:
            pass

    def begin_call(self, tool: str | None = None, arguments: dict | None = None, game: str | None = None) -> None:
        """A call starts now: what runs the game stops by itself `call_seconds` from here. Given its tool, the call
        goes into the record now, before it does anything (record.begin), so that one whose process dies in it
        (out of memory, a core given up on) is still counted; record_call then writes its answer's line."""
        self.deadline = time.monotonic() + self.call_seconds
        self._call_id = None
        if tool is not None:
            self._calls_begun = getattr(self, "_calls_begun", 0) + 1
            self._call_id = f"{self.session_id}-{self._calls_begun}"
            calls_record.begin(self.roots.scratch, self._entry(tool, arguments, game or self.game), self._call_id)

    def _entry(self, tool: str, arguments: dict | None, game: str | None) -> dict:
        """A record line's common part: when, which session and game, the tool and its arguments in short."""
        def short(value):
            if isinstance(value, str):
                return value if len(value) <= 48 else f"{value[:32]}… ({len(value)} characters)"
            if isinstance(value, list):
                return f"{len(value)} items"
            if isinstance(value, dict):
                return f"{len(value)} keys"
            return value
        return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session": self.session_id,
                "game": game, "tool": tool, "arguments": {str(k): short(v) for k, v in (arguments or {}).items()}}

    def record_call(self, tool: str, arguments: dict, ok: bool, seconds: float, outcome: dict | None = None,
                    game: str | None = None) -> None:
        """A line for this call in scratch's record (record/calls.jsonl): when, which session and game, the tool
        and its arguments in short. `yomikata calls` sums them by game. A record that can't be written is no
        reason for a call to fail. After begin_call with the tool, it is the call's second line, under the same
        game as the first: the loaded one, else the one the call names (`budget` or `load` before a game is up)."""
        entry = {**self._entry(tool, arguments, self.game or game), "ok": bool(ok), "seconds": round(seconds, 2),
                 **({"outcome": outcome} if outcome else {})}
        call, self._call_id = getattr(self, "_call_id", None), None
        if call is None:
            calls_record.append(self.roots.scratch, entry)
        else:
            calls_record.finish(self.roots.scratch, entry, call)

    def calls_so_far(self, game: str | None) -> int:
        """The calls the record has for a game: the same lines `yomikata calls` counts, read from the record
        itself each time it has grown by other than this session's own last line."""
        try:
            size = calls_record.path(self.roots.scratch).stat().st_size
        except OSError:
            return 0
        counted = getattr(self, "_counted", None)
        if counted is None or counted[0] != size:
            self._counted = counted = calls_record.counts(self.roots.scratch)
        return counted[1].get(game, 0)

    def budget_of(self, game: str | None) -> int | None:
        """The most calls a game may have in the record (scratch record/budgets.json), or None: no budget."""
        return calls_record.budget_of(self.roots.scratch, game)

    def over_budget(self, game: str | None) -> str | None:
        """Why no further call is taken for this game, or None."""
        limit = self.budget_of(game)
        used = self.calls_so_far(game) if limit is not None else 0
        if limit is None or used < limit:
            return None
        return (f"the budget for {game} is spent: {used} calls in the record, {limit} allowed. Nothing was run. "
                "Stop here and say so to whoever set the budget; `budget` with a higher number of calls raises "
                "it, when they have said how many")

    def _late(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def _advance(self, frames: int, held: set[str], where: tuple[float, float], events: list, watch=None):
        """Runs up to `frames` frames, ticking the script after each. Stops early when `watch()` gives a reason
        after a frame, or when the call's time is up ("time"). Gives the frames run and the reason, or None."""
        machine, ran = self.machine, 0
        while ran < frames:
            if self._late():
                return ran, "time"
            step = 1 if (self.reader is not None or watch is not None or self.each_frame is not None) \
                else min(CHUNK, frames - ran)
            machine.run(step, held, where)
            ran += step
            self._moved(step, held, where)
            if self.reader is not None:
                self.reader.frame(machine.frames)
                self._collect(self.reader.take(), events)
            if self.each_frame is not None:
                self.each_frame()
            why = watch() if watch is not None else None
            if why:
                return ran, why
        return ran, None

    def _moved(self, frames: int, held: set[str], where: tuple[float, float]) -> None:
        move = [frames, sorted(held), list(where)]
        moves = self.route["moves"]
        if moves and isinstance(moves[-1], list) and moves[-1][1:] == move[1:]:
            moves[-1][0] += frames
        else:
            moves.append(move)

    def _cell(self, address: int, size: int) -> int | None:
        data = self._read(address, size)
        if len(data) < size:
            return None
        return int.from_bytes(data, "big" if self.machine.platform in BIG_ENDIAN else "little")

    def _watch(self, events: list, address: str = "", size: int = 1, equals: str = "", event: str = "",
               still: int = 0):
        """What run_until and a step's `until` wait for, as a function to call after each frame."""
        machine = self._need()
        if size not in (1, 2, 4):
            raise HarnessError("size is 1, 2 or 4")
        if event and event not in ("text", "any", *STORY):
            raise HarnessError("event is text (any text), dialogue, choice, or any (anything the script emits)")
        if event and self.reader is None:
            raise HarnessError("no script is loaded to wait for: call script_try first")
        if not (address or event or still):
            raise HarnessError("say what to wait for: address, event or still")
        at = int(address, 16) if address else None
        want = int(str(equals), 0) if str(equals) != "" else None
        first = self._cell(at, size) if at is not None else None
        seen = len(events)
        state = {"picture": None, "same": 0}

        def watch():
            if at is not None:
                now = self._cell(at, size)
                if want is not None and now == want:
                    return "equals"
                if want is None and now != first:
                    return "changed"
            if event:
                for e in events[seen:]:
                    if event == "any" or (e["kind"] == "text" and (event == "text" or e.get("channel") == event)):
                        return "event"
            if still:
                import numpy as np
                picture = machine.picture()
                same = state["picture"] is not None and picture is not None and np.array_equal(picture, state["picture"])
                state["picture"], state["same"] = picture, state["same"] + 1 if same else 0
                if state["same"] >= int(still):
                    return "still"
            return None
        return watch

    # ---- the game

    @tool("What is loaded and where things are: the game, the frame count, saved states and snaps, the script.")
    def status(self) -> dict:
        states = sorted(p.name[:-len(".state.gz")] for p in (self.roots.scratch / "states").glob("*.state.gz"))
        return {"game": self.game, "platform": getattr(self.machine, "platform", None),
                "frames": getattr(self.machine, "frames", None), "states": states, "snaps": sorted(self.snaps),
                "script": self.script_name, **({"memory_writes": self.pokes} if self.allow_poke else {})}

    @tool("How many calls a game has had and how many it may have. The count is the record's own (what "
          "`yomikata calls` sums), over every run of the harness; every answer of every tool carries it as "
          "`calls_so_far`. Given `calls`, sets the most the game may have in all: at that many the harness "
          "takes no further call for the game (this one aside) until the number is raised. Set it when a "
          "game is begun, to what was agreed; raise it only when whoever set it has said so.",
          calls=("integer", "the most calls the game may have in the record, in all (0 or less: no budget)", False),
          game=("string", "path of the game under the library (default: the loaded game)", False))
    def budget(self, calls: int | None = None, game: str = "") -> dict:
        game = game or self.game
        if not game:
            raise HarnessError("no game is loaded: name one")
        if calls is not None:
            folder = self.roots.scratch / "record"
            folder.mkdir(parents=True, exist_ok=True)
            try:
                budgets = json.loads((folder / "budgets.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                budgets = {}
            budgets = budgets if isinstance(budgets, dict) else {}
            if int(calls) > 0:
                budgets[game] = int(calls)
            else:
                budgets.pop(game, None)
            (folder / "budgets.json").write_text(json.dumps(budgets, ensure_ascii=False, indent=1), encoding="utf-8")
        limit, used = self.budget_of(game), self.calls_so_far(game)
        return {"game": game, "calls_so_far": used, "budget": limit,
                **({"left": max(0, limit - used)} if limit is not None else {})}

    @tool("Which release a game file is: its platform, its id, and what names its build (the SHA-1 of a disc's "
          "boot executable, a cartridge's header checksum; for a PlayStation 2 disc `build`, its serial and "
          "the CRC an emulator gives it). The id names the script's file; the build goes in its "
          "`-- reader: build` line.",
          game=("string", "path of the game under the library (default: the loaded game)", False))
    def identify(self, game: str = "") -> dict:
        from ..disc import DiscError
        from ..identify import identify
        game = game or self.game
        if not game:
            raise HarnessError("no game is loaded: name one")
        path = self.roots.inside("games", game)
        if not path.is_file():
            raise HarnessError(f"no such game: {game}")
        work = self.roots.scratch / "work"
        work.mkdir(parents=True, exist_ok=True)
        try:
            return identify(path, work).as_dict()
        except (DiscError, OSError, ValueError) as e:
            raise HarnessError(f"it couldn't be identified: {e}") from None

    @tool("Whether a stretch of memory lies on the game's disc as it is: where in the disc's data its bytes are "
          "found. Text or a font that is on the disc as it is in memory can be read for every place from the "
          "disc, without playing to each. A CHD is unpacked to scratch for the search and removed after: a "
          "minute or so for a CD.",
          address=("string", "where the stretch starts, hex", True),
          size=("integer", "how many bytes of it to look for (default 32, at most 256)", False))
    def on_disc(self, address: str, size: int = 32) -> dict:
        from ..disc import DiscError, open_data_track
        machine = self._need()
        needle = machine.read(int(address, 16), max(8, min(int(size), 256)))
        if len(needle) < 8 or len(set(needle)) < 4:
            raise HarnessError("that stretch is too short or too plain to look for")
        path = self.roots.inside("games", self.game)
        work = self.roots.scratch / "work"
        work.mkdir(parents=True, exist_ok=True)
        found, sectors, step = [], 0, 1024
        try:
            with open_data_track(path, work) as image:
                image.f.seek(0, 2)
                sectors = image.f.tell() // (2352 if image.raw else 2048)
                keep = b""
                for first in range(0, sectors, step):
                    data = keep + b"".join(image.sector(lba) for lba in range(first, min(first + step, sectors)))
                    at = data.find(needle)
                    while at >= 0 and len(found) < 8:
                        found.append(first * 2048 - len(keep) + at)
                        at = data.find(needle, at + 1)
                    keep = data[-(len(needle) - 1):]
                    if len(found) >= 8 or self._late():
                        break
        except (DiscError, OSError, ValueError) as e:
            raise HarnessError(f"the disc couldn't be read: {e}") from None
        return {"bytes": len(needle), "found": [f"0x{at:X}" for at in found], "sectors": sectors,
                "note": "offsets into the disc's data, 2048 bytes a sector"}

    @tool("The loaded core's options as it declares them: what each is, the values it takes (the first is the "
          "core's own choice), and what was given at load. Options are given to `load`.")
    def core_options(self) -> dict:
        machine = self._need()
        return {"options": machine.core_options()}

    @tool("The games of one platform's folder, or the platform folders when none is named.",
          platform=("string", "a folder of the library, e.g. psx or ngc", False))
    def games(self, platform: str = "") -> dict:
        folder = self.roots.inside("games", platform)
        if not folder.is_dir():
            raise HarnessError(f"no such folder: {platform}")
        return {"entries": sorted(p.name + ("/" if p.is_dir() else "") for p in folder.iterdir())[:500]}

    @tool("Loads a game in a core, from power-on or from a savestate. The game loaded before is dropped.",
          game=("string", "path of the game under the library, e.g. psx/Some Game (Japan).chd", True),
          core=("string", "the core's file under the cores folder, e.g. pcsx_rearmed_libretro.so, or one built into "
                          "the image with a patch (genesis_plus_gx_vram_libretro.so: a Mega Drive's video memory)", True),
          platform=("string", "the catalog's name for the console: ps1, gc, sfc, …", True),
          state=("string", "a state saved here (its name), or a path under the inbox's snapshots ending in "
                           ".state.gz: the game is started, taken to the state and left ready (a picture, "
                           "memory as the state has it) in this one call. No run or state_load is wanted after", False),
          options=("object", "core options, name to value", False),
          settle=("integer", "with state: frames run after it, so that there is a picture and memory is the "
                             "core's own again (default 2; 0 to stand exactly on the state)", False))
    def load(self, game: str, core: str, platform: str, state: str = "", options: dict | None = None,
             settle: int = 2) -> dict:
        if self.make_machine is None:
            raise HarnessError("this harness has no cores")
        game_path, core_path = self.roots.inside("games", game), self.roots.core(core)
        if not game_path.is_file() or not core_path.is_file():
            raise HarnessError("no such game or core")
        if self.once:
            raise HarnessError("the core loaded before can't be followed by another load in the same harness: "
                               "start the harness again (states and snaps in scratch are kept)")
        self._close_reader()
        self.machine, self.snaps, self.pokes, self.first_snap = None, {}, 0, None
        platform = PLATFORM_ALIASES.get(platform, platform)         # ngc is the catalog's gc
        options = {**CORE_OPTIONS.get(Path(core).name, {}), **(options or {})}
        self.machine = self.make_machine(core_path, self._unpacked(game_path), platform,
                                         self.roots.system or self.roots.scratch / "system",
                                         {str(k): str(v) for k, v in (options or {}).items()})
        self.game, self.loaded = game, (game, core, platform, dict(options or {}))
        self.route = {"base": "power-on", "moves": []}
        self.core_hash = hashlib.sha256(core_path.read_bytes()).hexdigest()
        self.once = bool(getattr(self.machine, "once", False))
        if state:
            # A core takes a state only once it has run: a few frames from power-on first, then the state,
            # then a frame or two so that the picture and the memory read are the state's.
            self.machine.run(WARM_FRAMES, set(), (0.0, 0.0))
            self.state_load(state)
            settle = max(0, min(int(settle), 600))
            if settle:
                self.machine.run(settle, set(), (0.0, 0.0))
            return {**self.status(), "state": state}
        return self.status()

    def _unpacked(self, game_path: Path) -> Path:
        """A game kept as a .zip of one file, unpacked into scratch: not every core opens a zip, and one that
        doesn't shows a black picture and says nothing."""
        if game_path.suffix.lower() != ".zip":
            return game_path
        import zipfile
        with zipfile.ZipFile(game_path) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            if len(members) != 1:
                raise HarnessError("a .zip must hold one game file")
            out = self.roots.scratch / "work" / "unzipped" / Path(members[0].filename).name
            if not out.is_file() or out.stat().st_size != members[0].file_size:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(z.read(members[0]))
        return out

    @tool("Runs the game for a number of frames, holding buttons and the left stick the whole time.",
          frames=("integer", "how many frames (60 a second)", True),
          buttons=("array", "buttons held, RetroPad names: a b x y l r start select up down left right", False),
          stick=("array", "the left stick as [x, y], each from -1 to 1; up is y = -1", False))
    def run(self, frames: int, buttons: list | None = None, stick: list | None = None) -> dict:
        machine = self._need()
        if not 0 < int(frames) <= MAX_FRAMES:
            raise HarnessError(f"1 to {MAX_FRAMES} frames")
        held, where, events = self._buttons(buttons), self._stick(stick), []
        ran, why = self._advance(int(frames), held, where, events)      # a loaded script is ticked after each frame
        return {"frames": machine.frames, **({"ran": ran, "stopped": why} if why else {}),
                **({"events": events} if self.reader is not None else {})}

    @tool("Runs until something happens, or for `limit` frames: a place in memory changes (or holds `equals`), "
          "the loaded script emits something, or the picture has stood still. Says how far it ran and why it "
          "stopped: changed, equals, event, still, limit, or time (the call's own time was up: call again).",
          limit=("integer", "at most this many frames", True),
          address=("string", "a place in memory to watch, hex", False),
          size=("integer", "its bytes: 1, 2 or 4 (default 1), read in the console's byte order", False),
          equals=("string", "stop when it holds this value (0x… or decimal); without it, when it changes", False),
          event=("string", "stop when the script emits: text, dialogue, choice, or any", False),
          still=("integer", "stop when the picture hasn't changed for this many frames", False),
          buttons=("array", "buttons held meanwhile", False),
          stick=("array", "the left stick meanwhile, [x, y]", False))
    def run_until(self, limit: int, address: str = "", size: int = 1, equals: str = "", event: str = "",
                  still: int = 0, buttons: list | None = None, stick: list | None = None) -> dict:
        machine = self._need()
        if not 0 < int(limit) <= MAX_FRAMES:
            raise HarnessError(f"1 to {MAX_FRAMES} frames")
        held, where, events = self._buttons(buttons), self._stick(stick), []
        watch = self._watch(events, address, int(size), str(equals), event, int(still))
        ran, why = self._advance(int(limit), held, where, events, watch)
        out = {"frames": machine.frames, "ran": ran, "stopped": why or "limit"}
        if address:
            value = self._cell(int(address, 16), int(size))
            out["value"] = None if value is None else hex(value)
        return {**out, **({"events": events} if self.reader is not None else {})}

    @tool("Presses a pattern of buttons over and over while a place in memory says the game is in some mode (a "
          "fight, a menu), to get through it: stops when the mode ends, when a watched place changes (the text's "
          "pointer: a story page has begun), at `most` presses, or when the call's time is up. When the picture "
          "hasn't changed for `stuck` presses (a menu that takes no button; a blinking cursor or arrow doesn't count "
          "as a change), it presses `back` up to three times, then stops and says so. Says the presses, why it "
          "stopped, and the mode's place's value. A mode that doesn't hold when it starts (and doesn't begin "
          "within `begin` frames) is said to have never begun.",
          address=("string", "the mode's place in memory, hex: pressing goes on while it holds `equals` (or, with "
                             "`differs`, while it doesn't hold that)", True),
          size=("integer", "its bytes: 1, 2 or 4 (default 1)", False),
          equals=("string", "the value while in the mode (0x… or decimal)", False),
          differs=("string", "or the value the mode ends at: pressing goes on while it differs", False),
          buttons=("array", "the buttons of one press (default a); or a list of presses, each a list of buttons, "
                            "pressed in turn and again from the start ([[\"b\"], [\"up\"], [\"a\"]])", False),
          first=("array", "presses made once before the others, as `buttons`' list form (to set a menu's cursor)", False),
          hold=("integer", "frames a press is held (default 6)", False),
          wait=("integer", "frames after a press (default 30)", False),
          watch=("string", "a place whose change stops it at once, hex (a text pointer)", False),
          watch_size=("integer", "its bytes: 1, 2, 3 or 4 (default 1)", False),
          most=("integer", f"presses at most (default 300, at most {MAX_FRAMES // 8})", False),
          stuck=("integer", "presses with no change in the picture that count as stuck (default 8; 0: no check)", False),
          still_share=("number", "the share of the game screen's pixels that may change in a press that still "
                                 "counts as no change, against the picture a press back or two (default 0.01); "
                                 "a border around the game's screen (a Super Game Boy's) isn't counted", False),
          back=("array", "the buttons that back out of a stuck menu (default b)", False),
          cycle=("integer", "a press counts as no change when its picture is any of the last so many presses' "
                            "(default 6): a menu that goes round a few pictures and never on", False),
          begin=("integer", "frames to wait, pressing nothing, for the mode to begin (a fight's opening plays "
                            "before the game sets its flag); default 0", False))
    def advance_through(self, address: str, size: int = 1, equals: str = "", differs: str = "",
                        buttons: list | None = None, hold: int = 6, wait: int = 30, watch: str = "",
                        watch_size: int = 1, most: int = 300, stuck: int = 8, back: list | None = None,
                        first: list | None = None, still_share: float = 0.01, begin: int = 0,
                        cycle: int = 6) -> dict:
        import numpy as np
        machine = self._need()
        if size not in (1, 2, 4) or watch_size not in (1, 2, 3, 4):
            raise HarnessError("size is 1, 2 or 4; watch_size 1 to 4")
        if (str(equals) == "") == (str(differs) == ""):
            raise HarnessError("give equals (the mode's value) or differs (the value it ends at), one of them")
        at = int(address, 16)
        want = int(str(equals or differs), 0)
        def presses_of(value, default):
            """A list of presses (each a set of buttons) from one press's buttons or a list of presses."""
            value = default if value is None else value
            if not isinstance(value, list):
                raise HarnessError("buttons and first are lists")
            if value and all(isinstance(v, list) for v in value):
                return [self._buttons(v) for v in value]
            if any(isinstance(v, list) for v in value):
                raise HarnessError("buttons is one press's buttons, or a list of presses: not both")
            return [self._buttons(value)] if value else []
        pattern = presses_of(buttons, ["a"])
        if not pattern:
            raise HarnessError("buttons: at least one press")
        opening = presses_of(first, [])
        share = max(0.0, float(still_share))
        backing = self._buttons(back if back is not None else ["b"])
        hold, wait = max(1, int(hold)), max(0, int(wait))
        most = max(1, min(int(most), MAX_FRAMES // 8))
        stuck = max(0, int(stuck))
        watched = int(watch, 16) if watch else None
        events: list = []

        def mode() -> bool:
            value = self._cell(at, size)
            return value is not None and (value == want if str(equals) != "" else value != want)

        def seen():
            return self._read(watched, watch_size) if watched is not None else None

        def picture():
            # The game's own screen, without a border around it: a page typing is a small share of a big frame.
            frame = machine.screen() if hasattr(machine, "screen") else machine.picture()
            return None if frame is None else np.array(frame, copy=True)

        def same(a, b) -> bool:
            """Whether two pictures differ in no more than `share` of their pixels (a cursor blinking doesn't
            make a menu move)."""
            if a is None or b is None or a.shape != b.shape:
                return False
            changed = (a != b).any(axis=-1) if a.ndim == 3 else (a != b)
            return float(changed.mean()) <= share

        def press(keys) -> str | None:
            _, why = self._advance(hold, keys, (0.0, 0.0), events)
            if why:
                return why
            _, why = self._advance(wait, set(), (0.0, 0.0), events)
            return why

        stopped = None
        # The mode may begin a little later (a fight's opening plays before its flag is set): wait for it,
        # pressing nothing. One that doesn't hold by then never began, which isn't the same as one that ended.
        begin = max(0, min(int(begin), MAX_FRAMES))
        if begin and not mode():
            _, why = self._advance(begin, set(), (0.0, 0.0), events, watch=lambda: "began" if mode() else None)
            if why == "time":
                stopped = "time"
        if stopped is None and not mode():
            stopped = "never began"
        for keys in opening if stopped is None else ():   # once, before the rest: set a cursor, back out of a list
            if not mode():
                stopped = "mode ended"
                break
            why = press(keys)
            if why:
                stopped = why
                break
        first, presses, backs, still = seen(), 0, 0, 0
        # The pictures of the last presses: one that comes again within them is no change (a blink, or a menu
        # going round a few pictures under the same button).
        cycle = max(2, int(cycle))
        recent = [picture()]
        while stopped is None:
            if not mode():
                stopped = "mode ended"
                break
            if presses >= most:
                stopped = "most"
                break
            why = press(pattern[presses % len(pattern)])
            presses += 1
            if why:
                stopped = why
                break
            if watched is not None and seen() != first:
                stopped = "watched changed"
                break
            now = picture()
            # Still: the same as any of the last `cycle` presses' pictures (a blink, a loop of a few pictures).
            still = still + 1 if any(same(now, old) for old in recent) else 0
            recent = (recent + [now])[-cycle:]
            if stuck and still >= stuck:
                if backs >= 3:
                    stopped = "stuck"
                    break
                why = press(backing)
                backs += 1
                still = 0
                if why:
                    stopped = why
                    break
        value = self._cell(at, size)
        return {"frames": machine.frames, "presses": presses, "backs": backs, "stopped": stopped,
                "value": None if value is None else hex(value),
                **({"events": events} if self.reader is not None else {})}

    @tool("Several moves in one call: each step holds buttons for a few frames, then waits (a number of frames, "
          "or until something happens, as run_until), and may snap or take a picture. With pictures, they come "
          "back as one sheet, numbered by step. Stops where the call's time is up and says at which step.",
          steps=("array", "steps, each an object: buttons (array), stick ([x, y]), hold (frames held, default 4), "
                          "wait (frames after, default 0), until (object: run_until's address/size/equals/event/"
                          "still, with limit), times (repeat it, default 1), snap (true, or a name), shot (true), "
                          "every (with wait and snap: a snap each so many frames of the wait, to catch what "
                          "is there only while something happens)", True),
          on=("object", "what to do when the script says something: a kind of event (text, dialogue, choice, "
                        "any) and the steps to do right after the step it came in, e.g. {\"choice\": [{\"buttons\": "
                        "[\"down\"]}, {\"buttons\": [\"a\"], \"wait\": 30}]}. A step with times and "
                        "until_event (a kind) stops repeating once such an event has come; with until_quiet (a "
                        "number) it stops once that many repetitions in a row brought nothing: press through "
                        "a conversation of unknown length", False),
          prefix=("string", "names snaps taken with snap: true, as <prefix>-000, -001, … (default: step)", False),
          columns=("integer", "pictures across the sheet (default 4)", False),
          scale=("number", "each picture's scale on the sheet (default 1: text stays readable)", False),
          said=("object", "a snap the moment the script says text, wherever in a step that falls: {\"after\": "
                          "frames after the saying (default 2), \"shot\": true for a picture too}. Named "
                          "<prefix>-said-00, -01, …; for a page that gives way to the next within a step, which a "
                          "snap at the step's end (on) misses", False))
    def steps(self, steps: list, prefix: str = "step", columns: int = 4, scale: float = 1.0,
              on: dict | None = None, said: dict | None = None):
        machine, prefix = self._need(), self._name(prefix)
        if said is not None:
            if self.reader is None:
                raise HarnessError("no script is loaded to say anything: call script_try first")
            if not isinstance(said, dict) or set(said) - {"after", "shot"} or int(said.get("after", 2)) < 0:
                raise HarnessError("said is an object: after (frames, 0 or more) and shot")
        if not isinstance(steps, list) or not steps or not all(isinstance(s, dict) for s in steps):
            raise HarnessError("steps is a list of objects")
        known = {"buttons", "stick", "hold", "wait", "until", "times", "snap", "shot", "every", "until_event",
                 "until_quiet"}
        kinds = ("text", "any", *STORY)
        on = dict(on or {})
        if on or any("until_event" in step or "until_quiet" in step for step in steps):
            if self.reader is None:
                raise HarnessError("no script is loaded to say anything: call script_try first")
            if set(on) - set(kinds) or any(step.get("until_event", "text") not in kinds for step in steps) or not all(
                    isinstance(v, list) and v and all(isinstance(x, dict) and not set(x) - (known - {"until_event", "until_quiet", "times"})
                                                      for x in v) for v in on.values()):
                raise HarnessError("on and until_event take: text, dialogue, choice or any; on gives steps for each")
        plan, during = [], 0
        for step in [*steps, *(x for v in on.values() for x in v)]:
            if set(step) - known:
                raise HarnessError(f"a step has: {', '.join(sorted(known))}")
            if "every" in step:
                if int(step["every"]) < 1 or not step.get("snap") or int(step.get("wait", 0)) < 1 or "until" in step:
                    raise HarnessError("every goes with wait and snap, and is a number of frames")
                during += max(1, int(step.get("times", 1))) * (int(step["wait"]) // int(step["every"]))
        if during > MAX_STEPS:
            raise HarnessError(f"at most {MAX_STEPS} snaps during waits in a call")
        for step in [*steps, *(x for v in on.values() for x in v)]:
            self._buttons(step.get("buttons"))
            if "until" in step and not (isinstance(step["until"], dict) and "limit" in step["until"]):
                raise HarnessError("until is an object with a limit, as run_until takes")
        for step in steps:
            plan += [step] * max(1, min(int(step.get("times", 1)), MAX_STEPS))
        if len(plan) > MAX_STEPS:
            raise HarnessError(f"at most {MAX_STEPS} steps in a call")
        def came(kind, since):
            return any(kind == "any" or (e["kind"] == "text" and (kind == "text" or e.get("channel") == kind))
                       for e in events[since:])

        events, done, shots, why, left = [], [], [], None, 0
        queue, number, skipping, extra, answers = list(plan), -1, None, 0, 0
        quiet: dict[int, int] = {}
        # A snap the moment text is said: each frame (the hook _advance calls) looks at what has come since, and
        # takes the snap `after` frames on, mid-wait or mid-press as it falls.
        taken_said, due, seen_said, before_hook = [], [], [0], self.each_frame
        if said is not None:
            after, said_shot = int(said.get("after", 2)), bool(said.get("shot"))

            def on_frame():
                if before_hook is not None:
                    before_hook()
                for e in events[seen_said[0]:]:
                    if e["kind"] == "text" and len(taken_said) + len(due) < MAX_STEPS:
                        due.append(machine.frames + after)
                seen_said[0] = len(events)
                while due and machine.frames >= due[0]:
                    due.pop(0)
                    name = self.snap(f"{prefix}-said-{len(taken_said):02d}")["snap"]
                    taken_said.append({"snap": name, "frame": machine.frames})
                    if said_shot and len(shots) < MAX_SHOTS and machine.picture() is not None:
                        shots.append((f"s{len(taken_said) - 1}", self._picture()))
            self.each_frame = on_frame
        try:
            while queue:
                step = queue.pop(0)
                answer = step.get("_answer")
                if not answer:
                    number += 1
                if not answer and skipping is not None:
                    if step is skipping:                        # the rest of a repetition whose event has come
                        continue
                    skipping = None
                held, where, before = self._buttons(step.get("buttons")), self._stick(step.get("stick")), len(events)
                row, why = {"step": number, **({"on": answer} if answer else {})}, None
                answers = answers + 1 if answer else answers
                label = f"{prefix}-{number:03d}" + (f"-on{answers:02d}" if answer else "")
                if held or where != (0.0, 0.0):
                    _, why = self._advance(max(1, min(int(step.get("hold", 4)), MAX_FRAMES)), held, where, events)
                if not why and "until" in step:
                    until = dict(step["until"])
                    limit = max(1, min(int(until.pop("limit")), MAX_FRAMES))
                    try:
                        watch = self._watch(events, **until)
                    except TypeError:
                        raise HarnessError("until takes: limit, address, size, equals, event, still") from None
                    _, why = self._advance(limit, set(), (0.0, 0.0), events, watch)
                    row["stopped"], why = why or "limit", why if why == "time" else None
                elif not why and "every" in step:
                    # A snap each few frames of the wait: for what memory holds only while a line types or a
                    # window opens, which a snap after the wait never sees.
                    name = step["snap"] if isinstance(step["snap"], str) else label
                    left_frames, every, taken = min(int(step["wait"]), MAX_FRAMES), int(step["every"]), []
                    while left_frames >= every and not why:
                        _, why = self._advance(every, set(), (0.0, 0.0), events)
                        left_frames -= every
                        if not why:
                            taken.append(self.snap(f"{name}-{len(taken):02d}")["snap"])
                    if left_frames and not why:
                        _, why = self._advance(left_frames, set(), (0.0, 0.0), events)
                    row["snaps"] = taken
                elif not why and int(step.get("wait", 0)) > 0:
                    _, why = self._advance(min(int(step["wait"]), MAX_FRAMES), set(), (0.0, 0.0), events)
                row["frames"] = machine.frames
                if len(events) > before:
                    row["events"] = len(events) - before
                if why == "time":
                    done.append({**row, "stopped": "time"})
                    left = len(queue) + 1               # this step and those after it
                    break
                if step.get("snap") and "every" not in step:
                    name = step["snap"] if isinstance(step["snap"], str) else label
                    row["snap"] = self.snap(name)["snap"]
                if step.get("shot") and len(shots) < MAX_SHOTS and machine.picture() is not None:
                    shots.append((str(number), self._picture()))
                done.append(row)
                if not answer:
                    if "until_event" in step and came(step["until_event"], before):
                        row["stopped"], skipping = "event", step
                    if "until_quiet" in step:                   # so many times in a row with nothing said
                        quiet[id(step)] = 0 if came("any", before) else quiet.get(id(step), 0) + 1
                        if quiet[id(step)] >= max(1, int(step["until_quiet"])):
                            row["stopped"], skipping = "quiet", step
                    # What the script said in this step decides what comes next: the first kind of `on` it fits.
                    kind = next((k for k in on if came(k, before)), None)
                    if kind and extra < MAX_STEPS:
                        extra += len(on[kind])
                        queue[:0] = [{**x, "_answer": kind} for x in on[kind]]
        finally:
            self.each_frame = before_hook
        out = {"frames": machine.frames, "steps": done, **({"stopped": "time", "left": left} if why == "time" else {}),
               **({"said": taken_said} if said is not None else {}),
               **({"events": events} if self.reader is not None else {})}
        return ("both", out, self._png(self._sheet(shots, columns, scale))) if shots else out

    @tool("The picture on screen now, as a PNG.", scale=("number", "enlarge or shrink it, e.g. 0.5", False))
    def screenshot(self, scale: float = 1.0):
        return ("image", self._png(self._picture(), scale))

    def _picture(self):
        from PIL import Image
        frame = self._need().picture()
        if frame is None:
            raise HarnessError("the game hasn't shown a frame yet: run some frames")
        return Image.fromarray(frame)

    def _png(self, image, scale: float = 1.0) -> bytes:
        scale = max(0.1, min(4.0, float(scale)))
        if scale != 1.0:
            image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    # ---- memory

    @tool("Bytes of the game's memory, as hex.",
          address=("string", "the game's own address, hex, e.g. 0x80123400", True),
          size=("integer", f"how many bytes (at most {MAX_READ}: a larger size is cut, and said)", True),
          region=("string", "a named region to read by offset instead, e.g. vram", False),
          table=("string", "also read the bytes as text in the game's codes: a table's text (HEX=character lines)", False),
          rules=("array", "and/or by rules, worded as code_rule gives them: \"code = 0xA420 - JIS\", "
                          "\"(code & 0x1FFF) = 0x100 - ASCII\", \"code = JIS + 0x5CE0\"; the first that gives a "
                          "character is taken, so the narrowest first", False),
          unit=("integer", "bytes a code, for text: 1 or 2 (default 2)", False),
          big_endian=("boolean", "a code's byte order, for text (default true: as the bytes lie)", False),
          mask=("string", "bits of a code that say which character, hex, e.g. 1FFF: the rest are left out "
                          "before the table is asked", False),
          snaps=("array", "read the stretch (64 bytes at most) in each of these snaps instead: how a few "
                          "bytes go from moment to moment", False),
          mixed=("string", "also read the bytes as a script that has text among its commands: an encoding "
                           "(shift_jis, euc_jp, utf8) for what reads as text, each other byte as <XX>, and "
                           "`marks` named where they point", False),
          marks=("object", "with mixed: names for addresses inside the stretch, e.g. where a pointer stands "
                           "(a name to an address, hex): each is put into the text as [name]", False))
    def read(self, address: str, size: int, region: str = "", table: str = "", rules: list | None = None,
             unit: int = 2, big_endian: bool = True, mask: str = "", snaps: list | None = None,
             mixed: str = "", marks: dict | None = None) -> dict:
        at, asked = int(address, 16), int(size)
        size = max(0, min(asked, MAX_READ))
        # A size past the most is cut, and said: the rest wasn't read, not empty.
        cut = {"cut": f"{asked} bytes asked, {MAX_READ} read (the most a read gives): read on from "
                      f"{hex(at + MAX_READ)}"} if asked > MAX_READ else {}
        if snaps:                               # the same stretch in each of several moments, one under the other
            rows = {}
            for name, base, ram in self._each_memory(snaps):
                rows[name] = ram[max(0, at - base):max(0, at - base) + min(size, 64)].hex()
            return {"address": hex(at), "snaps": rows, **cut}
        if region:
            data = self._need().regions().get(region)
            if data is None:
                raise HarnessError(f"the core gives out no {region}")
            return {"offset": hex(at), "hex": data[at:at + size].hex(), **cut}
        data = self._read(at, size)
        out = {"address": hex(at), "hex": data.hex(), **cut}
        if mixed:
            try:
                where = {int(str(v), 16) - at: str(k) for k, v in (marks or {}).items()}
                out["mixed"] = search.mixed(data, mixed, where)
            except (LookupError, ValueError) as e:
                raise HarnessError(f"mixed takes an encoding, and marks addresses in hex: {e}") from None
        if table or rules:
            from .. import tbl
            try:
                chars = tbl.parse(table, "table").chars if table else None
                read = search.reader_of(chars, [str(r) for r in rules or ()], int(unit), bool(big_endian),
                                        int(mask, 16) if mask else None)
            except (search.SearchError, tbl.TableError, ValueError) as e:
                raise HarnessError(str(e)) from None
            out["text"] = read(data)
        return out

    def _each_memory(self, snaps: list | None):
        """As _memories, one at a time: each snap is read when its turn comes and let go after, so a tool that
        looks at each in turn holds one snap's memory, however many it is given. Every name is checked first,
        so a missing one is refused before any work."""
        from ..run import _load
        if not snaps:
            base, ram = self._need().ram()
            yield "", base, ram
            return
        paths = []
        for name in snaps:
            path = self.roots.inside("scratch", f"snaps/{self._name(name)}.ram.gz")
            if not path.is_file():
                raise HarnessError(f"no snap called {name}")
            paths.append((name, path))
        for name, path in paths:
            meta = json.loads(path.with_name(f"{name}.json").read_text(encoding="utf-8"))
            yield name, int(meta["base_address"], 16), _load(path)

    def _memories(self, snaps: list | None) -> list[tuple[str, int, bytes]]:
        """What a search looks through: live memory, or the snaps named (this session's, or scratch's)."""
        from ..run import _load
        if not snaps:
            base, ram = self._need().ram()
            return [("", base, ram)]
        out = []
        for name in snaps:
            path = self.roots.inside("scratch", f"snaps/{self._name(name)}.ram.gz")
            if not path.is_file():
                raise HarnessError(f"no snap called {name}")
            meta = json.loads(path.with_name(f"{name}.json").read_text(encoding="utf-8"))
            out.append((name, int(meta["base_address"], 16), _load(path)))
        return out

    @tool("Where a text or some bytes stand in main memory, or in snaps. A text is looked for in every encoding "
          "the catalog knows: Shift-JIS, EUC-JP, UTF-16 either way, UTF-8, Shift-JIS as 16-bit little-endian "
          "numbers, and the game's own table when you give one. A line that isn't found whole can be looked for "
          "in parts.",
          text=("string", "the text to look for", False),
          hex=("string", "or bytes, as hex", False),
          encodings=("array", f"for a text: any of {', '.join(search.ENCODINGS)} (default: all)", False),
          table=("string", "a table's text (HEX=character lines), to look with the game's own codes", False),
          pieces=("integer", "also look for the text's stretches of this many characters, and say which are found", False),
          snaps=("array", "look in these snaps instead of live memory", False))
    def find(self, text: str = "", hex: str = "", encodings: list | None = None, table: str = "",
             pieces: int = 0, snaps: list | None = None) -> dict:
        try:
            needles = {"hex": bytes.fromhex(hex)} if hex else {}
            if text:
                needles.update(search.needles(text, encodings, table or None))
            if not text and not hex or hex and not needles["hex"]:
                raise HarnessError("give a text or hex bytes to look for")
            parts = {part: search.needles(part, encodings, table or None)
                     for part in (search.pieces(text, max(2, int(pieces))) if pieces and text else [])}
        except search.SearchError as e:
            raise HarnessError(str(e)) from None
        out = {}
        for name, base, ram in self._each_memory(snaps):
            found = {}
            for kind, needle in needles.items():
                hits = search.places(ram, needle, MAX_HITS)
                if hits:
                    found[kind] = [f"0x{base + at:08X}" for at in hits]
                    # A hit with text standing right before it is the middle of a line: where the line
                    # begins is said, so that it isn't taken for a buffer's start.
                    before = {at: search.text_before(ram, at, kind) for at in hits[:16]}
                    middle = {f"0x{base + at:08X}": {"characters_before": n, "text_from": f"0x{base + at - 2 * n:08X}"}
                              for at, n in before.items() if n >= 2}      # one may be a mark's last byte
                    if middle:
                        found.setdefault("mid_line", {}).update(middle)
            for part, its in parts.items():
                for kind, needle in its.items():
                    hits = search.places(ram, needle, 8)
                    if hits and kind not in found:
                        found.setdefault("pieces", {}).setdefault(kind, {})[part] = [f"0x{base + at:08X}" for at in hits]
            out[name] = found
        if text and not any(out.values()):
            # Nothing anywhere: say what that usually means and what to try, so that it isn't searched for again.
            return {"found": out[""] if not snaps else out,
                    "not_found": "not in memory in these encodings. Next: find_relative (codes of the game's own). "
                                 "If that finds nothing either, the game may keep no line at all and draw from "
                                 "its cartridge or its script: snap each frame of a line's typing and ask "
                                 "`advancing` for the pointer."}
        return {"found": out[""]} if not snaps else {"found": out}

    @tool("The stretches of memory that read as Japanese, longest first, with where each is and in which "
          "encoding: what to hold against the screen when you have no line to look for yet. Only standard "
          "encodings; a game with codes of its own shows nothing here (see find_relative).",
          least=("integer", "the fewest Japanese characters a stretch must have (default 8)", False),
          snaps=("array", "look in these snaps instead of live memory", False))
    def text_runs(self, least: int = 8, snaps: list | None = None) -> dict:
        out = {}
        for name, base, ram in self._each_memory(snaps):
            out[name] = [{"address": f"0x{base + at:08X}", "encoding": kind, "text": text[:120]}
                         for at, kind, text in search.runs(ram, max(4, int(least)), 40)]
        return {"runs": out[""]} if not snaps else {"runs": out}

    @tool("Looks for a line you read off the screen when the game's codes are its own: the places where the same "
          "characters have the same code and different ones different codes, each with the codes it would give "
          "the characters (the start of a table). One line needs repeated characters, the longer the better. "
          "Surer: `lines`, two or more snaps each with the line on its screen; one place must fit them all, and "
          "characters the lines share must have the same code. Write wide spaces where the screen has them. "
          "A window kept as tiles (a cell a character, blanks too): give a row with its spaces as plain "
          "spaces and `spaces`; each is then a cell, and all of them the same tile. Rows found nowhere are also "
          "tried padded with blank cells to each width (`padded`): a window whose rows are that many cells.",
          text=("string", "the line on screen now; a line break stands for `gap` cells", False),
          lines=("object", "or several moments: a snap's name (or `now` for live memory) to the line on its screen", False),
          unit=("integer", "bytes a code: 1, 2 or 4 (default 1; try 2 as well)", False),
          order=("string", "unicode (kana as in JIS, small and voiced ones between) or gojuon (the 46 plain kana in a row)", False),
          stride=("integer", "codes are this many cells apart (default 1; 2 where each character has a second cell)", False),
          gap=("integer", "cells a line break in the line stands for (default 1: one code for the break)", False),
          big_endian=("boolean", "the byte order of 2- and 4-byte codes (default: the console's)", False),
          spaces=("boolean", "a plain space in the line is a cell too, the same code each time (default: "
                             "spaces are left out)", False),
          apart=("array", "or several lines of one scene, each wherever it lies in memory (a script read where "
                          "it is): two of them must share three characters or more. Kana stretches are safest", False),
          snap=("string", "with apart: the snap to look in (default: live memory)", False),
          window=("integer", "with apart: how far from the first line the others may lie, in bytes (default 65536)", False))
    def find_relative(self, text: str = "", lines: dict | None = None, unit: int = 1, order: str = "",
                      stride: int = 1, gap: int = 1, big_endian: bool | None = None, apart: list | None = None,
                      snap: str = "", window: int = 0x10000, spaces: bool = False) -> dict:
        if apart:
            _, base, ram = self._memories([snap] if snap else None)[0]
            if big_endian is None:
                big_endian = getattr(self.machine, "platform", "") in BIG_ENDIAN
            try:
                total, sets = search.relative_apart(ram, [str(line) for line in apart], int(unit), bool(big_endian),
                                                    int(window))
            except search.SearchError as e:
                raise HarnessError(str(e)) from None
            width = 2 * int(unit)
            return {"count": total, "sets": [{"lines": [f"0x{base + at:08X}" for at in places],
                                              "codes": {ch: f"{code:0{width}X}" for ch, code in codes.items()}}
                                             for places, codes in sets]}
        if bool(text) == bool(lines):
            raise HarnessError("give the line on screen now (text), lines for several snaps, or apart")
        wanted = lines or {snap or "now": text}                    # the line on screen now, or in the snap given
        memories, base = [], 0
        for name, line in wanted.items():
            _, base, ram = self._memories(None if name == "now" else [name])[0]
            memories.append((ram, str(line)))
        if big_endian is None:
            big_endian = getattr(self.machine, "platform", "") in BIG_ENDIAN
        shape = lambda hits, unit: [{"address": f"0x{base + at:08X}",                          # noqa: E731
                                     "codes": {ch: f"{code:0{2 * unit}X}" for ch, code in codes.items()}}
                                    for at, codes in hits]
        try:
            total, hits = search.relative(memories, int(unit), bool(big_endian), order, int(stride), int(gap),
                                          spaces=bool(spaces))
            out = {"count": total, "places": shape(hits, int(unit))}
        except search.SearchError as e:
            if not str(e).startswith("too little"):
                raise HarnessError(str(e)) from None
            total, out = 0, {"count": 0, "places": [], "note": str(e)}
        if not total:
            # The same line the other ways a game may keep it: codes of two bytes, the other byte order, kana
            # as far apart as in an order (up or down). Only the ways that fit a few places are given.
            # And, for a line with spaces in it, each space as a cell: a window kept as tiles.
            ways = []
            spaced = sorted({bool(spaces), any(" " in line.strip() for _, line in memories)})
            for size in (1, 2):
                for big in ((False, True) if size > 1 else (False,)):
                    for by in ("", "unicode", "gojuon"):
                        for cells in spaced:
                            if ((size, big, by, cells) == (int(unit), bool(big_endian), order, bool(spaces))
                                    and "note" not in out):
                                continue
                            try:
                                count, some = search.relative(memories, size, big, by, int(stride), int(gap), 3,
                                                              spaces=cells)
                            except search.SearchError:
                                continue
                            if 0 < count <= 50:
                                ways.append({"unit": size, "big_endian": big, "order": by, "count": count,
                                             "places": shape(some, size), **({"spaces": True} if cells else {})})
            if ways:
                out["other_ways"] = sorted(ways, key=lambda w: (w["count"], not w["order"]))   # an order is the surer fit
            elif "note" in out:
                raise HarnessError(out["note"])
        rows = [row for row in text.split("\n") if row.strip()] if text else []
        if not total and len(rows) > 1:
            # The game may have other codes among the line's (a colour, a wait): its rows two at a time, then
            # one at a time, as far as each gives enough to go by.
            parts = []
            for size in (2, 1):
                for i in range(len(rows) - size + 1):
                    try:
                        count, some = search.relative([(memories[0][0], "\n".join(rows[i:i + size]))], int(unit),
                                                      bool(big_endian), order, int(stride), int(gap), 5,
                                                      spaces=bool(spaces))
                    except search.SearchError:
                        continue
                    if count:
                        parts.append({"rows": list(range(i + 1, i + size + 1)), "count": count,
                                      "places": shape(some, int(unit))})
                if parts:
                    break
            out["parts"] = parts
            # A window kept as cells of a fixed width pads each row with blanks to the width, so a row break
            # stands for as many cells as the row is short of it (Kaeru no Tame ni Kane wa Naru: rows of 18). The
            # rows padded to each width from the longest row's, a space a cell, wide ones too.
            cells = [row.replace("　", " ") for row in rows]
            padded = []
            for width in range(max(len(row) for row in cells), 41):
                line = "".join(row.ljust(width) for row in cells[:-1]) + cells[-1]
                try:
                    count, some = search.relative([(memories[0][0], line)], int(unit), bool(big_endian), order,
                                                  int(stride), int(gap), 5, spaces=True)
                except search.SearchError:
                    continue
                if 0 < count <= 50:
                    padded.append({"width": width, "count": count, "places": shape(some, int(unit))})
            if padded:
                out["padded"] = sorted(padded, key=lambda p: p["count"])[:5]
        return out

    @tool("What a script can check to know it is on this build of the game: places in memory that hold the same "
          "in every snap given, and a Lua line that checks them. Give snaps from power-on's first seconds, the "
          "title and play, so that nothing loaded later is picked; and, if you have them, snaps of another "
          "game or build of the same family, which the places must differ in.",
          snaps=("array", "snaps of this game: early, the title, in play (three or more)", True),
          others=("array", "snaps of another build or game that must differ there", False))
    def suggest_build_words(self, snaps: list, others: list | None = None) -> dict:
        if not isinstance(snaps, list) or len(snaps) < 3:
            raise HarnessError("give three or more snaps: early after power-on, the title, in play")
        mine, theirs = self._memories(snaps), self._memories(others) if others else []
        platform = self._need().platform
        try:
            picks, candidates = search.build_words(mine[0][1], [ram for _, _, ram in mine], platform,
                                                   [ram for _, _, ram in theirs])
        except search.SearchError as e:
            raise HarnessError(str(e)) from None
        if not picks:
            return {"candidates": 0, "words": [], "note": "nothing stays the same in all of them: the check can "
                    "only be the game's id (reader.game_id). Say so in the script and the pull request."}
        big = platform in BIG_ENDIAN
        if len(picks[0][1]) == 4 and candidates and platform in ("ps1", "psp", "ps2", "gc", "wii"):
            read = "mem.u32be" if big else "mem.u32"
            terms = [f"{read}(0x{at:08X}) ~= 0x{int.from_bytes(data, 'big' if big else 'little'):08X}" for at, data in picks]
        else:
            terms = [f'mem.read(0x{at:06X}, {len(data)}) ~= "' + "".join(f"\\x{b:02X}" for b in data) + '"'
                     for at, data in picks[:1]]
        lua = ("if " + " or ".join(terms) + ' then\n    error("this script is for another build of the game", 0)\nend')
        frames = [json.loads(self.roots.inside("scratch", f"snaps/{n}.json").read_text(encoding="utf-8")).get("frame")
                  for n in snaps]
        return {"candidates": candidates, "words": [{"address": f"0x{at:08X}", "bytes": data.hex()} for at, data in picks],
                "lua": lua, "snap_frames": frames,
                "note": "They hold from the earliest snap given on. Before that a host gets \"another build\" "
                        "and tries again (docs/script-api.md): give an early snap so that this is short."}

    @tool("Keeps this moment's memory under a name, to compare with others, and writes it to scratch as an "
          "inbox-style snapshot (ram, video memory, the frame) that `yomikata run` and `check` take.",
          name=("string", "a name for it", True),
          ocr=("array", "the lines on screen, if you read them off the frame", False),
          no_text=("boolean", "true when the screen shows no text", False))
    def snap(self, name: str, ocr: list | None = None, no_text: bool = False) -> dict:
        machine, name = self._need(), self._name(name)
        folder = self.roots.scratch / "snaps"
        self._not_anothers(folder, name, machine.platform)
        base, ram = machine.ram()
        regions = machine.regions()
        self.snaps[name] = Snap(base, machine.frames)
        # All but the session's first are written as what differs from it (snapfile.py): a few kilobytes, not
        # all of memory again. Writing over the first makes this one the first; the snaps kept as differing
        # from a file about to be written over (this run's, or an earlier run's under the same name) are
        # made whole first, so that a name used twice costs disk and not the other snaps.
        kept = sum(snapfile.release(folder / f"{name}.{region}.gz") for region in ("ram", *regions))
        first = self.first_snap if self.first_snap and self.first_snap[0] != name else None
        if first is not None and not (folder / f"{first[0]}.ram.gz").is_file():
            first = None
        snapfile.write(folder / f"{name}.ram.gz", ram, (f"{first[0]}.ram.gz", first[1]) if first else None)
        for region, data in regions.items():
            other = (f"{first[0]}.{region}.gz", first[2][region]) if first and region in first[2] else None
            snapfile.write(folder / f"{name}.{region}.gz", data, other)
        if first is None:
            self.first_snap = (name, ram, regions)
        meta = {"platform": machine.platform, "game": self.game, "base_address": f"0x{base:X}",
                "frame": machine.frames, "ocr": list(ocr or [])}
        if no_text:
            meta["no_text"] = True
        elif not ocr:
            meta["ocr_unavailable"] = True
        if self.pokes:                          # what this moment shows was reached with memory written
            meta["memory_writes"] = self.pokes
        if self.reader is not None:             # what the script said on the way here, for `compare` with `live`
            meta["said"] = self.said
        self.said = []
        (folder / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        if machine.picture() is not None:
            (folder / f"{name}.png").write_bytes(self._png(self._picture()))
        return {"snap": name, "frame": machine.frames, "bytes": len(ram), "regions": sorted(regions),
                **({"written_over": "a snap of this name was there, and others were kept as what differs from "
                                    "it: they are whole files now. A name of its own for each snap saves that"}
                   if kept else {}),
                **({"memory_writes": self.pokes} if self.pokes else {})}

    def _not_anothers(self, folder: Path, name: str, platform: str) -> None:
        """Refuses a snap's name that another game's snap holds: snaps of every game share one folder, and a
        route's snaps written over by another game's can't be compared again. A snap from before games were
        written beside it is known by its console only."""
        try:
            held = json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(held, dict):
            return
        game, console = held.get("game"), held.get("platform")
        if game and game != self.game or not game and console not in (None, platform):
            whose = f"{game}'s" if game else f"a {console} game's"
            raise HarnessError(f"the snap named {name} is {whose}: give this game's snaps a prefix of their own "
                               "(the start of its name, say), so that no other game's snaps are written over")

    @tool("Says what a snap's screen showed, once you have read it off its picture: the lines, or that there is "
          "no text. `compare` holds what a script reads against these. For a route's screens, give them all "
          "at once with `screens`: one call for a sheet's worth or a whole route, where it was one a screen.",
          name=("string", "the snap's name", False),
          ocr=("array", "the lines on screen", False),
          no_text=("boolean", "true when the screen shows no text", False),
          screens=("object", "or many snaps at once: each snap's name to its lines (a list), or to false or "
                             "an empty list for a screen that shows no text. Nothing is written unless "
                             "every one of them is a snap", False))
    def snap_lines(self, name: str = "", ocr: list | None = None, no_text: bool = False,
                   screens: dict | None = None) -> dict:
        if screens is not None:
            if name or ocr or no_text or not isinstance(screens, dict) or not screens:
                raise HarnessError("give screens alone: a snap's name to its lines, or to false for no text")
            wanted = {}
            for snap, lines in screens.items():
                if lines is not False and not isinstance(lines, list):
                    raise HarnessError(f"{snap}: its lines as a list, or false for a screen without text")
                path = self.roots.inside("scratch", f"snaps/{self._name(snap)}.json")
                if not path.is_file():
                    raise HarnessError(f"no snap called {snap}")
                wanted[snap] = (path, [str(line) for line in lines or []])
            for path, lines in wanted.values():
                self._lines(path, lines)
            return {"snaps": len(wanted), "with_text": sum(1 for _, lines in wanted.values() if lines),
                    "no_text": sum(1 for _, lines in wanted.values() if not lines)}
        path = self.roots.inside("scratch", f"snaps/{self._name(name)}.json") if name else None
        if path is None or not path.is_file():
            raise HarnessError(f"no snap called {name}")
        if bool(ocr) == bool(no_text):
            raise HarnessError("give the lines, or no_text: true")
        lines = [str(line) for line in ocr or []]
        self._lines(path, lines)
        return {"snap": name, "lines": len(lines), "no_text": bool(no_text)}

    @staticmethod
    def _lines(path: Path, lines: list[str]) -> None:
        """What a snap's screen showed goes into the file beside it: its lines, or that it has none."""
        meta = json.loads(path.read_text(encoding="utf-8"))
        for key in ("ocr_unavailable", "no_text"):
            meta.pop(key, None)
        meta["ocr"] = lines
        if not lines:
            meta["no_text"] = True
        path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    @tool("The pictures of several snaps as one sheet, each under its name: many moments for one look.",
          snaps=("array", "snaps' names, in this session or written to scratch before", True),
          columns=("integer", "pictures across (default 4)", False),
          scale=("number", "each picture's scale (default 1: text stays readable)", False),
          crop=("array", "only this part of each picture, [x, y, width, height] in its pixels: the window the "
                         "text is in, so that a route's screens can be read by the dozen", False),
          number=("integer", "label each with a number counted from this one, before its name: what to "
                             "call a screen by when reading a route's sheets one after the other", False))
    def sheet(self, snaps: list, columns: int = 4, scale: float = 1.0, crop: list | None = None,
              number: int | None = None):
        from PIL import Image
        if not snaps or len(snaps) > MAX_SHOTS:
            raise HarnessError(f"1 to {MAX_SHOTS} snaps")
        if crop is not None and (len(crop) != 4 or min(int(v) for v in crop) < 0 or int(crop[2]) < 1 or int(crop[3]) < 1):
            raise HarnessError("crop is [x, y, width, height]")
        shots = []
        for i, name in enumerate(snaps):
            path = self.roots.inside("scratch", f"snaps/{self._name(name)}.png")
            if not path.is_file():
                raise HarnessError(f"no picture was kept for {name}")
            image = Image.open(path).convert("RGB")
            if crop is not None:
                x, y, w, h = (int(v) for v in crop)
                image = image.crop((min(x, image.width - 1), min(y, image.height - 1),
                                    min(x + w, image.width), min(y + h, image.height)))
            shots.append((name if number is None else f"{int(number) + i} {name}", image))
        return ("image", self._png(self._sheet(shots, columns, scale)))

    def _sheet(self, shots: list, columns: int = 4, scale: float = 1.0):
        """Pictures side by side, `columns` across, each with its label in a strip above it."""
        from PIL import Image, ImageDraw
        columns, scale = max(1, min(int(columns), 8)), max(0.1, min(2.0, float(scale)))
        width = max(1, round(max(image.width for _, image in shots) * scale))
        height = max(1, round(max(image.height for _, image in shots) * scale))
        strip = 12
        rows = (len(shots) + columns - 1) // columns
        sheet = Image.new("RGB", (width * min(columns, len(shots)), (height + strip) * rows))
        draw = ImageDraw.Draw(sheet)
        for i, (label, image) in enumerate(shots):
            x, y = (i % columns) * width, (i // columns) * (height + strip)
            small = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
            sheet.paste(small, (x, y + strip))
            draw.text((x + 2, y), label[:width // 6], fill=(255, 255, 0))
        return sheet

    @tool("How this moment's memory differs from a snap's: one of this session, or one written to scratch by "
          "another. Says whether two harnesses that did the same thing ended up the same.",
          snap=("string", "the snap's name", True))
    def differs(self, snap: str) -> dict:
        import numpy as np
        from ..run import _load
        path = self.roots.inside("scratch", f"snaps/{self._name(snap)}.ram.gz")
        if not path.is_file():
            raise HarnessError(f"no snap called {snap}")
        base, ram = self._need().ram()
        then, now = np.frombuffer(_load(path), dtype=np.uint8), np.frombuffer(ram, dtype=np.uint8)
        if len(then) != len(now):
            raise HarnessError("the snap isn't of the same memory")
        where = np.nonzero(then != now)[0]
        meta = json.loads(path.with_name(f"{snap}.json").read_text(encoding="utf-8"))
        return {"same": len(where) == 0, "bytes_differing": int(len(where)), "frame": self.machine.frames,
                "snap_frame": meta.get("frame"), "first_places": [f"0x{base + int(i):08X}" for i in where[:20]]}

    @tool("Places in memory that differ between two snaps, narrowed by others: the same as the first in some, "
          "the same as the second in others. Finds a flag, a counter, a cursor. Or, with snaps and a pattern, "
          "places that go as the pattern says over those snaps: abab is one value, another, the first again, "
          "the other again; aAaA is one value, anything else, the value again, anything else (a pointer that "
          "is set while a window is open); a dot is a snap that doesn't matter.",
          a=("string", "a snap's name", False), b=("string", "another's", False),
          same_as_a=("array", "snaps where the place must hold what it holds in a", False),
          same_as_b=("array", "snaps where it must hold what it holds in b", False),
          unit=("integer", "bytes compared together: 1, 2 or 4 (default 1)", False),
          snaps=("array", "snaps in order, for a pattern", False),
          pattern=("string", "a character for each snap: the same small letter is the same value, another "
                             "letter another value, a capital anything but its small letter's value, a dot "
                             "anything", False),
          within=("array", "only places from one address to another, hex: [\"0x80080000\", \"0x80090000\"]", False))
    def changed(self, a: str = "", b: str = "", same_as_a: list | None = None, same_as_b: list | None = None,
                unit: int = 1, snaps: list | None = None, pattern: str = "", within: list | None = None) -> dict:
        import numpy as np
        if unit not in (1, 2, 4):
            raise HarnessError("unit is 1, 2 or 4")
        bases = {}
        if within is not None and (len(within) != 2 or int(str(within[0]), 16) > int(str(within[1]), 16)):
            raise HarnessError("within is two addresses, the lower first")

        def inside(places, base):
            if within is None:
                return places
            low, high = (int(str(v), 16) for v in within)
            return places[(places * unit + base >= low) & (places * unit + base < high)]

        def cells(name):                                 # a snap of this session, or one kept in scratch
            _, bases[name], ram = self._memories([name])[0]
            data = np.frombuffer(ram, dtype=np.uint8)
            return data[:len(data) // unit * unit].reshape(-1, unit)
        if snaps or pattern:
            snaps = list(snaps or ())
            if a or b or same_as_a or same_as_b:
                raise HarnessError("a pattern goes with snaps alone")
            if len(pattern) != len(snaps) or not all(ch == "." or ch.isalpha() and ch.isascii() for ch in pattern):
                raise HarnessError("the pattern has a character for each snap: letters and dots")
            values = {}                                  # a small letter: the first snap's cells that has it
            for name, ch in zip(snaps, pattern):
                if ch.islower() and ch not in values:
                    values[ch] = cells(name)
            if len(values) < 1 or any(ch.isupper() and ch.lower() not in values for ch in pattern) \
                    or len({v.shape for v in values.values()}) != 1:
                raise HarnessError("a capital needs its small letter in the pattern, and the snaps one memory")
            if len(values) == 1 and not any(ch.isupper() for ch in pattern):
                raise HarnessError("a pattern of one value finds nothing: give another letter or a capital")
            letters = sorted(values)
            keep = np.ones(len(values[letters[0]]), dtype=bool)
            for i, one in enumerate(letters):
                for other in letters[i + 1:]:
                    keep &= (values[one] != values[other]).any(axis=1)
            for name, ch in zip(snaps, pattern):
                if ch == ".":
                    continue
                now = cells(name)
                if now.shape != values[ch.lower()].shape:
                    raise HarnessError("the snaps aren't of the same memory")
                same = (now == values[ch.lower()]).all(axis=1)
                keep &= same if ch.islower() else ~same
            base = bases[snaps[0]]
            places = inside(np.nonzero(keep)[0], base)
            return {"count": int(len(places)),
                    "places": [{"address": f"0x{base + int(i) * unit:08X}",
                                **{ch: values[ch][i].tobytes().hex() for ch in letters}} for i in places[:MAX_HITS]]}
        if not a or not b:
            raise HarnessError("give two snaps (a, b), or snaps and a pattern")
        first, second = cells(a), cells(b)
        if first.shape != second.shape:
            raise HarnessError("the snaps aren't of the same memory")
        keep = (first != second).any(axis=1)
        for name in same_as_a or ():
            keep &= (cells(name) == first).all(axis=1)
        for name in same_as_b or ():
            keep &= (cells(name) == second).all(axis=1)
        base = bases[a]
        places = inside(np.nonzero(keep)[0], base)
        return {"count": int(len(places)),
                "places": [{"address": f"0x{base + int(i) * unit:08X}", "a": first[i].tobytes().hex(),
                            "b": second[i].tobytes().hex()} for i in places[:MAX_HITS]]}

    @tool("Places that hold the same in every moment of a group and differ from group to group: which map "
          "it is, which chapter, who is in the party. Give saved states by group, a name for each group (the "
          "states you kept on one map under one name); each is loaded for a look and the game is then put "
          "back where it was. Or give snaps. Two groups may turn out to be one (two rooms of one map): `least` "
          "lets some share a value, and the answer says which do.",
          groups=("object", "a name for each group to its states' names (or snaps'), two groups or more", True),
          snaps=("boolean", "the names are snaps, not states", False),
          least=("integer", "how many different values the groups must show (default: one for each group)", False),
          unit=("integer", "bytes compared together: 1, 2 or 4 (default 1)", False),
          within=("array", "only places from one address to another, hex", False),
          values=("object", "only places whose values fit: for a group's name, \"0\" (zero), \"!0\" (not zero), a "
                            "value (0x80) or \"!\" and a value (not it); or the word \"flag\" for places zero in "
                            "some groups and not in the others (a flag that goes on or off between them)", False))
    def grouped(self, groups: dict, snaps: bool = False, least: int = 0, unit: int = 1, within: list | None = None,
                values: dict | str | None = None) -> dict:
        import numpy as np
        machine = self._need()
        if unit not in (1, 2, 4):
            raise HarnessError("unit is 1, 2 or 4")
        if not isinstance(groups, dict) or len(groups) < 2 or not all(isinstance(v, list) and v for v in groups.values()):
            raise HarnessError("groups is a name for each group to a list of states or snaps, two groups or more")
        if within is not None and (len(within) != 2 or int(str(within[0]), 16) > int(str(within[1]), 16)):
            raise HarnessError("within is two addresses, the lower first")
        memories, base = {}, None
        if snaps:
            for name in {n for v in groups.values() for n in v}:
                _, base, memories[name] = self._memories([name])[0]
        else:
            paths = {n: self._state_file(n) for v in groups.values() for n in v}
            for name, path in paths.items():
                if not path.is_file():
                    raise HarnessError(f"no such state: {name}")
            here = machine.save_state()
            try:
                for name, path in paths.items():
                    try:
                        machine.load_state(path.read_bytes())
                    except RuntimeError as e:
                        raise HarnessError(f"{name}: {e}") from None
                    machine.run(1, set(), (0.0, 0.0))       # a core may show a state's memory only once it has run
                    base, memories[name] = machine.ram()
            finally:
                machine.load_state(here)
                if self.reader is not None:
                    self.reader.state_loaded()
                    self._collect(self.reader.take(), [])
        if len({len(m) for m in memories.values()}) != 1:
            raise HarnessError("these aren't all of the same memory")

        def cells(name):
            data = np.frombuffer(memories[name], dtype=np.uint8)
            return data[:len(data) // unit * unit].reshape(-1, unit)
        names = list(groups)
        # The same within each group, and how many different values the groups show (search.group_cells, which
        # page_buffer shares).
        firsts, stands, kinds = search.group_cells([[cells(n) for n in groups[g]] for g in names])
        keep = np.ones(len(firsts[0]), dtype=bool)
        for still in stands:
            keep &= still
        least = len(names) if not least else max(2, min(int(least), len(names)))
        keep &= kinds >= least
        if values is not None:
            # Only places whose values fit: a flag going on or off, or each group's own condition.
            zero = [(first == 0).all(axis=1) for first in firsts]
            if values == "flag":
                some = np.zeros(len(keep), dtype=bool)
                every = np.ones(len(keep), dtype=bool)
                for z in zero:
                    some |= z
                    every &= z
                keep &= some & ~every
            elif isinstance(values, dict) and values and set(values) <= set(names):
                order = ">" if machine.platform in BIG_ENDIAN else "<"
                for g, want in values.items():
                    first = firsts[names.index(g)]
                    number = first.view(f"{order}u{unit}").reshape(-1) if unit > 1 else first.reshape(-1)
                    text = str(want).strip()
                    negate = text.startswith("!")
                    try:
                        value = int(text.lstrip("!"), 0)
                    except ValueError:
                        raise HarnessError(f"values: {want} isn't 0, !0, a value or ! and a value") from None
                    fits = number == value
                    keep &= ~fits if negate else fits
            else:
                raise HarnessError("values is \"flag\", or a group's name to its condition (0, !0, 0x80, !0x80) "
                                   "for groups that are given")
        places = np.nonzero(keep)[0]
        if within is not None:
            low, high = (int(str(v), 16) for v in within)
            places = places[(places * unit + base >= low) & (places * unit + base < high)]
        places = sorted((int(i) for i in places), key=lambda i: (-int(kinds[i]), i))
        rows = []
        for i in places[:MAX_HITS]:
            values = {g: first[i].tobytes().hex() for g, first in zip(names, firsts)}
            alike = [sorted(g for g in names if values[g] == v) for v in sorted(set(values.values()))]
            rows.append({"address": f"0x{base + i * unit:08X}", "values": values,
                         **({"alike": [a for a in alike if len(a) > 1]} if int(kinds[i]) < len(names) else {})})
        out = {"count": len(places), "groups": len(names), "places": rows}
        if len(places) > MAX_HITS:
            # The list is cut, the places that differ most first: a flag elsewhere may not be among those given.
            out["cut"] = (f"{len(places)} places fit, {MAX_HITS} given (those the most groups differ at, then by "
                          "address): narrow it with `values` (\"flag\", or each group's value) or `within`")
        return out

    @tool("Where a page of text is written whole into memory: the buffer a game decompresses or unpacks each page "
          "into before its window shows it, for a game whose script lies packed in the cartridge or on the disc. "
          "Snap a few pages (three or more, two snaps or more while each stands, and if you can one just before "
          "each opens): it gives the stretches the pages' bytes differ in, those that held still while each page "
          "stood first, ranked against the size a page takes (`size`, or estimated: one far longer, graphics or a "
          "whole unpacked block, goes down), and one that moves while a page stands (a tile copy, text scrolled "
          "through) ranked down; "
          "one that never stands is left out), and a buffer kept twice as two stretches that alternate. With "
          "each, when each page's bytes were first there, and whether pages shown twice hold the same. Bytes "
          "only: read them with find_relative or a table after.",
          pages=("array", "for each page shown, the snaps taken while it stood: [[\"p1-a\", \"p1-b\"], ...], two "
                          "pages or more", True),
          before=("array", "for each page, a snap taken just before it opened (or null)", False),
          repeats=("array", "pairs of page indices (from 0) that show the same page: [[0, 3]]", False),
          least=("integer", "the shortest stretch kept, in bytes (default 16)", False),
          within=("array", "only stretches from one address to another, hex", False),
          size=("integer", "the bytes a page is expected to take: the most characters a page shows times the bytes "
                           "a character takes (2 for 16-bit codes). Stretches are ranked against it; without it, "
                           "against the middle length of those that stood (estimated), which the answer says", False),
          cap=("integer", "with no size: stretches longer than this (default 4096) count as graphics or whole "
                          "unpacked blocks, left out of the estimate", False))
    def page_buffer(self, pages: list, before: list | None = None, repeats: list | None = None, least: int = 16,
                    within: list | None = None, size: int = 0, cap: int = 4096) -> dict:
        if not isinstance(pages, list) or len(pages) < 2 or not all(isinstance(p, list) and p for p in pages):
            raise HarnessError("pages is a list of pages, each a list of snaps taken while it stood: two pages or more")
        if before is not None and (not isinstance(before, list) or len(before) > len(pages)):
            raise HarnessError("before has a snap (or null) for each page, no more")
        pairs = []
        for pair in repeats or ():
            if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(k, int) and 0 <= k < len(pages)
                                                                      for k in pair)):
                raise HarnessError("repeats is pairs of page indices, from 0: [[0, 3]]")
            pairs.append(tuple(pair))
        if within is not None and (len(within) != 2 or int(str(within[0]), 16) > int(str(within[1]), 16)):
            raise HarnessError("within is two addresses, the lower first")
        memories, bases = {}, set()
        for name in {n for page in pages for n in page} | {n for n in (before or ()) if n}:
            _, base, memories[name] = self._memories([name])[0]
            bases.add(base)
        if len(bases) != 1 or len({len(m) for m in memories.values()}) != 1:
            raise HarnessError("these snaps aren't all of the same memory")
        base = bases.pop()
        found, expected = search.page_buffers([[memories[n] for n in page] for page in pages],
                                              [memories[n] if n else None for n in before] if before else None,
                                              pairs, least=max(1, int(least)), most=10 ** 6, size=max(0, int(size)),
                                              cap=max(16, int(cap)))
        if within is not None:
            low, high = (int(str(v), 16) for v in within)
            found = [f for f in found if low <= base + f["offset"] < high]
        rows = []
        for f in found[:20]:
            row = {"address": f"0x{base + f.pop('offset'):08X}", **f}
            if "alternates_with" in row:
                row["alternates_with"] = f"0x{base + row['alternates_with']:08X}"
            rows.append(row)
        return {"count": len(found), "expected": expected, "stretches": rows}

    @tool("Reads a name off a naming screen. Snap the empty name, enter a character, snap, enter another, "
          "snap, and so on: this finds the places that took a value one after the other, which are the name, "
          "and gives each character's code. With kana in the order of the fifty sounds it says the rule the "
          "codes follow, and the lines of a table for `find`.",
          snaps=("array", "the snaps in order: the empty name first, then one after each character", True),
          characters=("string", "the characters entered, in order", True))
    def entered(self, snaps: list, characters: str) -> dict:
        import numpy as np
        if len(characters) < 2 or len(snaps) != len(characters) + 1:
            raise HarnessError("give two characters or more, and one snap more than characters")
        memories = self._memories(snaps)
        raw = [np.frombuffer(ram, dtype=np.uint8) for _, _, ram in memories]
        if len({len(r) for r in raw}) != 1:
            raise HarnessError("the snaps aren't of the same memory")
        base, count, found = memories[0][1], len(characters), []
        for unit in (1, 2):
            moments = [r[:len(r) // unit * unit].reshape(-1, unit) for r in raw]
            cells = len(moments[0]) - count
            keep = np.ones(cells, dtype=bool)
            # The cell of character k: set at snap k+1, kept after. Before its turn it may change too: a game
            # moves the name's end mark along.
            for k in range(count):
                cell = [m[k:k + cells] for m in moments]
                keep &= (cell[k + 1] != cell[k]).any(axis=1)
                for later in range(k + 2, count + 1):
                    keep &= (cell[later] == cell[k + 1]).all(axis=1)
            for i in np.nonzero(keep)[0][:8]:
                codes = [moments[-1][int(i) + k].tobytes() for k in range(count)]
                if len(set(codes)) < len(set(characters)):
                    continue
                row = {"address": f"0x{base + int(i) * unit:08X}", "unit": unit,
                       "codes": {ch: code.hex().upper() for ch, code in zip(characters, codes)},
                       "table": [f"{code.hex().upper()}={ch}" for ch, code in dict(zip(characters, codes)).items()]}
                rule = search.kana_rule(characters, codes)
                if rule:
                    row["rule"] = rule
                found.append(row)
        return {"count": len(found), "names": found}

    @tool("What arithmetic ties the game's codes to their characters: give some characters with their codes "
          "(from a line found in memory, or a name entered) and each kind (hiragana, katakana, letters, kanji, "
          "signs) is tried against JIS, Shift-JIS, EUC-JP, Unicode, ASCII and the fifty sounds, counting up or "
          "down, and against the same with a code's top bits left out. Says the rule for each kind, or that "
          "the codes only keep JIS order (a font's own numbering, to be read off its glyphs).",
          codes=("object", "characters to their codes, hex: {\"し\": \"7FE9\", \"レ\": \"7EB4\", \"反\": \"6B96\"}", True))
    def code_rule(self, codes: dict) -> dict:
        try:
            pairs = {str(ch): int(str(code), 16) for ch, code in codes.items()}
        except ValueError:
            raise HarnessError("codes are hex") from None
        if any(len(ch) != 1 for ch in pairs) or len(pairs) < 2:
            raise HarnessError("give two characters or more, each with its code")
        return {"kinds": search.code_rules(pairs)}

    def _vram(self, x: int, y: int, w: int, h: int):
        import numpy as np
        data = self._need().regions().get("vram")
        if data is None or len(data) < 1024 * 512 * 2:
            raise HarnessError("the core gives out no video memory of 1024 by 512 words (PlayStation only)")
        x, y = max(0, min(int(x), 1023)), max(0, min(int(y), 511))
        w, h = max(1, min(int(w), 1024 - x)), max(1, min(int(h), 512 - y))
        return np.frombuffer(data[:1024 * 512 * 2], dtype="<u2").reshape(512, 1024)[y:y + h, x:x + w]

    @staticmethod
    def _texels(words, bits: int):
        import numpy as np
        if bits not in (4, 8):
            raise HarnessError("bits is 4, 8 or 16")
        per = 16 // bits
        return np.stack([(words >> (bits * i)) & ((1 << bits) - 1) for i in range(per)], axis=2).reshape(
            words.shape[0], -1)

    @tool("A rectangle of video memory as a picture (PlayStation: 1024 by 512 words): the frames, and the "
          "textures the game draws from, a glyph cache among them. As colours, or as a texture of 4 or 8 bits "
          "a pixel shown in greys. On a Super Famicom: all of video memory as tiles, 64 across, at 2 bits a "
          "pixel (the text layer's) or 4; x, y, w and h are then in tiles. A fixed font shows there in order, "
          "and so does a message the game draws as a picture; tile n is at column n % 64, row n // 64.",
          x=("integer", "left edge, in words (default 0)", False), y=("integer", "top (default 0)", False),
          w=("integer", "width in words (default all)", False), h=("integer", "height (default all)", False),
          bits=("integer", "16 for colours (default), or 4 or 8: a texture's pixels", False),
          scale=("number", "enlarge or shrink it", False))
    def vram_picture(self, x: int = 0, y: int = 0, w: int = 1024, h: int = 512, bits: int = 16, scale: float = 1.0):
        import numpy as np
        from PIL import Image
        if self._need().platform == "sfc":
            # 64 KB of tiles and tilemaps: drawn as tiles of 8 by 8, 64 across, at 2 or 4 bits a pixel. x, y, w
            # and h are then in tiles of that sheet.
            data = self.machine.regions().get("vram")
            if data is None or len(data) < 0x10000:
                raise HarnessError("the core gives out no video memory")
            bits = 2 if int(bits) not in (2, 4) else int(bits)
            size = 8 * bits
            tiles = np.frombuffer(data[:0x10000 // size * size], dtype=np.uint8).reshape(-1, size)
            # A row of a tile is a byte of each of two planes; a 4-bit tile has two more planes in its second half.
            planes = [np.unpackbits(tiles[:, 16 * (k // 2) + k % 2:16 * (k // 2) + 16:2], axis=1) for k in range(bits)]
            pixels = sum(plane.astype(np.int64) << k for k, plane in enumerate(planes)).reshape(-1, 8, 8)
            rows = len(pixels) // 64
            sheet = pixels[:rows * 64].reshape(rows, 64, 8, 8).transpose(0, 2, 1, 3).reshape(rows * 8, 512)
            x, y = max(0, min(int(x), 63)), max(0, min(int(y), rows - 1))
            w, h = max(1, min(int(w), 64 - x)), max(1, min(int(h), rows - y))
            cut = sheet[y * 8:(y + h) * 8, x * 8:(x + w) * 8]
            image = Image.fromarray((cut * (255 // ((1 << bits) - 1))).astype(np.uint8))
            return ("image", self._png(image, scale))
        words = self._vram(x, y, w, h)
        if int(bits) == 16:
            image = Image.fromarray(np.stack([(words >> s & 31) << 3 for s in (0, 5, 10)], axis=2).astype(np.uint8))
        else:
            pixels = self._texels(words, int(bits))
            top = max(1, int(pixels.max()))
            image = Image.fromarray((pixels * (255 // top)).astype(np.uint8))
        return ("image", self._png(image, scale))

    @tool("A tilemap out of Super Famicom video memory, as numbers: which tile stands in each cell, a row a "
          "line. A window of text kept as tiles shows as a run of numbers that change with the text.",
          offset=("string", "where the map starts in video memory, hex bytes (maps start at multiples of 0x800)", True),
          x=("integer", "first column (default 0)", False), y=("integer", "first row (default 0)", False),
          w=("integer", "columns (default 32)", False), h=("integer", "rows (default 28)", False))
    def vram_tilemap(self, offset: str, x: int = 0, y: int = 0, w: int = 32, h: int = 28) -> dict:
        data = self._need().regions().get("vram")
        if self.machine.platform != "sfc" or data is None:
            raise HarnessError("a Super Famicom's video memory only")
        at = int(offset, 16)
        x, y = max(0, min(int(x), 31)), max(0, min(int(y), 31))
        w, h = max(1, min(int(w), 32 - x)), max(1, min(int(h), 32 - y))
        rows = []
        for row in range(y, y + h):
            cells = struct.unpack_from(f"<{w}H", data, at + 64 * row + 2 * x) if at + 64 * row + 2 * (x + w) <= len(data) else ()
            rows.append(" ".join(f"{c & 0x3FF:03X}" for c in cells))
        return {"offset": hex(at), "tiles": rows, "note": "a cell's low 10 bits; the rest are its palette and flips"}

    @tool("Pairs codes with the pictures the game draws for them, while you play: for a font that isn't in "
          "memory whole. Each frame it looks at where the game keeps the code of the character it draws (or at a "
          "queue of codes still to draw, whose first leaves as it is drawn) and at the buffer the glyph's "
          "picture is in, and keeps the picture of each code the first time. Give steps to play, as `steps` "
          "takes them. Writes a sheet of the glyphs under their codes to scratch (work/<name>.png, and the "
          "rows as hex in work/<name>.json, added to from call to call) and gives the sheet back.",
          picture=("string", "where the glyph being drawn is, hex", True),
          rows=("integer", "its rows", True), row_bytes=("integer", "bytes a row, the high bit first (default 2)", False),
          steps=("array", "what to play meanwhile, as `steps` takes it", True),
          queue=("string", "the front of a queue of codes still to draw, hex: a code is paired when the queue "
                           "has moved up by exactly one", False),
          code=("string", "or the place of the code being drawn, hex: paired when it takes a new value", False),
          size=("integer", "bytes of a code: 1 or 2 (default 2)", False),
          least=("string", "codes below this aren't characters, hex (default 1)", False),
          below=("string", "nor codes from this on, hex (default 1000)", False),
          name=("string", "the sheet's name in scratch (default glyphs)", False),
          prefix=("string", "names the snaps the steps take, as `steps` does (default step)", False))
    def glyph_pairs(self, picture: str, rows: int, steps: list, row_bytes: int = 2, queue: str = "", code: str = "",
                    size: int = 2, least: str = "1", below: str = "1000", name: str = "glyphs", prefix: str = "step"):
        import numpy as np
        from .. import glyphs as sheets
        machine = self._need()
        if bool(queue) == bool(code) or size not in (1, 2) or not 1 <= int(rows) <= 32 or not 1 <= int(row_bytes) <= 4:
            raise HarnessError("give queue or code; size is 1 or 2; rows up to 32 of up to 4 bytes")
        at, low, high = int(queue or code, 16), int(least, 16), int(below, 16)
        where, rows, row_bytes, look = int(picture, 16), int(rows), int(row_bytes), 16
        order = "big" if machine.platform in BIG_ENDIAN else "little"
        path = self.roots.inside("scratch", f"work/{self._name(name)}.json")
        kept = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        state = {"before": None, "new": 0, "drawn": 0}

        def cells(count):
            data = self._read(at, size * count)
            return [int.from_bytes(data[i:i + size], order) for i in range(0, len(data) - size + 1, size)]

        def frame():
            now = cells(look + 1 if queue else 1)
            before, state["before"] = state["before"], now
            if before is None or len(now) != len(before):
                return
            if queue:
                if now[:look] != before[1:look + 1] or now == before:
                    return
                value = before[0]
            else:
                if now[0] == before[0]:
                    return
                value = now[0]
            if not low <= value < high:
                return
            state["drawn"] += 1
            key = f"{value:0{2 * size}X}"
            if key not in kept:
                kept[key] = self._read(where, rows * row_bytes).hex()
                state["new"] += 1
        self.each_frame = frame
        try:
            played = self.steps(steps, prefix=prefix)
        finally:
            self.each_frame = None
        if isinstance(played, tuple):
            played = played[1]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(kept), encoding="utf-8")
        codes = sorted(kept)
        pictures = []
        for key in codes:
            data = bytes.fromhex(kept[key])
            bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8)).reshape(rows, row_bytes * 8)
            pictures.append(bits.astype(np.uint8))
        columns = 16
        image = sheets.sheet(pictures, codes, columns=columns, scale=4) if pictures else None
        out = {"frames": machine.frames, "drawn": state["drawn"], "new": state["new"], "codes": len(kept),
               "sheet": f"work/{self._name(name)}.png", "across": columns,
               "in_order": [" ".join(codes[i:i + columns]) for i in range(0, len(codes), columns)],
               **({"stopped": played["stopped"]} if played.get("stopped") else {})}
        said = [e for e in played.get("events", []) if e.get("kind") == "text"]
        if said:                                # what the loaded script read meanwhile
            out["said"] = [{"frame": e["frame"], "text": e.get("text"),
                            **({"speaker": e["speaker"]} if e.get("speaker") else {})} for e in said]
        if image is None:
            return out
        image.save(path.with_suffix(".png"))
        return ("both", out, self._png(image))

    @tool("Looks in main memory for a picture cut out of video memory: a glyph the game has drawn into a "
          "texture, to find the font it came from. Give the rectangle of one character (a few rows of it are "
          "enough) and how many bits a pixel the texture has. Memory is tried at 1, 2, 4 and 8 bits a pixel, "
          "either end of a byte first, rows any number of bytes apart. Says where, and how the font is packed.",
          x=("integer", "left edge in video memory, in words", True), y=("integer", "top", True),
          w=("integer", "width in words", True), h=("integer", "height", True),
          bits=("integer", "the texture's bits a pixel: 4 (default) or 8", False),
          skip=("integer", "pixels to leave off the left (a character starts inside a word)", False),
          width=("integer", "pixels across to take (default: the rest)", False))
    def find_picture(self, x: int, y: int, w: int, h: int, bits: int = 4, skip: int = 0, width: int = 0) -> dict:
        pixels = self._texels(self._vram(x, y, w, h), int(bits))[:, max(0, int(skip)):]
        if int(width) > 0:
            pixels = pixels[:, :int(width)]
        base, ram = self._need().ram()
        try:
            found = search.picture_places(ram, pixels)
        except search.SearchError as e:
            raise HarnessError(str(e)) from None
        return {"pixels": [int(pixels.shape[1]), int(pixels.shape[0])], "values": sorted(int(v) for v in set(pixels.ravel())),
                "found": [{**f, "at": f"0x{base + f['at']:08X}"} for f in found]}

    def _cursor_cells(self, x_address: str, y_address: str, size: int):
        if x_address and y_address:
            self.cursor_at = (int(x_address, 16), int(y_address, 16), int(size))
        if getattr(self, "cursor_at", None) is None:
            raise HarnessError("where the cursor is kept isn't known yet: call cursor_find, or give x_address and y_address")
        return self.cursor_at

    @tool("Finds where the game keeps a cursor it moves with the pad: moves it right, left, down and up a few "
          "frames each and looks for a place that follows the sideways moves only, and one that follows the "
          "others only. The cursor ends where it began, give or take. The places are remembered for cursor_to.",
          frames=("integer", "frames each way (default 6)", False),
          stick=("boolean", "move with the left stick instead of the pad's arrows", False),
          size=("integer", "bytes of each number, when you know: 1, 2 or 4 (default: 2, then 4, then 1 is tried)", False))
    def cursor_find(self, frames: int = 6, stick: bool = False, size: int = 0) -> dict:
        import numpy as np
        machine = self._need()
        frames, events = max(2, min(int(frames), 60)), []

        def moved(direction):
            held = set() if stick else {direction}
            where = {"right": (1.0, 0.0), "left": (-1.0, 0.0), "down": (0.0, 1.0), "up": (0.0, -1.0)}[direction] if stick \
                else (0.0, 0.0)
            self._advance(frames, held, where, events)
            self._advance(2, set(), (0.0, 0.0), events)
            return machine.ram()
        moved("left")                           # away from the right and bottom edges first: a cursor
        moved("up")                             # against an edge doesn't follow a push towards it
        base, start = machine.ram()
        shots = [start] + [moved(d)[1] for d in ("right", "left", "down", "up")]
        found = {}
        for size in ((int(size),) if int(size) in (1, 2, 4) else (2, 4, 1)):
            kind = {1: "u1", 2: "<i2", 4: "<i4"}[size] if machine.platform not in BIG_ENDIAN else {1: "u1", 2: ">i2", 4: ">i4"}[size]
            cut = len(start) // size * size
            s0, r, l, d, u = (np.frombuffer(x[:cut], dtype=kind).astype(np.int64) for x in shots)
            # A cursor: numbers a screen could have, moved by no more than a screen's worth in those frames.
            small = lambda *a: np.logical_and.reduce([(v >= -64) & (v < 4096) for v in a])            # noqa: E731
            far = 24 * frames
            # It follows the pushes one way only. A push may be stopped by a wall, so one of the two is enough,
            # as long as the other didn't go the wrong way.
            xs = np.nonzero((r >= s0) & (l <= r) & ((r > s0) | (l < r)) & (d == l) & (u == l)
                            & (r - s0 <= far) & (r - l <= far) & small(s0, r, l))[0]
            ys = np.nonzero((r == s0) & (l == s0) & (d >= l) & (u <= d) & ((d > l) | (u < d))
                            & (d - l <= far) & (d - u <= far) & small(s0, d, u))[0]
            if len(xs) and len(ys):
                found = {"size": size, "x": [int(i) * size + base for i in xs[:8]], "y": [int(i) * size + base for i in ys[:8]],
                         "x_moved": {int(i) * size + base: int(max(r[i] - s0[i], r[i] - l[i])) for i in xs[:8]},
                         "y_moved": {int(i) * size + base: int(max(d[i] - l[i], d[i] - u[i])) for i in ys[:8]}}
                break
        if not found:
            raise HarnessError("no place followed the moves: is a cursor on screen, and does the pad move it?")
        # The pair that lie closest together, of those that moved alike: x and y are kept side by side, and a
        # push moves either as far.
        x, y = min(((a, b) for a in found["x"] for b in found["y"]),
                   key=lambda p: (found["x_moved"][p[0]] != found["y_moved"][p[1]], abs(p[0] - p[1])))
        self.cursor_at = (x, y, found["size"])
        return {"x_address": f"0x{x:08X}", "y_address": f"0x{y:08X}", "size": found["size"],
                "x": self._cell(x, found["size"]), "y": self._cell(y, found["size"]),
                "moved": {"right": found["x_moved"][x], "down": found["y_moved"][y], "frames": frames},
                "candidates": {"x": {f"0x{a:08X}": found["x_moved"][a] for a in found["x"]},
                               "y": {f"0x{a:08X}": found["y_moved"][a] for a in found["y"]}},
                "frame": machine.frames}

    @tool("Presses a button when a number in memory comes to a value: for a game that asks for a press in time "
          "(a ring's hand over its mark, a bar that fills, a rhythm). The number is watched a frame at a time, "
          "and the button goes down on the first frame it has come to each value of `at` in turn, from "
          "whichever side it was on. It plays through the pad only. With `tries`, several lists of values "
          "are tried one after the other from this moment, the game put back after each, and what `watch` "
          "held some frames later is given for each: how the right values are found, by what the game does "
          "after a press that was in time and one that wasn't. Without it the presses are made for good.",
          address=("string", "where the number is, hex", True),
          at=("array", "the values to press at, in turn", False),
          tries=("array", "or lists of such values to try, the game put back after each", False),
          kind=("string", "float (default), or u8, u16, u32, s16, s32", False),
          button=("string", "the button (default a)", False),
          hold=("integer", "frames it is held (default 2)", False),
          limit=("integer", "frames at most to wait for each value (default 600)", False),
          after=("integer", "frames to run after the last press (default 30)", False),
          watch=("array", "places to read afterwards, hex, each as the same kind: what tells a press in time from "
                          "one that wasn't", False),
          wrap=("number", "for a number that goes round (an angle: 360): it is then taken as only ever going "
                          "forward, and a value behind where it is now is waited for the next time round", False))
    def press_at(self, address: str, at: list | None = None, tries: list | None = None, kind: str = "float",
                 button: str = "a", hold: int = 2, limit: int = 600, after: int = 30, watch: list | None = None,
                 wrap: float = 0.0) -> dict:
        machine = self._need()
        kinds = {"float": ("f", 4), "u8": ("B", 1), "u16": ("H", 2), "u32": ("I", 4), "s16": ("h", 2), "s32": ("i", 4)}
        if kind not in kinds or bool(at) == bool(tries):
            raise HarnessError("kind is float, u8, u16, u32, s16 or s32; give at, or tries")
        code, size = kinds[kind]
        order = ">" if machine.platform in BIG_ENDIAN else "<"
        place, held = int(address, 16), self._buttons([button])
        seen = [int(str(a), 16) for a in watch or ()]
        hold, limit, after = max(1, min(int(hold), 60)), max(1, min(int(limit), MAX_FRAMES)), max(0, min(int(after), 3600))
        wrap = max(0.0, float(wrap))

        def value(where):
            raw = self._read(where, size)
            return struct.unpack(order + code, raw)[0] if len(raw) == size else None

        def play(values, events):
            """The presses for one list of values: each when the number has come to it. What happened, and why
            it ended early if it did."""
            presses, why = [], None
            for wanted in values:
                wanted = float(wanted)
                before, waited = value(place), 0
                if before is None:
                    raise HarnessError("the number can't be read")
                rising = before < wanted
                # A number that goes round: how far it still has to go forward, which only gets less until
                # it is there (and then jumps up by nearly the whole round).
                left = (wanted - before) % wrap if wrap else None
                while waited < limit and not why:
                    now = value(place)
                    if wrap and now is not None:
                        to_go = (wanted - now) % wrap
                        if to_go > left + wrap / 2 or to_go == 0:
                            break
                        left = to_go
                    elif now is not None and (now >= wanted if rising else now <= wanted):
                        break
                    _, why = self._advance(1, set(), (0.0, 0.0), events)
                    waited += 1
                if why or waited >= limit:
                    presses.append({"at": wanted, "pressed": False, "waited": waited})
                    break
                presses.append({"at": wanted, "pressed": True, "frame": machine.frames, "value": round(value(place), 4)})
                _, why = self._advance(hold, held, (0.0, 0.0), events)
                if why:
                    break
            if not why:
                _, why = self._advance(after, set(), (0.0, 0.0), events)
            return presses, why
        show = lambda v: None if v is None else round(v, 4)                 # noqa: E731
        if at:
            events = []
            presses, why = play(at, events)
            return {"presses": presses, "value": show(value(place)), "frames": machine.frames,
                    **({"watch": [show(value(w)) for w in seen]} if seen else {}), **({"stopped": why} if why else {}),
                    **({"events": events} if self.reader is not None and events else {})}
        if len(tries) > 64 or not all(isinstance(t, list) and t for t in tries):
            raise HarnessError("tries is up to 64 lists of values")
        # Tried off the record, as explore tries its pushes: the game is put back, and nothing of it happened.
        start, frames, way, rows = machine.save_state(), machine.frames, json.dumps(self.route), []
        reader, self.reader = self.reader, None
        try:
            for values in tries:
                machine.load_state(start)
                machine.frames = frames
                presses, why = play(values, [])
                rows.append({"at": [float(v) for v in values], "pressed": sum(1 for p in presses if p["pressed"]),
                             "value": show(value(place)), **({"watch": [show(value(w)) for w in seen]} if seen else {}),
                             **({"stopped": why} if why else {})})
                if why == "time":
                    break
        finally:
            machine.load_state(start)
            machine.frames, self.route, self.reader = frames, json.loads(way), reader
        return {"tried": len(rows), "rows": rows, "frames": machine.frames}

    @tool("Whole numbers that only ever go up over snaps taken a frame apart while a line types, by a little at "
          "a time, and stand still in snaps taken after: a pointer walking through a script, a count of "
          "characters drawn. For a game whose text no search finds: it may draw from its cartridge, or from "
          "a script it never copies, and then such a pointer is all that says what is on screen. Read what "
          "it points at (`read` with `mixed`, in the cartridge too) to see the line.",
          snaps=("array", "snaps in order through the typing (`steps` with `every`: 1), six or more", True),
          rest=("array", "snaps from after the typing, where it must hold what it held at the last of `snaps`", False),
          unit=("integer", "bytes of the number: 2 (default), 1 or 4. A number may start at any address: "
                           "every offset is looked at, not only those a multiple of the unit", False),
          most=("integer", "the most it may go up from one snap to the next (default 4: a character is one "
                           "or two codes, and a mark or two may come between)", False),
          within=("array", "look only between two addresses, hex", False),
          least=("integer", "the fewest snaps it must have moved in (default: half of them). 3 or so for a game "
                            "that types a character every few frames, or whose pointer the snaps caught seldom", False))
    def advancing(self, snaps: list, rest: list | None = None, unit: int = 2, most: int = 4,
                  within: list | None = None, least: int = 0) -> dict:
        if unit not in (1, 2, 4) or len(snaps or ()) < 6:
            raise HarnessError("six snaps or more; unit is 1, 2 or 4")
        names = list(snaps) + list(rest or ())
        if len(names) > ADVANCING_MOST_SNAPS:
            raise HarnessError(f"{len(names)} snaps: advancing takes {ADVANCING_MOST_SNAPS} at most. Every "
                               "second or third frame of the typing is enough (`every`: 2)")
        # A number may start at any address (a Game Boy keeps words at odd ones), so the snaps are read once for
        # each offset into the unit: a pointer at an odd address isn't missed.
        found = []
        for shift in range(unit):
            found += self._advancing(self._each_memory(names), len(snaps), unit, most, within, least, shift)
        # A console's other memory, kept with each snap as a region (a Game Boy Advance's 32 KB in the CPU,
        # where its games keep their own state): looked through the same way.
        from .machine import region_starts
        from ..run import _load
        for region, start in region_starts(getattr(self.machine, "platform", "")).items():
            files = [self.roots.inside("scratch", f"snaps/{self._name(n)}.{region}.gz") for n in names]
            if all(f.is_file() for f in files):
                for shift in range(unit):
                    found += self._advancing(((None, start, _load(f)) for f in files), len(snaps), unit, most,
                                             within, least, shift)
        found.sort(key=lambda place: -place["moved_in"])
        out = {"count": len(found), "snaps": len(snaps), "rest": len(rest or ()), "places": found[:MAX_HITS]}
        if not found:
            out["nothing_advances"] = (
                "No number walks while this line types. If the snaps do span the typing (and unit 1 and 4 give "
                "nothing either; and `least` 3, for a game that types a character every few frames), the game "
                "doesn't walk a script: it may put a window down whole, as tiles. Look "
                "for the window itself: find_relative with a row as the screen has it, its spaces as plain spaces, "
                "and spaces: true (unit 2 as well); a place that fits is the tile map or the list of writes it "
                "is filled from. Then `read` the words before the row for where and how many, and `changed` "
                "over snaps a window apart for the list's start.")
        return out

    def _advancing(self, memories, typed: int, unit: int, most: int, within: list | None, least: int,
                   shift: int = 0) -> list[dict]:
        """advancing over one stretch of memory at so many moments: (name, base, bytes) each, the first `typed`
        of them while the line types, the numbers taken from `shift` bytes in. The moments come one at a time and only what the walk needs is kept (the
        first, last and final values, two counts a number), so the memory it takes doesn't grow with the snaps;
        the steps are worked out a slice at a time. A stretch too big even for that is refused before any work."""
        import numpy as np
        order = ">" if self.machine is not None and self.machine.platform in BIG_ENDIAN else "<"
        kind = np.dtype(f"{order}u{unit}")
        most = max(1, int(most))
        base = size = lo = hi = None
        first = last = final = rose = away = ok = None
        count = 0
        for at, (_, where, ram) in enumerate(memories):
            if shift:
                where, ram = where + shift, memoryview(ram)[shift:]
            if base is None:
                need = advancing_bytes(len(ram), unit)
                if need > ADVANCING_MOST_BYTES:
                    raise HarnessError(
                        f"advancing over {len(ram) >> 20} MB a snap would hold about {need >> 20} MB at once, "
                        f"more than the {ADVANCING_MOST_BYTES >> 20} MB it may: give `within` a stretch of "
                        "memory, or a larger `unit`")
                base, size = where, len(ram) // unit
                if within is not None:
                    low, high = (int(str(v), 16) for v in within)
                    lo = max(0, min(size, -(-(low - base) // unit)))        # numbers that start inside
                    hi = max(lo, min(size, -(-(high - base) // unit)))
                else:
                    lo, hi = 0, size
                first = np.frombuffer(ram, dtype=kind, count=size)[lo:hi].copy()
                last = first.copy()
                rose = np.zeros(hi - lo, dtype=np.uint16)
                away = np.zeros(hi - lo, dtype=np.uint16)
                count = 1
                if typed == 1:
                    final = first
                continue
            if where != base or len(ram) // unit != size:
                raise HarnessError("the snaps don't hold the same stretch of memory: take them all from one "
                                   "game and one load")
            now = np.frombuffer(ram, dtype=kind, count=size)[lo:hi]
            if at < typed:
                # A snap may catch the number away for a moment (the game reading a word kept elsewhere): a
                # snap that isn't a small rise from the last good one is counted, and the walk goes on from there.
                for s in range(0, hi - lo, ADVANCING_SLICE):
                    e = s + ADVANCING_SLICE
                    step = now[s:e].astype(np.int64) - last[s:e].astype(np.int64)
                    good = (step >= 0) & (step <= most)
                    rose[s:e] += (good & (step > 0)).astype(np.uint16)
                    away[s:e] += (~good).astype(np.uint16)
                    last[s:e] = np.where(good, now[s:e], last[s:e])
                count += 1
                if at == typed - 1:
                    final = now.copy()
                    ok = (away <= max(1, typed // 6)) & (final == last)      # away a snap or two in a dozen
                    # It moved on most frames: not a flag, not a clock's high part. (`least`: or on so many.)
                    ok &= rose >= (max(1, int(least)) if least else (typed - 1) // 2)
            else:
                ok &= now == final
        if base is None or count < typed or ok is None:
            return []
        places = np.nonzero(ok)[0]
        places = sorted(places, key=lambda i: -int(rose[i]))[:MAX_HITS]
        return [{"address": f"0x{base + (lo + int(i)) * unit:08X}", "from": f"0x{int(first[i]):0{2 * unit}X}",
                 "to": f"0x{int(final[i]):0{2 * unit}X}", "moved_in": int(rose[i])} for i in places]

    @tool("Numbers that go up or down by the same amount from each snap to the next, over snaps taken evenly "
          "apart: a hand going round, a bar filling, a timer. What press_at wants to watch. Read as 32-bit "
          "floats, or as whole numbers of 1, 2 or 4 bytes.",
          snaps=("array", "three snaps or more, in order, taken the same number of frames apart", True),
          kind=("string", "float (default), or u8, u16, u32", False),
          least=("number", "the least it must move from one snap to the next (default 0.001 for floats, 1)", False),
          within=("array", "look only between two addresses, hex", False))
    def steady(self, snaps: list, kind: str = "float", least: float = 0.0, within: list | None = None) -> dict:
        import numpy as np
        kinds = {"float": "f4", "u8": "u1", "u16": "u2", "u32": "u4"}
        if kind not in kinds or len(snaps or ()) < 3:
            raise HarnessError("three snaps or more; kind is float, u8, u16 or u32")
        order = ">" if self.machine is not None and self.machine.platform in BIG_ENDIAN else "<"
        size = int(kinds[kind][1])
        least = float(least) or (0.001 if kind == "float" else 1.0)
        moments, base = [], 0
        for _, base, ram in self._memories(snaps):
            moments.append(np.frombuffer(ram[:len(ram) // size * size], dtype=order + kinds[kind]).astype(np.float64))
        with np.errstate(invalid="ignore", over="ignore"):
            first = moments[1] - moments[0]
            ok = np.isfinite(first) & (np.abs(first) >= least) & (np.abs(moments[0]) < 1e9)
            for before, after in zip(moments[1:], moments[2:]):
                step = after - before
                ok &= np.isfinite(step) & (np.abs(step - first) <= 0.02 * np.abs(first) + 1e-6)
        places = np.nonzero(ok)[0]
        if within is not None:
            low, high = (int(str(v), 16) for v in within)
            places = places[(places * size + base >= low) & (places * size + base < high)]
        return {"count": int(len(places)), "snaps": len(moments),
                "places": [{"address": f"0x{base + int(i) * size:08X}", "from": round(float(moments[0][i]), 4),
                            "to": round(float(moments[-1][i]), 4), "step": round(float(first[i]), 4)}
                           for i in places[:MAX_HITS]]}

    @tool("Finds where a game of three dimensions keeps the place of the character the pad moves: numbers with "
          "a point (32-bit floats), where cursor_find looks for whole ones. It stands still twice, then pushes "
          "right, left, down and up, and gives the floats that stood while it stood, moved with a push and "
          "came back with the push the other way, grouped where they lie three on end (a place is kept as "
          "x, y, z; the one of the three that doesn't move is the height). The camera may be turned, so a push "
          "right may move both of the other two. Give two of a group's addresses to explore with `grid`. "
          "A console without floats (PlayStation, Super Famicom) keeps a place as whole numbers: `kind` s16 "
          "or s32 looks for those the same way, and explore then wants `whole` with `grid` and the `size`.",
          frames=("integer", "frames each way (default 20)", False),
          stick=("boolean", "move with the left stick instead of the pad's arrows", False),
          least=("number", "how far a push must move a number to count (default 1.0)", False),
          kind=("string", "float, s16 or s32 (default: s16 on ps1 and sfc, float elsewhere)", False))
    def place_find(self, frames: int = 20, stick: bool = False, least: float = 1.0, kind: str = "") -> dict:
        import numpy as np
        machine = self._need()
        frames, events, least = max(2, min(int(frames), 120)), [], max(1e-6, float(least))
        kind = kind or ("s16" if machine.platform in ("ps1", "sfc") else "float")
        if kind not in ("float", "s16", "s32"):
            raise HarnessError("kind is float, s16 or s32")
        unit = 2 if kind == "s16" else 4
        kind = (">" if machine.platform in BIG_ENDIAN else "<") + {"float": "f4", "s16": "i2", "s32": "i4"}[kind]

        def after(direction):
            held = set() if stick or direction is None else {direction}
            where = {"right": (1.0, 0.0), "left": (-1.0, 0.0), "down": (0.0, 1.0), "up": (0.0, -1.0)}[direction] \
                if stick and direction else (0.0, 0.0)
            self._advance(frames, held, where, events)
            self._advance(4, set(), (0.0, 0.0), events)
            base, ram = machine.ram()
            return base, np.frombuffer(ram[:len(ram) // unit * unit], dtype=kind).astype(np.float64)
        base, still = after(None)
        _, s0 = after(None)
        r, l, d, u = (after(way)[1] for way in ("right", "left", "down", "up"))
        with np.errstate(invalid="ignore", over="ignore"):
            sane = np.isfinite(s0) & np.isfinite(r) & np.isfinite(l) & np.isfinite(d) & np.isfinite(u) \
                & (np.abs(s0) < 1e6) & (np.abs(r) < 1e6) & (np.abs(d) < 1e6) & (still == s0)
            if kind[1] != "f":
                # A whole number that leaps isn't a place: a walk is a few units a frame at most.
                sane &= (np.abs(r - s0) <= 64 * frames) & (np.abs(d - l) <= 64 * frames) & (np.abs(u - s0) <= 64 * frames)
            # A push moved it, and the push the other way took it back at least half the way.
            across = (np.abs(r - s0) >= least) & (np.abs(l - s0) <= 0.5 * np.abs(r - s0) + least)
            along = (np.abs(d - l) >= least) & (np.abs(u - l) <= 0.5 * np.abs(d - l) + least)
            moved = np.nonzero(sane & (across | along))[0]
        show = lambda i: {"address": f"0x{base + unit * int(i):08X}", "value": round(float(s0[i]), 3),    # noqa: E731
                          "right": round(float(r[i] - s0[i]), 3), "down": round(float(d[i] - l[i]), 3)}
        chosen, groups = set(int(i) for i in moved), []
        # x, y, z on end: two of the three moved. Whole numbers of two bytes may lie a word apart instead.
        shapes = [(first, apart) for apart in ((1,) if unit == 4 else (1, 2)) for first in (-2, -1, 0)]
        for i in sorted(chosen):
            for back, apart in shapes:
                first = i + back * apart
                three = [first, first + apart, first + 2 * apart]
                if first >= 0 and three[2] < len(s0) and sum(1 for k in three if k in chosen) >= 2 \
                        and not any(abs(g["at"] - three[0]) < 3 for g in groups) and all(bool(sane[k]) for k in three):
                    groups.append({"at": three[0], "places": [show(k) for k in three],
                                   "moved": round(float(sum(abs(r[k] - s0[k]) + abs(d[k] - l[k]) for k in three)), 3)})
                    break
        # A place is two numbers that both pushes moved and a height that neither did: such a group first.
        flat = lambda g: sorted(bool(q["right"] or q["down"]) for q in g["places"]) == [False, True, True]   # noqa: E731
        groups.sort(key=lambda g: (not flat(g), -g["moved"]))
        if not groups and not len(moved):
            raise HarnessError("no number followed the moves: does the pad move the character here?")
        return {"frames": frames, "floats_that_followed" if kind[1] == "f" else "numbers_that_followed": int(len(moved)),
                "groups": [{"places": g["places"]} for g in groups[:12]],
                "alone": [show(i) for i in moved[:12]] if not groups else [], "frame": machine.frames}

    @tool("Walks the character of a game of three dimensions to a place on the ground, with the left stick, "
          "correcting as it goes (walk_to is the same for a map of whole numbers and the arrows). Give the two of place_find's floats that the pushes move (its group's first and "
          "third, often: x and z; the height is the one that stood). It pushes the stick right, then down, to "
          "learn which way each moves the two numbers (the camera may be turned, so a push moves both), then "
          "pushes toward the place a few frames at a time, learning the way again from its own last pushes as "
          "it goes (a camera that turns behind the character is followed). It stops when the character is "
          "near enough, when a push no longer moves it (a wall, a fence, a talk that has begun), or at the "
          "limit, and says which, with where it got to. Without x and z, only says where the character is.",
          x_address=("string", "where the game keeps the first of the two, hex", True),
          z_address=("string", "and the second, hex", True),
          x=("number", "where to, in the first", False), z=("number", "where to, in the second", False),
          near=("number", "how near is near enough, in the game's units (default: what a 4-frame push "
                          "moves the character)", False),
          push=("integer", "frames of each push toward the place (default 8)", False),
          limit=("integer", "at most this many frames (default 1200)", False))
    def walk_3d(self, x_address: str, z_address: str, x: float | None = None, z: float | None = None,
                near: float = 0.0, push: int = 8, limit: int = 1200) -> dict:
        import math
        import struct
        machine = self._need()
        order = ">f" if machine.platform in BIG_ENDIAN else "<f"
        try:
            ax, az = int(str(x_address), 16), int(str(z_address), 16)
        except ValueError:
            raise HarnessError("x_address and z_address are hex addresses, as place_find gives them") from None

        def where():
            bx, bz = machine.read(ax, 4), machine.read(az, 4)
            if len(bx) < 4 or len(bz) < 4:
                raise HarnessError("the addresses can't be read")
            px, pz = struct.unpack(order, bx)[0], struct.unpack(order, bz)[0]
            if not (math.isfinite(px) and math.isfinite(pz)):
                raise HarnessError("the addresses don't hold numbers with a point")
            return px, pz
        events, ran = [], 0
        start = where()
        if x is None or z is None:
            return {"at": [round(start[0], 3), round(start[1], 3)], "frame": machine.frames}
        push, limit = max(2, min(int(push), 60)), max(push, min(int(limit), MAX_FRAMES))
        target = (float(x), float(z))

        def stick(s, frames):
            nonlocal ran
            before = where()
            done, why = self._advance(frames, set(), s, events)
            ran += done
            after = where()
            return (after[0] - before[0], after[1] - before[1]), why
        # The way each stick axis moves the two numbers, per frame: two pushes to learn it.
        learn = max(4, push)
        seen = []                                           # (stick, move per frame) of the pushes made
        for s in ((1.0, 0.0), (0.0, 1.0)):
            move, why = stick(s, learn)
            if why == "time":
                return {"reached": False, "stopped": "time", "at": list(map(lambda v: round(v, 3), where())),
                        "frames": ran}
            seen.append((s, (move[0] / learn, move[1] / learn)))
        speed = max(math.hypot(*m) for _, m in seen)
        if speed < 1e-9:
            return {"reached": False, "stopped": "the stick doesn't move the two numbers: are they the place, and "
                                                 "can the character move now?", "at": list(where()), "frames": ran}
        near = float(near) if near and near > 0 else 4 * speed

        def way():
            """The 2x2 that takes a stick to a move per frame, fitted to the last pushes (least squares)."""
            rows = seen[-6:]
            sxx = sum(s[0] * s[0] for s, _ in rows); sxy = sum(s[0] * s[1] for s, _ in rows)
            syy = sum(s[1] * s[1] for s, _ in rows)
            det = sxx * syy - sxy * sxy
            if abs(det) < 1e-9:
                return None
            out = []
            for k in (0, 1):                                # each of the two numbers
                bx = sum(s[0] * m[k] for s, m in rows); by = sum(s[1] * m[k] for s, m in rows)
                out.append(((syy * bx - sxy * by) / det, (sxx * by - sxy * bx) / det))
            return out                                      # move[k] = out[k][0] * sx + out[k][1] * sy
        best, stuck, why = math.inf, 0, None
        while ran < limit:
            px, pz = where()
            gap = (target[0] - px, target[1] - pz)
            dist = math.hypot(*gap)
            if dist <= near:
                return {"reached": True, "at": [round(px, 3), round(pz, 3)], "frames": ran,
                        "events": len(events)}
            m = way()
            if m is None:
                why = "the pushes don't tell the way apart"
                break
            det = m[0][0] * m[1][1] - m[0][1] * m[1][0]
            if abs(det) < 1e-12:
                why = "the stick moves the two numbers along one line only: they aren't a place on the ground"
                break
            # The stick that would get there: its length is the frames it takes at full tilt. Pushed at full
            # tilt toward the place, a whole push, or fewer frames when it is nearer than that.
            sx = (m[1][1] * gap[0] - m[0][1] * gap[1]) / det
            sy = (-m[1][0] * gap[0] + m[0][0] * gap[1]) / det
            length = math.hypot(sx, sy)
            if not length:
                why = "no way toward the place"
                break
            frames = push if length > push else max(2, int(round(length)))
            sx, sy = sx / length, sy / length
            move, why = stick((sx, sy), frames)
            if why:
                break
            # Learnt from only when it moved: a push against a wall says nothing of the way, and would turn it.
            if math.hypot(*move) >= 0.25 * speed * frames:
                seen.append(((sx, sy), (move[0] / frames, move[1] / frames)))
            if dist < best - 0.25 * speed * frames:
                best, stuck = dist, 0
            else:
                stuck += 1
                if stuck >= 6:
                    why = "it stopped getting nearer: something is in the way, or a talk has begun"
                    break
        px, pz = where()
        return {"reached": math.hypot(target[0] - px, target[1] - pz) <= near, "stopped": why or "limit",
                "at": [round(px, 3), round(pz, 3)], "frames": ran, "events": len(events)}

    @tool("Moves the cursor to a place, in the game's own numbers for it (cursor_find says where they are kept "
          "and how far a few frames move it; the picture says what the numbers mean): holds the pad a frame "
          "at a time until the cursor is there, or as near as it gets, letting go between pushes when it is "
          "close. Without x and y, only says where it is.",
          x=("integer", "where to, across", False), y=("integer", "where to, down", False),
          x_address=("string", "where the game keeps the cursor's x, hex (default: as cursor_find found)", False),
          y_address=("string", "and its y", False), size=("integer", "bytes of each: 1, 2 or 4 (default 2)", False),
          near=("integer", "how near is near enough (default 1)", False),
          limit=("integer", "at most this many frames (default 600)", False),
          if_stuck=("string", "a button to press when the cursor won't move (a menu or a bubble is open): "
                              "pressed once, then again if it still won't, e.g. b", False))
    def cursor_to(self, x: int | None = None, y: int | None = None, x_address: str = "", y_address: str = "",
                  size: int = 2, near: int = 1, limit: int = 600, if_stuck: str = "") -> dict:
        machine = self._need()
        at_x, at_y, size = self._cursor_cells(x_address, y_address, size)

        def now():
            def signed(value):
                return value - (1 << 8 * size) if value is not None and size > 1 and value >> (8 * size - 1) else value
            return signed(self._cell(at_x, size)), signed(self._cell(at_y, size))
        events, ran, why = [], 0, None
        unstick, still, pressed, last = self._buttons([if_stuck]) if if_stuck else set(), 0, 0, None
        if x is not None and y is not None:
            near, limit = max(0, int(near)), max(1, min(int(limit), MAX_FRAMES))
            while ran < limit and not why:
                cx, cy = now()
                if cx is None or cy is None:
                    raise HarnessError("the cursor's places can't be read")
                still = still + 1 if (cx, cy) == last else 0
                last = (cx, cy)
                if still >= 24 and unstick and pressed < 2:
                    _, why = self._advance(3, unstick, (0.0, 0.0), events)
                    if not why:
                        _, why = self._advance(30, set(), (0.0, 0.0), events)
                    ran, still, pressed = ran + 33, 0, pressed + 1
                    continue
                held = set()
                if abs(cx - x) > near:
                    held.add("right" if cx < x else "left")
                if abs(cy - y) > near:
                    held.add("down" if cy < y else "up")
                if not held:
                    break
                _, why = self._advance(1, held, (0.0, 0.0), events)
                ran += 1
                # Near the place, a frame's push and then let go: a cursor may gather speed while held, and
                # run on after.
                if not why and max(abs(cx - x), abs(cy - y)) <= 16:
                    _, why = self._advance(2, set(), (0.0, 0.0), events)
                    ran += 2
            self._advance(2, set(), (0.0, 0.0), events)
        cx, cy = now()
        return {"x": cx, "y": cy, "frames": machine.frames, **({"ran": ran} if ran else {}),
                **({"stopped": why} if why else {}), **({"pressed": pressed} if pressed else {}),
                **({"reached": abs(cx - x) <= max(0, int(near)) and abs(cy - y) <= max(0, int(near))} if x is not None and y is not None else {}),
                **({"events": events} if self.reader is not None and events else {})}

    @tool("Walks a character on a map to a place, in the game's own numbers for where it stands (cursor_find "
          "finds them as it does a cursor's: give it 20 frames or so each way, since a step takes time). One "
          "way at a time, as such games move: across first, then up or down, and the other way round when "
          "something is in the way. Stops at the place, or where it can get no nearer, and says which. Stops "
          "too when the map changes (a door, stairs): the numbers start anew on the next map, so a place asked "
          "for on this one means nothing there. It then waits for the new map's picture and says where the "
          "character stands on it.",
          x=("integer", "where to, across", True), y=("integer", "where to, down", True),
          x_address=("string", "where the game keeps the place across, hex (default: as cursor_find found)", False),
          y_address=("string", "and down", False), size=("integer", "bytes of each: 1, 2 or 4 (default 2)", False),
          limit=("integer", "at most this many frames (default 1200)", False),
          patience=("integer", "frames without getting nearer before the other way is tried (default 40)", False),
          jump=("integer", "a move of this much or more in one frame is a change of map (default 8; 0: never)", False),
          map_address=("string", "or where the game keeps which map it is, hex, one byte: a change there is a "
                                 "change of map", False),
          linger=("integer", "frames to stand at the place and see whether it is a door or stairs, which may "
                             "take a moment to act (default 45; 0: none)", False),
          near=("integer", "how near is near enough, each way (default 0: the very place). For numbers finer "
                           "than a step, where a walk may never land on the place itself", False))
    def walk_to(self, x: int, y: int, x_address: str = "", y_address: str = "", size: int = 2, limit: int = 1200,
                patience: int = 40, jump: int = 8, map_address: str = "", linger: int = 45, near: int = 0) -> dict:
        machine = self._need()
        at_x, at_y, size = self._cursor_cells(x_address, y_address, size)

        def now():
            def signed(value):
                return value - (1 << 8 * size) if value is not None and size > 1 and value >> (8 * size - 1) else value
            return signed(self._cell(at_x, size)), signed(self._cell(at_y, size))
        events, ran, why, across, turns = [], 0, None, True, 0
        limit, patience = max(1, min(int(limit), MAX_FRAMES)), max(4, int(patience))
        best, since, last, new_map, near = None, 0, None, None, max(0, int(near))

        def there(cx, cy):
            return abs(cx - x) <= near and abs(cy - y) <= near
        jump, map_at = max(0, int(jump)), int(map_address, 16) if map_address else None
        which = self._cell(map_at, 1) if map_at is not None else None
        stood = 0
        while ran < limit and not why:
            cx, cy = now()
            if cx is None or cy is None:
                raise HarnessError("the place can't be read")
            # Another map: its numbers are its own, so the walk ends here, wherever it was going.
            if (map_at is not None and self._cell(map_at, 1) != which) or \
                    (jump and last and max(abs(cx - last[0]), abs(cy - last[1])) >= jump):
                new_map = {"from": list(last or (cx, cy))}
                break
            last = (cx, cy)
            if there(cx, cy):
                # There: stairs take a moment to act, so it stands a while with the pad let go.
                if stood >= max(0, int(linger)) or not (jump or map_at is not None):
                    break
                _, why = self._advance(1, set(), (0.0, 0.0), events)
                ran, stood = ran + 1, stood + 1
                continue
            far = abs(cx - x) + abs(cy - y)
            if best is None or far < best:
                best, since = far, 0
            else:
                since += 1
            if since >= patience:                       # no nearer for a while: the other way, a few times
                across, since, turns = not across, 0, turns + 1
                if turns > 6:
                    break
            want_x = "right" if cx < x - near else "left" if cx > x + near else None
            want_y = "down" if cy < y - near else "up" if cy > y + near else None
            held = (want_x or want_y) if across else (want_y or want_x)
            _, why = self._advance(1, {held}, (0.0, 0.0), events)
            ran += 1
        self._advance(2, set(), (0.0, 0.0), events)
        if new_map and not why:
            # The pad let go, until the new map stands still and shows (it fades in from one colour, and the
            # place may settle meanwhile).
            still, before = 0, now()
            for _ in range(360):
                _, why = self._advance(1, set(), (0.0, 0.0), events)
                ran += 1
                still, before = (still + 1, before) if now() == before else (0, now())
                frame = machine.picture()
                if why or (still >= 40 and (frame is None or int(frame.max()) - int(frame.min()) > 64)):
                    break
        cx, cy = now()
        if new_map:
            return {"x": cx, "y": cy, "frames": machine.frames, "ran": ran, "reached": False, "map_changed": True,
                    **new_map, **({"map": self._cell(map_at, 1)} if map_at is not None else {}),
                    **({"stopped": why} if why else {}), **({"turned": turns} if turns else {}),
                    **({"events": events} if self.reader is not None and events else {})}
        return {"x": cx, "y": cy, "frames": machine.frames, "ran": ran, "reached": there(cx, cy),
                **({"stopped": why} if why else {}), **({"turned": turns} if turns else {}),
                **({"events": events} if self.reader is not None and events else {})}

    def _fight_through(self, fight: dict):
        """For explore: a function that, after a step, waits up to `begin` frames for a fight's mode and presses
        through it as advance_through does (no stuck check: a menu that holds the fight runs to `most`). A mode
        that comes and goes within `holds` frames (a screen's fade on a room change), or comes without `also` (a
        second mode, the same keys), is no fight: the frames it took are kept and nothing is pressed. It gives
        the moves made, as [frames, buttons], and the fight's outcome, or None when no fight came."""
        machine = self.machine
        if not isinstance(fight, dict) or not fight.get("address"):
            raise HarnessError("fight is advance_through's mode: address, and equals or differs")

        def condition(spec: dict, what: str):
            size = int(spec.get("size", 1))
            equals, differs = str(spec.get("equals", "")), str(spec.get("differs", ""))
            if size not in (1, 2, 4) or (equals == "") == (differs == "") or not spec.get("address"):
                raise HarnessError(f"{what}: address, size 1, 2 or 4, and equals or differs, one of them")
            at, want = int(str(spec["address"]), 16), int(equals or differs, 0)

            def holds_now() -> bool:
                value = self._cell(at, size)
                return value is not None and (value == want if equals != "" else value != want)
            return holds_now
        mode = condition(fight, "fight")
        also = fight.get("also")
        if also is not None and not isinstance(also, dict):
            raise HarnessError("fight's also is a second mode: address, and equals or differs")
        second = condition(also, "fight's also") if also is not None else None
        keys = sorted(self._buttons(fight.get("buttons", ["a"])))
        hold, wait = max(1, int(fight.get("hold", 6))), max(0, int(fight.get("wait", 30)))
        most, begin = max(1, min(int(fight.get("most", 300)), MAX_FRAMES // 8)), max(0, int(fight.get("begin", 0)))
        holds = max(0, min(int(fight.get("holds", 0)), 600))

        def through():
            moves, waited = [], 0
            while waited < begin and not mode():
                machine.run(1, set(), (0.0, 0.0))
                waited += 1
            if waited:
                moves.append([waited, []])
            if not mode():
                return moves, None
            # A fight holds its mode; a fade sets it for a few frames and drops it. Watched frame by frame, and
            # the frames kept in the moves whatever they show.
            stood = 0
            while stood < holds and mode():
                machine.run(1, set(), (0.0, 0.0))
                stood += 1
            if stood:
                moves.append([stood, []])
            if not mode() or (second is not None and not second()):
                return moves, None
            presses = 0
            while mode() and presses < most:
                machine.run(hold, set(keys), (0.0, 0.0))
                machine.run(wait, set(), (0.0, 0.0))
                moves += [[hold, keys], [wait, []]]
                presses += 1
            return moves, {"presses": presses, "stopped": "mode ended" if not mode() else "most"}
        return through

    @tool("Finds where the character can walk by trying it: from where it stands it pushes each way for a few "
          "frames, goes back, and does the same from every place it got to, until nothing new comes or `most` "
          "places are known. For a map walk_to can't cross (ledges, fences, a maze) or one too dark to read. "
          "Gives the places as rows of a map in the game's own numbers (# reached, @ where it stands, D where "
          "the next push changed the map: a door, stairs) with a `ruler` to hold over it (a mark at every fifth "
          "column and its number; the first row is `rows`[0]), and the game is left where it was. Given `to`, the "
          "game is then put at that place as it was reached while trying (the pushes go into the record of the "
          "way); not reached, it is put at the place it got to nearest the one asked for, and says where it "
          "stopped and why (`reached` false, `stopped_at`), so the next call goes on from there. The trying ends "
          "as soon as the place is reached, and tries the places nearest to it first, so a walk is quick even "
          "far off; the map given is only what was tried till then. A place kept in pixels (a push moves it "
          "several) makes every pixel a place, and the call's time runs out: give `grid` and `whole` (a `hint` "
          "says so).",
          x_address=("string", "where the game keeps the place across, hex (default: as cursor_find found)", False),
          y_address=("string", "and down", False), size=("integer", "bytes of each: 1, 2 or 4 (default 2)", False),
          step=("integer", "frames a push is held (default 8): about one of the game's numbers a push is best", False),
          most=("integer", "places at most (default 400, 1500 at most)", False),
          to=("array", "a place [x, y] to walk to by the way found", False),
          near=("integer", "how near to it is near enough, each way (default 1)", False),
          jump=("integer", "a move of this much or more in one push is a change of map (default 8)", False),
          within=("array", "look no further than [x0, y0, x1, y1]", False),
          diagonals=("boolean", "push the four slanting ways too: for a passage that runs aslant of the pad's "
                                "ways and is narrower than a push (a camera turned, as in most games of three "
                                "dimensions)", False),
          whole=("boolean", "with grid: the place is whole numbers of `size` bytes, not floats: pixels, or any "
                            "place a push moves by many (a console without floats)", False),
          grid=("number", "count in cells of this many of the game's units, so that places a hair apart are one: "
                          "with whole, the pixels a push moves (16, 32) for a place kept in pixels; without, "
                          "for a place kept as numbers with a point (place_find), read as 32-bit floats. About "
                          "what one push moves the character is best; jump and to are then in cells", False),
          close=("string", "a button that closes a window (a, b): pressed once first, and the press kept, when "
                           "no push moves the character from where it stands, not even a longer one (a message "
                           "open takes the pad). Without it such a start is only said (`still`)", False),
          fight=("object", "where walking brings fights at random: the fight's mode as advance_through takes it "
                           "(address, size, equals or differs, buttons, hold, wait, most) and `begin`, frames "
                           "after a step for the fight to begin (0: the mode is set at once). A fight a push "
                           "starts is pressed through there and the map goes on from where it ended; the "
                           "fights are counted and listed, and a walk with `to` keeps their presses in the "
                           "record of the way. `holds`: frames the mode must stand before it is a fight (a "
                           "screen's fade on a room change may set it for a few); `also`: a second mode, the "
                           "same keys, that must hold with it", False),
          room=("string", "where the game keeps which room it is in, hex: a push that changes it is a door, "
                          "however far the place moved (a game whose rooms share their numbers, or whose door "
                          "moves the place less than jump)", False),
          room_size=("integer", "bytes of room: 1, 2 or 4 (default 1)", False))
    def explore(self, x_address: str = "", y_address: str = "", size: int = 2, step: int = 8, most: int = 400,
                to: list | None = None, near: int = 1, jump: int = 8, within: list | None = None,
                grid: float = 0.0, diagonals: bool = False, whole: bool = False, close: str = "",
                fight: dict | None = None, room: str = "", room_size: int = 1) -> dict:
        machine = self._need()
        at_x, at_y, size = self._cursor_cells(x_address, y_address, size)
        if room_size not in (1, 2, 4):
            raise HarnessError("room_size is 1, 2 or 4 bytes")
        room_at = int(str(room), 16) if room else None

        def room_now():
            return self._cell(room_at, room_size) if room_at is not None else None
        step, most, jump = max(1, min(int(step), 60)), max(2, min(int(most), 1500)), max(2, int(jump))
        if within is not None and (len(within) != 4 or within[0] > within[2] or within[1] > within[3]):
            raise HarnessError("within is [x0, y0, x1, y1]")

        grid = float(grid)
        if grid < 0:
            raise HarnessError("grid is how many of the game's units make a cell: above 0")
        if grid and not whole and machine.platform in WHOLE_ONLY:
            raise HarnessError(f"a {machine.platform} game keeps its place as whole numbers, and grid without whole "
                               "reads numbers with a point: give whole as well")

        def signed(value):
            return value - (1 << 8 * size) if value is not None and size > 1 and value >> (8 * size - 1) else value

        def now():
            if grid and whole:
                values = [signed(self._cell(at, size)) for at in (at_x, at_y)]
                return tuple(None if v is None else round(v / grid) for v in values)
            if grid:
                import math
                order = ">f" if machine.platform in BIG_ENDIAN else "<f"
                raw = [self._read(at, 4) for at in (at_x, at_y)]
                if any(len(b) < 4 for b in raw):
                    return None, None
                values = [struct.unpack(order, b)[0] for b in raw]
                return tuple(round(v / grid) if math.isfinite(v) and abs(v) < 1e9 else None for v in values)
            return signed(self._cell(at_x, size)), signed(self._cell(at_y, size))
        origin = now()
        if None in origin:
            raise HarnessError("the place can't be read" + (": with grid and without whole it is read as numbers with "
                                                             "a point; for whole numbers give whole" if grid and not whole else ""))
        import zlib
        ways = ("up", "down", "left", "right") + (("up+left", "up+right", "down+left", "down+right") if diagonals else ())

        def moves(frames: int) -> bool:
            """Whether a push of so many frames, any way, moves the character from here; the game is put back."""
            here = machine.save_state()
            try:
                for push in ways:
                    machine.load_state(here)
                    machine.run(frames, set(push.split("+")), (0.0, 0.0))
                    if now() != origin:
                        return True
                return False
            finally:
                machine.load_state(here)
        # A game may count a step only when it is over, later than a short push ends: a push of `long` frames
        # tells that from a window that takes the pad, before anything is pressed to close one.
        long = min(60, max(2 * step, step + 16))
        closed, late_count = 0, None
        if close and not moves(step):
            late_count = moves(long)
            if not late_count:
                # The window is closed with the button, once, and the press goes into the record of the way:
                # from here on the game is where it was after the press, not before it.
                held = {close}
                machine.run(6, held, (0.0, 0.0))
                self._moved(6, held, (0.0, 0.0))
                machine.run(30, set(), (0.0, 0.0))
                self._moved(30, set(), (0.0, 0.0))
                closed = 1
                origin = now()
                if None in origin:
                    raise HarnessError("the place can't be read")
        through = self._fight_through(fight) if fight else None
        fights: list[dict] = []
        # Tried without the script and off the record of the way: the game is put back afterwards, so nothing
        # of the trying happened as far as a state or a waypoint knows.
        # A state is kept packed: a console's memory is mostly the same bytes over and over, and a house of
        # several rooms is more places than would fit unpacked.
        start, frames = machine.save_state(), machine.frames
        # A place: the state there, where it was reached from, the push, and the moves of a fight it brought.
        places = {origin: (zlib.compress(start, 1), None, None, [])}
        doors, queue, held, late = {}, [origin], len(places[origin][0]), False
        moved: list[int] = []                               # how far each push that moved moved the place
        near, found = max(0, int(near)), False             # with `to`, the trying ends once the place is reached
        while queue and not late and not found:
            # Towards a place asked for, the nearest known place is tried next (and so on round what is in
            # the way); with none, the places in the order they were found, so the map grows evenly.
            if to is not None:
                queue.sort(key=lambda p: abs(p[0] - to[0]) + abs(p[1] - to[1]))
            here = queue.pop(0)
            for push in ways:
                if self._late():
                    late = True
                    break
                machine.load_state(zlib.decompress(places[here][0]))
                room_before = room_now()
                machine.run(step, set(push.split("+")), (0.0, 0.0))
                there, fought = now(), []
                if through is not None and None not in there and there != here:
                    # A step may bring a fight: pressed through here, and the map goes on from where it ended.
                    fought, outcome = through()
                    if outcome is not None:
                        fights.append({"at": list(there), **outcome})
                        there = now()
                if room_at is not None and room_now() != room_before:
                    # The room changed: a door, however far (or little) the place moved.
                    doors.setdefault(here, push)
                    continue
                if None in there or there == here:
                    continue
                # Counted for the hint (a place kept in pixels) whether or not it reads as a change of map: a push
                # of many pixels is a "door" at the default jump, every way.
                moved.append(max(abs(there[0] - here[0]), abs(there[1] - here[1])))
                if moved[-1] >= jump:
                    doors.setdefault(here, push)
                    continue
                inside = within is None or (within[0] <= there[0] <= within[2] and within[1] <= there[1] <= within[3])
                if there not in places and inside and len(places) < most and held < 1 << 30:
                    places[there] = (zlib.compress(machine.save_state(), 1), here, push, fought)
                    held += len(places[there][0])
                    queue.append(there)
                    if to is not None and abs(there[0] - to[0]) <= near and abs(there[1] - to[1]) <= near:
                        found = True
                        break
        machine.load_state(start)
        machine.frames = frames

        def still() -> str:
            """Why no push moved the character: the game counts a step later than a push of `step` ends, or a
            window takes the pad (or the address isn't the place's)."""
            if late_count if late_count is not None else moves(long):
                return (f"no push of {step} frames moved the character, but one of {long} did: the game counts a "
                        f"step only once it is over; give step {long}")
            return (f"no push moved the character from where it stands, not even one of {long} frames: a window "
                    "open (a message, a talk) may be taking the pad; give `close` the button that closes it, or "
                    "check the place's address with a push of your own")
        xs, ys = [p[0] for p in places], [p[1] for p in places]
        rows = ["".join("@" if (x, y) == origin else "D" if (x, y) in doors else "#" if (x, y) in places else "."
                        for x in range(min(xs), max(xs) + 1)) for y in range(min(ys), max(ys) + 1)]
        # Which column is which, to hold over the map: a mark at every fifth, and its number written from the
        # mark on. A place read off the map by counting characters alone is easily a column's offset out.
        ruler = [" "] * (max(xs) - min(xs) + 1 + 12)      # room for the last mark's number past the map's edge
        for x in range(min(xs), max(xs) + 1):
            if x % 5 == 0:
                for k, ch in enumerate("|" + str(x)):
                    if x - min(xs) + k < len(ruler) and (k == 0 or ruler[x - min(xs) + k] == " "):
                        ruler[x - min(xs) + k] = ch
        out = {"x": origin[0], "y": origin[1], "places": len(places), "columns": [min(xs), max(xs)],
               "rows": [min(ys), max(ys)], "ruler": "".join(ruler).rstrip(), "map": rows,
               "doors": [{"at": list(p), "push": push} for p, push in sorted(doors.items())][:MAX_ROWS],
               **({"stopped": "time"} if late else {}),
               **({"closed": closed} if closed else {}),
               **({"fights": len(fights), "fought": fights[:MAX_ROWS]} if fight else {}),
               **({"still": still()} if len(places) == 1 and not doors and not late else {}),
               **({"more": "places beyond these weren't tried: raise most, or give within"}
                  if not late and len(places) >= most else {})}
        if moved and not grid:
            # A push that moves the place several of its numbers: they are most likely pixels, and every pixel
            # a push can end on is a place of its own, so the map grows by thousands and the call's time runs
            # out before it gets anywhere. In steps of a push, it is one place a push.
            typical = sorted(moved)[len(moved) // 2]
            if typical >= 4:
                out["hint"] = (f"a push moves the place {typical} of its numbers: they look like pixels; give grid "
                               f"{typical} and whole to count a push as one step (and jump in those steps)"
                               + ("; every push read as a change of map at this jump" if len(places) == 1 and doors
                                  else ""))
        if to is None:
            return out
        fits = [p for p in places if abs(p[0] - to[0]) <= near and abs(p[1] - to[1]) <= near]
        reached = bool(fits)
        # Not reached: it walks as far as it got, to the place nearest the one asked for, and says so, so that
        # the next call goes on from there instead of trying the same places again.
        goal = min(fits or places, key=lambda p: abs(p[0] - to[0]) + abs(p[1] - to[1]))
        if not reached and goal == origin:
            return {**out, "reached": False, "stopped_at": list(origin),
                    "why": "no place it got to is nearer the one asked for than where it stands"}
        pushes, at = [], goal
        while places[at][1] is not None:
            pushes.append((places[at][2], places[at][3]))
            at = places[at][1]
        # The game is taken as it stood at that place while trying, and the pushes that led there go into the
        # record of the way. Playing them again from here may end elsewhere: a core need not go on from a
        # loaded state exactly as it would have run through it, and each place was reached from a loaded one.
        machine.load_state(zlib.decompress(places[goal][0]))
        machine.frames = frames + sum(step + sum(n for n, _ in fought) for _, fought in pushes)
        for push, fought in reversed(pushes):
            self._moved(step, set(push.split("+")), (0.0, 0.0))
            for n, keys in fought:                  # a fight the push brought, pressed through
                self._moved(n, set(keys), (0.0, 0.0))
        events = []
        if self.reader is not None:
            self.reader.state_loaded()
            self._collect(self.reader.take(), events)
        self._advance(2, set(), (0.0, 0.0), events)
        cx, cy = now()
        if not reached:
            why = ("the call's time ran out" if late else "it tried as many places as most allows" if len(places) >= most
                   else "the way on wasn't found among the places it could reach")
            return {**{k: v for k, v in out.items() if k != "map"}, "x": cx, "y": cy, "pushes": len(pushes),
                    "reached": False, "stopped_at": [cx, cy],
                    "why": f"didn't reach {list(to)}: {why}; walked to the nearest place it got to"}
        return {**{k: v for k, v in out.items() if k != "map"}, "x": cx, "y": cy, "pushes": len(pushes),
                "reached": (cx, cy) == goal, "frames": machine.frames,
                **({"events": events} if self.reader is not None and events else {})}

    @tool("The picture on screen with a grid over it in the game's own numbers for where the character stands, "
          "the numbers along the top and the left, and the character's own tile marked: so that a door, a "
          "person or stairs seen in the picture can be given to walk_to as a place. For a map the game keeps "
          "the character in the middle of (most do); say where it draws the character otherwise.",
          x_address=("string", "where the game keeps the place across, hex (default: as cursor_find found)", False),
          y_address=("string", "and down", False), size=("integer", "bytes of each: 1, 2 or 4 (default 2)", False),
          tile=("integer", "a tile's width and height on screen, in pixels (default 16)", False),
          per=("integer", "how much the numbers go up from a tile to the next (default 1)", False),
          at_x=("integer", "the left edge of the character's tile on screen, in pixels (default: the middle)", False),
          at_y=("integer", "and its top", False),
          screen_x_address=("string", "or where the game keeps where on screen it draws the character, across, "
                                      "hex, 2 bytes: for a map whose picture doesn't keep the character in the "
                                      "middle. at_x and at_y are then added to it (the mark's place from the "
                                      "sprite's: default 8 left and 16 up, a sprite kept by its feet)", False),
          screen_y_address=("string", "and down", False),
          scale=("number", "enlarge the picture (default 2)", False))
    def map_picture(self, x_address: str = "", y_address: str = "", size: int = 2, tile: int = 16, per: int = 1,
                    at_x: int | None = None, at_y: int | None = None, scale: float = 2.0,
                    screen_x_address: str = "", screen_y_address: str = ""):
        from PIL import Image, ImageDraw
        at = self._cursor_cells(x_address, y_address, size)
        cx, cy = self._cell(at[0], at[2]), self._cell(at[1], at[2])
        if cx is None or cy is None:
            raise HarnessError("the place can't be read")
        tile, per, scale = max(4, int(tile)), max(1, int(per)), max(1.0, min(4.0, float(scale)))
        shot = self._picture().convert("RGB")
        if screen_x_address and screen_y_address:
            sx, sy = self._cell(int(screen_x_address, 16), 2), self._cell(int(screen_y_address, 16), 2)
            if sx is None or sy is None:
                raise HarnessError("where the character is drawn can't be read")
            px, py = sx + (-8 if at_x is None else int(at_x)), sy + (-16 if at_y is None else int(at_y))
        else:
            px = (shot.width - tile) // 2 if at_x is None else int(at_x)
            py = (shot.height - tile) // 2 if at_y is None else int(at_y)
        shot = shot.resize((round(shot.width * scale), round(shot.height * scale)), Image.NEAREST)
        left, top = 22, 12                                              # room for the numbers
        sheet = Image.new("RGB", (shot.width + left, shot.height + top), (0, 0, 0))
        sheet.paste(shot, (left, top))
        draw = ImageDraw.Draw(sheet)
        first_col, first_row = -((px + tile - 1) // tile), -((py + tile - 1) // tile)
        columns, rows = [], []
        col = first_col
        while (edge := (px + col * tile) * scale) < shot.width:
            draw.line([(left + edge, top), (left + edge, sheet.height)], fill=(90, 90, 90))
            if edge >= 0 and (col % 2 == 0 or tile * scale >= 28):
                draw.text((left + edge + 2, 0), str(cx + col * per), fill=(255, 255, 0))
            columns.append(cx + col * per)
            col += 1
        row = first_row
        while (edge := (py + row * tile) * scale) < shot.height:
            draw.line([(left, top + edge), (sheet.width, top + edge)], fill=(90, 90, 90))
            if edge >= 0:
                draw.text((0, top + edge + 2), str(cy + row * per), fill=(255, 255, 0))
            rows.append(cy + row * per)
            row += 1
        draw.rectangle([left + px * scale, top + py * scale, left + (px + tile) * scale, top + (py + tile) * scale],
                       outline=(255, 0, 0), width=2)
        return ("both", {"x": cx, "y": cy, "columns": [columns[0], columns[-1]], "rows": [rows[0], rows[-1]],
                         "per_tile": per, "frame": self.machine.frames}, self._png(sheet))

    @tool("Goes round a picture's spots with the cursor and says what the loaded script read at each: moves "
          "the cursor there (cursor_to), presses the button, then presses on until the script has said "
          "nothing for a press, and does the steps of `after` if any (to leave a close-up, say). For a "
          "point-and-click scene: every spot of a room in one call. Where the cursor can't be brought to a "
          "spot, its numbers are looked for again once (they move from scene to scene). Stops where the "
          "call's time is up.",
          points=("array", "the spots, each [x, y] in the cursor's numbers", True),
          button=("string", "the button that looks or confirms (default a)", False),
          wait=("integer", "frames after each press (default 130: text may come a hundred frames after its press)", False),
          first=("integer", "presses made before a quiet one counts (default 1: the first may only open a menu)", False),
          most=("integer", "at most this many presses at a spot (default 16)", False),
          after=("array", "steps to do after each spot, as `steps` takes them", False),
          if_stuck=("string", "a button to press when the cursor won't move (default b; \"\" for none)", False))
    def visit(self, points: list, button: str = "a", wait: int = 130, first: int = 1, most: int = 16,
              after: list | None = None, if_stuck: str = "b") -> dict:
        machine = self._need()
        if self.reader is None:
            raise HarnessError("no script is loaded to say what each spot gives: call script_try first")
        if not points or not all(isinstance(p, (list, tuple)) and len(p) == 2 for p in points):
            raise HarnessError("points is a list of [x, y]")
        self._cursor_cells("", "", 2)
        press = {"buttons": [button], "hold": 3, "wait": max(1, int(wait))}
        plan = [press] * max(0, int(first)) + [{**press, "times": max(1, int(most)), "until_quiet": 1}] + list(after or [])
        rows, why, found_again = [], None, False
        for x, y in points:
            if self._late():
                why = "time"
                break
            moved = self.cursor_to(int(x), int(y), if_stuck=if_stuck)
            row = {"at": [int(x), int(y)], "reached": moved.get("reached", False)}
            if not moved.get("reached") and not found_again:
                # A cursor's numbers move from scene to scene: look for them once more, and try again.
                found_again = True
                try:
                    self.cursor_find()
                    moved = self.cursor_to(int(x), int(y), if_stuck=if_stuck)
                    row.update(reached=moved.get("reached", False), found_again=True)
                except HarnessError:
                    row["found_again"] = False
            if not moved.get("reached"):
                row["cursor"] = [moved["x"], moved["y"]]
            out = self.steps(plan)
            said = [e for e in moved.get("events", []) + out.get("events", []) if e.get("kind") == "text"]
            row["presses"] = sum(1 for step in out["steps"] if "on" not in step)
            row["said"] = [{"frame": e["frame"], "channel": e.get("channel"), "text": e.get("text"),
                            **({"speaker": e["speaker"]} if e.get("speaker") else {})} for e in said]
            rows.append(row)
            if out.get("stopped") == "time" or moved.get("stopped") == "time":
                why = "time"
                break
        return {"frames": machine.frames, "spots": rows, **({"stopped": why, "left": len(points) - len(rows)} if why else {})}

    @tool("Places in memory that take some values one after the other over snaps taken in order (a snap every "
          "frame while a line types): for a game that keeps no line of text but must hold each character's "
          "code for a moment as it draws it. Give the codes of the characters in the order they appear, as far "
          "as you know them (from a naming screen, say); other values may come between. A value given twice "
          "running must be left and taken again.",
          snaps=("array", "the snaps, in order", True),
          values=("array", "the values in the order they must come, hex: [\"1E\", \"12\", \"15\"]", True),
          unit=("integer", "bytes of a value: 1 (default) or 2", False),
          within=("array", "only places from one address to another, hex", False),
          pattern=("string", "instead of values, when the codes aren't known: the characters as they appear, a "
                             "letter each, the same letter for the same character (そっそうか is abacd), ? for "
                             "one you can't tell. The place must take values that go so, one after the other, "
                             "once the value it rests at between characters is left out", False))
    def follows(self, snaps: list, values: list | None = None, unit: int = 1, within: list | None = None,
                pattern: str = "") -> dict:
        import numpy as np
        if pattern:
            if values or unit not in (1, 2) or len(pattern) < 4 or len(set(pattern) - {"?"}) < 2:
                raise HarnessError("a pattern goes without values, and has four characters or more")
            kind = ("u1" if unit == 1 else (">u2" if getattr(self.machine, "platform", "") in BIG_ENDIAN else "<u2"))
            rows, base = [], 0
            for name in snaps:
                _, base, ram = self._memories([name])[0]
                rows.append(np.frombuffer(ram[:len(ram) // unit * unit], dtype=kind))
            if len({len(r) for r in rows}) != 1:
                raise HarnessError("the snaps aren't of the same memory")
            grid = np.stack(rows)                                        # snaps by cells
            changes = (grid[1:] != grid[:-1]).sum(axis=0)
            found = []
            for i in np.nonzero((changes >= len(pattern) - 1) & (changes <= 6 * len(pattern) + 16))[0]:
                column = grid[:, i]
                runs = [int(column[0])] + [int(v) for k, v in enumerate(column[1:], 1) if v != column[k - 1]]
                rest = max(set(runs), key=runs.count)
                for line in ([v for v in runs if v != rest], runs):
                    at = search.pattern_at(line, pattern)
                    if at is not None:
                        got = line[at:at + len(pattern)]
                        found.append({"address": f"0x{base + int(i) * unit:08X}", "rests_at": f"{rest:0{2 * unit}X}",
                                      "values": {ch: f"{v:0{2 * unit}X}" for ch, v in zip(pattern, got) if ch != "?"}})
                        break
            if within is not None:
                low, high = (int(str(v), 16) for v in within)
                found = [f for f in found if low <= int(f["address"], 16) < high]
            return {"count": len(found), "places": found[:MAX_HITS]}
        values = values or []
        if unit not in (1, 2) or len(values) < 2 or len(snaps) < len(values):
            raise HarnessError("unit is 1 or 2; give two values or more, and at least as many snaps")
        try:
            wanted = [int(str(v), 16) for v in values]
        except ValueError:
            raise HarnessError("values are hex") from None
        kind = ("u1" if unit == 1 else (">u2" if getattr(self.machine, "platform", "") in BIG_ENDIAN else "<u2"))
        state = before = base = None
        seen_at: list = []
        for name in snaps:
            _, base, ram = self._memories([name])[0]
            cells = np.frombuffer(ram[:len(ram) // unit * unit], dtype=kind).astype(np.int64)
            if state is None:
                state, before = np.zeros(len(cells), dtype=np.int64), np.full(len(cells), -1, dtype=np.int64)
                seen_at = [np.full(len(cells), -1, dtype=np.int64) for _ in wanted]
            elif len(cells) != len(state):
                raise HarnessError("the snaps aren't of the same memory")
            want = np.array(wanted + [-2], dtype=np.int64)[state]
            hit = (cells == want) & (cells != before)
            for k in range(len(wanted)):
                seen_at[k][hit & (state == k)] = len([n for n in snaps[:snaps.index(name)]])
            state = state + hit
            before = cells
        places = np.nonzero(state >= len(wanted))[0]
        if within is not None:
            low, high = (int(str(v), 16) for v in within)
            places = places[(places * unit + base >= low) & (places * unit + base < high)]
        return {"count": int(len(places)),
                "places": [{"address": f"0x{base + int(i) * unit:08X}",
                            "at_snaps": [snaps[int(seen_at[k][i])] for k in range(len(wanted))]} for i in places[:MAX_HITS]]}

    @tool("Runs the game and lists each change of a place in memory with its frame: how a pointer, a counter "
          "or a flag goes while something happens (a line types, a page turns).",
          address=("string", "the place, hex", True), frames=("integer", "how many frames to run", True),
          size=("integer", "bytes: 1, 2 or 4 (default 4)", False),
          buttons=("array", "buttons held for the first `hold` frames", False),
          hold=("integer", "frames the buttons are held (default 4)", False),
          more=("array", "other places to list beside it, hex: a row then gives them all, whenever any changes", False),
          stretch=("integer", "or so many values on end from the place (16 at most): a queue's front, a record", False))
    def watch(self, address: str, frames: int, size: int = 4, buttons: list | None = None, hold: int = 4,
              more: list | None = None, stretch: int = 0) -> dict:
        machine = self._need()
        if size not in (1, 2, 4) or not 0 < int(frames) <= MAX_FRAMES:
            raise HarnessError(f"size is 1, 2 or 4; 1 to {MAX_FRAMES} frames")
        at, held, events = int(address, 16), self._buttons(buttons), []
        places = [at] + [int(str(a), 16) for a in more or ()]
        if int(stretch) > 0:                    # so many values on end from the first place: a queue's front, a record
            places = [at + size * k for k in range(min(int(stretch), 16))] + places[1:]
        if len(places) > 16:
            raise HarnessError("at most 16 places")
        several = len(places) > 1
        changes, last = [], [[self._cell(p, size) for p in places]]
        first = last[0][0]
        show = lambda v: None if v is None else f"0x{v:0{2 * size}X}"      # noqa: E731

        def note():
            now = [self._cell(p, size) for p in places]
            if now != last[0]:
                last[0] = now
                if len(changes) < MAX_HITS:
                    changes.append({"frame": machine.frames, "values": " ".join(str(show(v))[2:] for v in now)} if several
                                   else {"frame": machine.frames, "value": show(now[0])})
            return None
        why = None
        if held:
            ran, why = self._advance(max(1, min(int(hold), int(frames))), held, (0.0, 0.0), events, note)
        if not why:
            _, why = self._advance(int(frames) - (min(int(hold), int(frames)) if held else 0) or 1, set(), (0.0, 0.0),
                                   events, note)
        return {"frames": machine.frames,
                **({"places": [f"0x{p:08X}" for p in places], "from": " ".join(str(show(self._cell(p, size)))[2:] for p in places)}
                   if several and not changes else {}),
                **({"places": [f"0x{p:08X}" for p in places]} if several and changes else {}),
                **({} if several else {"from": None if first is None else f"0x{first:0{2 * size}X}"}),
                "changes": changes, **({"stopped": why} if why else {}),
                **({"events": events} if self.reader is not None else {})}

    @tool("Words in memory that point at an address, or into the stretch after it. With snaps, the same for "
          "each of them, a row for each word: which moments it points there in, and where. A word that "
          "points into a line in some moments only is what follows the line as it types or is shown.",
          address=("string", "the address pointed at, hex", True),
          span=("integer", "also pointers up to this many bytes past it (default 0)", False),
          big_endian=("boolean", "the console's byte order (default: true for gc, wii and saturn, else false)", False),
          snaps=("array", "look in these snaps instead of live memory", False),
          moving=("boolean", "with snaps: only the words that point there in every one of them and at "
                             "another place in some: what goes along a script as the game reads it, when "
                             "nothing else says which line is on screen", False))
    def refs(self, address: str, span: int = 0, big_endian: bool | None = None, snaps: list | None = None,
             moving: bool = False) -> dict:
        import numpy as np
        if big_endian is None:
            big_endian = self._need().platform in BIG_ENDIAN
        target = int(address, 16)

        def pointing(base, ram):
            words = np.frombuffer(ram[:len(ram) // 4 * 4], dtype=">u4" if big_endian else "<u4")
            places = np.nonzero((words >= target) & (words <= target + max(0, int(span))))[0]
            return {base + 4 * int(i): int(words[i]) for i in places}

        if not snaps:
            found = pointing(*self._need().ram())
            return {"count": len(found), "refs": [{"at": f"0x{at:08X}", "points_at": f"0x{to:08X}"}
                                                  for at, to in list(found.items())[:MAX_HITS]]}
        each = {name: pointing(base, ram) for name, base, ram in self._each_memory(snaps)}
        rows = {}
        for name, found in each.items():
            for at, to in found.items():
                rows.setdefault(at, {})[name] = f"0x{to:08X}"
        # First those that point there in some moments only, or at another place from one to the next (a
        # cursor going along the line); last those that never move.
        if moving:
            rows = {at: found for at, found in rows.items()
                    if len(found) == len(each) and len(set(found.values())) > 1}
            # Most places first: what follows the game's reading is at a new place in nearly every moment.
            order = sorted(rows, key=lambda at: (-len(set(rows[at].values())), at))
            return {"count": len(rows), "snaps": len(each),
                    "refs": [{"at": f"0x{at:08X}", "places": len(set(rows[at].values())), "points_at": rows[at]}
                             for at in order[:MAX_HITS]]}
        order = sorted(rows, key=lambda at: (len(rows[at]) == len(each) and len(set(rows[at].values())) == 1,
                                             len(rows[at]), at))
        return {"count": len(rows), "snaps": len(each),
                "refs": [{"at": f"0x{at:08X}", "in": len(rows[at]), "points_at": rows[at]} for at in order[:MAX_HITS]]}

    @tool("The words from an address on, and where each that points into memory leads: the text that stands "
          "there, when it reads as text. For a stack or a record whose pointers are to strings.",
          address=("string", "where the words start, hex", True),
          count=("integer", "how many words (default 32, at most 256)", False),
          big_endian=("boolean", "the console's byte order (default: true for gc, wii and saturn, else false)", False))
    def pointed(self, address: str, count: int = 32, big_endian: bool | None = None) -> dict:
        machine = self._need()
        base, ram = machine.ram()
        if big_endian is None:
            big_endian = machine.platform in BIG_ENDIAN
        start, count = int(address, 16), max(1, min(int(count), 256))
        data = self._read(start, 4 * count)
        rows = []
        for i in range(len(data) // 4):
            word = int.from_bytes(data[4 * i:4 * i + 4], "big" if big_endian else "little")
            # Through the memory's other addresses too, but there only when text stands at the end: a small
            # number is an address as well, on a console whose memory also answers from 0.
            to = next((word - m for m in (base, *machine.mirrors()) if 0 <= word - m < len(ram)), None)
            if to is None:
                continue
            row = {"at": f"0x{start + 4 * i:08X}", "points_at": f"0x{word:08X}"}
            text = search.string_at(ram, to)
            if text:
                row["encoding"], row["text"] = text
            elif not 0 <= word - base < len(ram):
                continue
            rows.append(row)
        return {"words": len(data) // 4, "pointers": rows}

    @tool("Writes bytes into the game's memory, to REACH a moment the game is slow to get to: a chapter, a flag, "
          "a position, a timer. Then let the game produce its text itself. Never write the text or the buffer a "
          "script reads, except as a test you label as made up. Every write is kept in scratch's record "
          "(record/memory-writes.jsonl), snaps taken after one say so, and a pull request must list every check "
          "that relied on one and what was written.",
          address=("string", "the game's own address, hex", True),
          hex=("string", f"the bytes to put there, as hex (at most {MAX_POKE})", True),
          why=("string", "what this write is for, in a sentence: it goes into the record", True),
          synthetic=("boolean", "true when this writes text or a buffer a script reads: a made-up test", False))
    def poke(self, address: str, hex: str, why: str, synthetic: bool = False) -> dict:
        machine = self._need()
        if not self.allow_poke:
            raise HarnessError("memory writes are off: the harness must be started with --allow-poke")
        if not isinstance(why, str) or len(why.strip()) < 8:
            raise HarnessError("say why, in a sentence: the record keeps it")
        try:
            at, data = int(address, 16), bytes.fromhex(hex)
        except ValueError:
            raise HarnessError("address and hex are hex") from None
        if not 0 < len(data) <= MAX_POKE:
            raise HarnessError(f"1 to {MAX_POKE} bytes")
        before = self._read(at, len(data))
        if len(before) < len(data) or machine.write(at, data) != len(data):
            raise HarnessError("that isn't all in main memory")
        self.pokes += 1
        self.route["moves"].append({"poke": f"0x{at:08X}", "hex": data.hex()})
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "game": self.game, "frame": machine.frames,
                 "write": self.pokes, "address": f"0x{at:08X}", "before": before.hex(), "after": data.hex(),
                 "why": why.strip(), "synthetic": bool(synthetic)}
        folder = self.roots.scratch / "record"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "memory-writes.jsonl").open("a", encoding="utf-8") as record:
            record.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return {"written": len(data), "address": entry["address"], "before": entry["before"], "write": self.pokes}

    @tool("Does the same thing twice or more and says whether the game ended up the same: from this moment "
          "(it goes back to it by a state kept in memory), or from power-on (the game is loaded anew each time, "
          "and a loaded script is dropped). Tells whether a core repeats itself, which everything found by "
          "replaying rests on. Afterwards the game stands where the last run ended.",
          frames=("integer", "run this many frames each time", False),
          steps=("array", "or these steps each time, as `steps` takes them (their snaps and pictures are left out)", False),
          runs=("integer", "how many times (default 2, at most 5)", False),
          power_on=("boolean", "start each run from power-on instead of from this moment", False))
    def repeat(self, frames: int = 0, steps: list | None = None, runs: int = 2, power_on: bool = False) -> dict:
        import numpy as np
        machine = self._need()
        if bool(frames) == bool(steps):
            raise HarnessError("give frames, or steps")
        runs = max(2, min(int(runs), 5))
        if power_on and self.once:
            raise HarnessError("this core can't be loaded twice in one harness. From power-on, run the same "
                               "frames in two harnesses: snap in the first, and ask `differs` in the second")
        plan = [{k: v for k, v in step.items() if k not in ("snap", "shot")} for step in steps or []
                if isinstance(step, dict)]
        start = None if power_on else machine.save_state()
        way = json.dumps(self.route)
        ends, base = [], 0
        for _ in range(runs):
            if power_on:
                game, core, platform, options = self.loaded
                self.load(game, core, platform, options=options)
                machine = self.machine
            else:
                machine.load_state(start)
                self.route = json.loads(way)
                if self.reader is not None:
                    self.reader.state_loaded()
                    self._collect(self.reader.take(), [])
            first = machine.frames
            if plan:
                out = self.steps(plan)
                out = out[1] if isinstance(out, tuple) else out
                late = out.get("stopped") == "time"
            else:
                late = self._advance(min(int(frames), MAX_FRAMES), set(), (0.0, 0.0), [])[1] == "time"
            if late:
                raise HarnessError("the call's time was up before the runs were done: repeat something shorter")
            base, ram = machine.ram()
            picture = machine.picture()
            ends.append((machine.frames - first, np.frombuffer(ram, dtype=np.uint8), machine.regions(),
                         None if picture is None else picture.copy()))
        ran, memory, regions, picture = ends[0]
        differing, places, others = [], [], set()
        for frames_run, ram, its_regions, its_picture in ends[1:]:
            if frames_run != ran or len(ram) != len(memory):
                raise HarnessError("the runs weren't of the same length: the steps wait on something that varies")
            where = np.nonzero(ram != memory)[0]
            differing.append(int(len(where)))
            places = places or [f"0x{base + int(i):08X}" for i in where[:20]]
            others |= {name for name in regions if its_regions.get(name) != regions[name]}
            if picture is not None and (its_picture is None or not np.array_equal(picture, its_picture)):
                others.add("picture")
        return {"runs": runs, "frames": ran, "from": "power-on" if power_on else "this moment",
                "same": not any(differing) and not others, "bytes_differing": differing,
                **({"first_places": places} if places else {}), **({"also_differing": sorted(others)} if others else {})}

    # ---- states

    @tool("Saves the game's state under a name, in scratch.", name=("string", "a name for it", True))
    def state_save(self, name: str) -> dict:
        path = self._state_file(name)
        state = self._need().save_state()
        path.write_bytes(gzip.compress(state, 1))
        # A state saved after a write carries it. A file beside the state says how many, so that loading it
        # (in this session or a later one) marks what follows; the record says which state.
        path.with_name(f"{name}.route.json").write_text(
            json.dumps({"game": self.game, "core": self.core_hash, **self.route}), encoding="utf-8")
        beside = path.with_name(f"{name}.writes.json")
        if self.pokes:
            beside.write_text(json.dumps({"memory_writes": self.pokes}), encoding="utf-8")
        elif beside.is_file():
            beside.unlink()
        if self.pokes:
            with (self.roots.scratch / "record" / "memory-writes.jsonl").open("a", encoding="utf-8") as record:
                record.write(json.dumps({"game": self.game, "frame": self._need().frames, "state_saved": name,
                                         "after_writes": self.pokes}, ensure_ascii=False) + "\n")
        return {"state": name, "bytes": len(state), **({"memory_writes": self.pokes} if self.pokes else {})}

    @tool("Goes back to a saved state: one saved here, or a reader's from the inbox. A loaded script is told.",
          name=("string", "a state's name, or a path under the inbox's snapshots ending in .state.gz", True))
    def state_load(self, name: str) -> dict:
        machine = self._need()
        path = self.roots.inside("inbox", name) if name.endswith(".state.gz") else self._state_file(name)
        if not path.is_file():
            raise HarnessError(f"no such state: {name}")
        try:
            machine.load_state(path.read_bytes())
        except RuntimeError as e:
            raise HarnessError(str(e)) from None
        # Memory is now the state's: with the writes behind that state, and none of those made since.
        beside = path.with_name(path.name[:-len(".state.gz")] + ".writes.json")
        self.pokes = int(json.loads(beside.read_text(encoding="utf-8")).get("memory_writes", 0)) if beside.is_file() else 0
        # The way to this state, when it was kept with it and is this game's; else the way starts at the state.
        way = path.with_name(path.name[:-len(".state.gz")] + ".route.json")
        kept = json.loads(way.read_text(encoding="utf-8")) if way.is_file() else {}
        self.route = ({"base": kept["base"], "moves": kept["moves"]} if kept.get("game") == self.game
                      else {"base": {"state": name}, "moves": []})
        if self.reader is not None:
            self.reader.state_loaded()
            self._collect(self.reader.take(), [])
        return {"loaded": name, "frames": machine.frames, **({"memory_writes": self.pokes} if self.pokes else {})}

    # ---- waypoints

    def _waypoint(self, name: str) -> Path:
        return self.roots.inside("scratch", f"waypoints/{self._name(name)}.json")

    def _shown(self):
        """The picture now as an array, or None."""
        return self.machine.picture()

    @tool("Keeps this moment as a waypoint: the game and core, every move since power-on (or since the state "
          "the way started from), a state to come back by, and the picture, by which a replay is checked. A "
          "waypoint from power-on outlives a new build of the core, which a state doesn't.",
          name=("string", "a name for it", True),
          note=("string", "what is on screen here, for whoever comes back", False))
    def waypoint_save(self, name: str, note: str = "") -> dict:
        machine, path = self._need(), self._waypoint(name)
        if machine.picture() is None:
            raise HarnessError("the game hasn't shown a frame yet: run some frames")
        game, core, platform, options = self.loaded
        picture = self._png(self._picture())
        path.with_suffix(".png").write_bytes(picture)
        path.with_suffix(".state.gz").write_bytes(gzip.compress(machine.save_state(), 1))
        point = {"game": game, "core": core, "core_sha256": self.core_hash, "platform": platform, "options": options,
                 "base": self.route["base"], "moves": self.route["moves"], "frames": machine.frames,
                 "picture_sha1": hashlib.sha1(machine.picture().tobytes()).hexdigest(),
                 "memory_writes": self.pokes, "note": note}
        path.write_text(json.dumps(point, ensure_ascii=False), encoding="utf-8")
        return {"waypoint": name, "from": point["base"], "moves": len(point["moves"]), "frames": machine.frames,
                **({"memory_writes": self.pokes} if self.pokes else {})}

    @tool("The waypoints kept in scratch: the game, where each starts, its length, its note.")
    def waypoints(self) -> dict:
        out = {}
        for path in sorted((self.roots.scratch / "waypoints").glob("*.json")):
            point = json.loads(path.read_text(encoding="utf-8"))
            out[path.stem] = {"game": point["game"], "core": point["core"], "from": point["base"],
                              "frames": point["frames"], "note": point.get("note", ""),
                              **({"memory_writes": point["memory_writes"]} if point.get("memory_writes") else {})}
        return {"waypoints": out}

    @tool("Goes to a waypoint, loading its game if need be: by its state when the core is the build that made "
          "it, else by playing its moves again from where its way starts. Then says whether the picture is the "
          "one kept with it. A replay that reaches it keeps a state of the new build.",
          name=("string", "the waypoint's name", True),
          replay=("boolean", "play the moves again even if the state would load: the check that the way still holds", False))
    def waypoint_go(self, name: str, replay: bool = False) -> dict:
        import numpy as np
        path = self._waypoint(name)
        if not path.is_file():
            raise HarnessError(f"no waypoint called {name}")
        point = json.loads(path.read_text(encoding="utf-8"))
        wanted = (point["game"], point["core"], point["platform"], point["options"])
        state = path.with_suffix(".state.gz")
        loaded_now = self.machine is not None and self.loaded == wanted
        core_file = self.roots.core(point["core"])
        same_build = core_file.is_file() and hashlib.sha256(core_file.read_bytes()).hexdigest() == point["core_sha256"]
        via = "state" if same_build and state.is_file() and not replay else "replay"
        if via == "state":
            if not loaded_now:
                self.load(*wanted[:3], options=wanted[3])
            try:
                self.machine.load_state(state.read_bytes())
            except RuntimeError as e:
                raise HarnessError(str(e)) from None
            if self.reader is not None:
                self.reader.state_loaded()
                self._collect(self.reader.take(), [])
        else:
            base = point["base"]
            if base == "power-on":
                if self.once:
                    raise HarnessError("this core can't be loaded twice in one harness: start it again and make "
                                       "waypoint_go the first call")
                self.load(*wanted[:3], options=wanted[3])
            else:
                if not loaded_now:
                    self.load(*wanted[:3], options=wanted[3])
                self.state_load(base["state"])
            for move in point["moves"]:
                if isinstance(move, dict):
                    if not self.allow_poke:
                        raise HarnessError("this waypoint's way writes memory: the harness must be started with --allow-poke")
                    self.poke(move["poke"], move["hex"], f"replaying the waypoint {name}")
                    continue
                frames, buttons, stick = move
                ran, why = self._advance(int(frames), set(buttons), tuple(stick), [])
                if why == "time":
                    raise HarnessError(f"the call's time was up {self.machine.frames} frames into the way (of "
                                       f"{point['frames']}): start the harness with more --call-seconds")
        self.route = {"base": point["base"], "moves": json.loads(json.dumps(point["moves"]))}
        self.pokes = int(point.get("memory_writes", 0))
        picture = self.machine.picture()
        same = picture is not None and hashlib.sha1(picture.tobytes()).hexdigest() == point["picture_sha1"]
        out = {"waypoint": name, "via": via, "frames": self.machine.frames, "picture_same": bool(same),
               "note": point.get("note", "")}
        if not same and picture is not None:
            from PIL import Image
            kept = np.asarray(Image.open(path.with_suffix(".png")).convert("RGB"))
            if kept.shape == picture.shape:
                out["pixels_differing"] = round(float((kept != picture).any(axis=2).mean()), 4)
        if via == "replay" and same:
            path.with_suffix(".state.gz").write_bytes(gzip.compress(self.machine.save_state(), 1))
            point["core_sha256"] = self.core_hash
            path.write_text(json.dumps(point, ensure_ascii=False), encoding="utf-8")
        return out

    # ---- scripts

    @tool("Loads a script on the running game (docs/script-api.md), in place of the one before. From then on run "
          "ticks it after every frame and returns what it emits. Send the script as `yomikata bundle` gives it.",
          source=("string", "the script's Lua source", True),
          name=("string", "its file name, for messages, e.g. SLPS-00000.lua", False),
          data=("object", "its data files: name (tbl, charset) to their text", False))
    def script_try(self, source: str, name: str = "script.lua", data: dict | None = None) -> dict:
        machine = self._need()
        self._close_reader()
        try:
            self.reader = rd.Reader(machine.read, platform=machine.platform, game_id=Path(name).stem,
                                    regions=_Regions(machine),
                                    data={k: v.encode("utf-8") for k, v in (data or {}).items()})
        except rd.ReaderUnavailable as e:
            raise HarnessError(str(e)) from None
        picture = machine.picture()
        if picture is not None:
            self.reader.picture(picture.shape[1], picture.shape[0])
        events: list = []
        try:
            self.reader.load(source, name)
        except rd.ScriptError as e:
            self._collect(self.reader.take(), events)
            self._close_reader()
            return {"loaded": False, "error": str(e), "events": events}
        self.script_name = name
        self._collect(self.reader.take(), events)
        return {"loaded": True, "header": [list(line) for line in rd.header(source)], "events": events}

    @tool("Runs a script over snaps, each on its own, and counts what it reads: against the lines you gave "
          "the snaps (snap_lines), and, with an entry, against that entry's layout read from the same memory "
          "(as `yomikata compare`). The counts hold no text; `show` adds both readings, for your eyes only.",
          source=("string", "the script's Lua source, as `yomikata bundle` gives it", True),
          name=("string", "its file name, e.g. SLPS-00000.lua", False),
          data=("object", "its data files: name to their text", False),
          snaps=("array", "snaps' names (default: all of this session's)", False),
          frames=("integer", "ticks over each snap (default 40): a script may wait for text to stand still", False),
          entry=("object", "a catalog entry (its JSON) whose layout to compare with", False),
          tables=("object", "that entry's tables: each path the entry names to the table's text", False),
          version=("string", "which of the entry's versions the snaps are (its exe_sha1 or header_crc32)", False),
          show=("boolean", "also give what was read at each snap", False),
          scenes=("object", "the snaps by scene, a name for each scene to its snaps: the answer then says "
                            "whether the bar for a checked script is met (30 screens from play over 3 scenes "
                            "or more, nine in ten with text agreeing, nothing read where there is none, no "
                            "memory write), and what is short of it", False),
          live=("boolean", "count what the script said in play on the way to each snap (kept with the snap when "
                           "it was taken with a script loaded) instead of running it over the still snap: for a "
                           "script that reads a line only as it appears", False),
          scrolls=("boolean", "with scenes and live, for a window that scrolls and keeps old rows: give each "
                              "screen all its rows as usual (snap_lines), and a screen is held to the rows it "
                              "gained, those after what the screen before it in its scene left. What a script "
                              "that says each line once is meant to say", False),
          reread=("boolean", "with live, for snaps already taken from play: run this script over each snap's "
                             "memory again (as without live) instead of counting what was said on the way to it, "
                             "to check a fix to a script or its table before spending calls on a fresh run. "
                             "Every snap is re-read the same way, those taken with no script loaded too; the "
                             "answer and the record say how many were re-read. The bar is never met by it: the "
                             "bar counts what was said in play", False))
    def compare(self, source: str = "", name: str = "script.lua", data: dict | None = None, snaps: list | None = None,
                frames: int = 40, entry: dict | None = None, tables: dict | None = None, version: str = "",
                show: bool = False, live: bool = False,
                scenes: dict | None = None, scrolls: bool = False, reread: bool = False) -> dict:
        first_of_scene, left = set(), []
        if reread and not live:
            raise HarnessError("reread goes with live: without live every snap is run over as it stands already")
        played = live and not reread        # what was said in play counts, not a reading of the still snap
        if reread and scrolls:
            raise HarnessError("scrolls counts the rows said in play: a snap re-read gives its whole window")
        if scrolls and not (scenes and live):
            raise HarnessError("scrolls goes with scenes and live: the rows a screen gained are counted from play")
        if scenes:
            if not all(isinstance(v, list) and v for v in scenes.values()):
                raise HarnessError("scenes is an object: a name for each scene to the snaps taken in it")
            snaps = [n for group in scenes.values() for n in group]
            first_of_scene = {self._name(group[0]) for group in scenes.values()}
        from .. import compare as cmp
        from ..check import agreement, sidecars
        from ..run import Reading, _load, _regions, with_regions
        names = [self._name(n) for n in (snaps or sorted(self.snaps))]
        if not names:
            raise HarnessError("no snaps to run it over: snap some moments first")
        paths = [self.roots.inside("scratch", f"snaps/{n}.ram.gz") for n in names]
        for n, path in zip(names, paths):
            if not path.is_file():
                raise HarnessError(f"no snap called {n}")
        blobs = {k: v.encode("utf-8") for k, v in (data or {}).items()}
        readings, rows = [], []
        for n, path in zip(names, paths):
            meta, _ = sidecars(path)
            platform = PLATFORM_ALIASES.get(meta.get("platform"), meta.get("platform"))
            read = with_regions(rd.memory(int(meta["base_address"], 16), _load(path), MIRRORS.get(platform, ())),
                                platform, _regions(path))
            if meta.get("game") and meta.get("game") == self.game:
                # A snap keeps memory, not the cartridge: the loaded game's, where the snap is of that game.
                from .machine import with_rom
                read = with_rom(read, getattr(self.machine, "rom", None))
            reading = Reading(n)
            if played:
                if "said" not in meta:
                    raise HarnessError(f"{n} was taken with no script loaded: nothing was said on the way to it "
                                       "(give reread to run the script over every snap again)")
                reading.events = [("text", {"channel": e.get("channel") or "dialogue", "speaker": e.get("speaker") or "",
                                            "text": e.get("text") or ""}) for e in meta["said"]]
            try:
                with (_NoReader() if played else rd.Reader(read, platform=platform, game_id=Path(name).stem,
                                                         regions=_regions(path), data=blobs)) as reader:
                    try:
                        reader.load(source, name)
                        for tick in range(max(1, min(int(frames), 600))):
                            reader.frame(tick + 1)
                    except rd.ScriptError as e:
                        reading.error = str(e)
                    if not played:
                        reading.events = reader.take()
            except rd.ReaderUnavailable as e:
                raise HarnessError(str(e)) from None
            readings.append(reading)
            # All that was read: each text with its speaker's name, as the screen shows both.
            said = "\n".join(part for e in reading.story() for part in (e.get("speaker") or "", e["text"]) if part)
            unknown = meta.get("ocr_unavailable") or ("ocr" not in meta and not meta.get("no_text"))
            seen = None if unknown else list(meta.get("ocr") or [])
            if scrolls and seen is not None:
                # The rows after those the screen before left standing: the longest end of that screen's rows
                # that this one begins with. A scene's first screen gained all it shows.
                whole, before = seen, [] if n in first_of_scene else left
                tidy = lambda row: "".join(str(row).split())                                 # noqa: E731
                keep = next((k for k in range(min(len(before), len(whole)), 0, -1)
                             if [tidy(r) for r in before[-k:]] == [tidy(r) for r in whole[:k]]), 0)
                seen, left = whole[keep:], whole
            rows.append({"snap": n, "read": said, "seen": seen, "error": reading.error,
                         "written": bool(meta.get("memory_writes")), "reread": reread,
                         "agreement": None if seen is None else round(agreement(said, seen), 2)})
        lined, empty = [r for r in rows if r["seen"]], [r for r in rows if r["seen"] == []]
        out = {"script": {"snapshots": len(rows),
                          "reading something": sum(1 for r in rows if r["read"]),
                          "reading nothing": sum(1 for r in rows if not r["read"] and not r["error"]),
                          "the script didn't load": sum(1 for r in rows if r["error"]),
                          "with lines read off the screen": len(lined),
                          "of those, agreeing 80% or more": sum(1 for r in lined if r["agreement"] >= 0.8),
                          "marked as showing no text": len(empty),
                          "of those, reading nothing": sum(1 for r in empty if not r["read"]),
                          "taken after a memory write": sum(1 for r in rows if r["written"])}}
        if live:
            out["script"]["re-read offline"] = sum(1 for r in rows if r["reread"])
        if scenes:
            # The bar for calling a script checked (docs/first-script.md): thirty screens from play, over three
            # scenes or more, none after a memory write; nine in ten of those with text read as shown, and
            # nothing read where there is none.
            agreeing = sum(1 for r in lined if r["agreement"] >= 0.8)
            quiet = sum(1 for r in empty if not r["read"])
            short = []
            if len(lined) < 30:                             # a screen that shows no text isn't one of the thirty
                short.append(f"{len(lined)} screens with text ({len(empty)} more show none): 30 with text are wanted")
            if len(scenes) < 3:
                short.append(f"{len(scenes)} scene(s): 3 are wanted")
            if not live:
                short.append("not counted from play: give live")
            if reread:
                short.append("re-read offline: the bar counts what was said in play; run the stretch again")
            if any(r["written"] for r in rows):
                short.append("some snaps were taken after a memory write")
            if lined and agreeing < 0.9 * len(lined):
                short.append(f"{agreeing} of {len(lined)} screens with text agree: nine in ten are wanted")
            if quiet < len(empty):
                short.append(f"something was read on {len(empty) - quiet} screen(s) that show no text")
            by = {r["snap"]: r for r in rows}
            out["bar"] = {"met": not short, "short_of_it": short,
                          "screens with text": len(lined), "screens showing none": len(empty),
                          "re-read offline": sum(1 for r in rows if r["reread"]),
                          "scenes": {scene: {"screens": sum(1 for n in group if by[n]["seen"] is not None),
                                             "agreeing": sum(1 for n in group if by[n]["seen"] and by[n]["agreement"] >= 0.8)
                                             + sum(1 for n in group if by[n]["seen"] == [] and not by[n]["read"])}
                                     for scene, group in scenes.items()}}
        if show:
            out["rows"] = rows[:MAX_ROWS]
        if entry is not None:
            root = self.roots.inside("scratch", "work/catalog")
            for path, text in (tables or {}).items():
                target = self.roots.inside("scratch", f"work/catalog/{path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            try:
                result = cmp.compare_readings(entry, paths, readings, root, version=version or None)
            except (ValueError, KeyError, OSError) as e:
                raise HarnessError(f"the entry's layout couldn't be read: {e}") from None
            out["layout"] = result.counts()
            if show:
                out["differing"] = [{"snap": r.name, "layout": r.layout, "script": r.script}
                                    for r in result.rows if r.verdict == cmp.DIFFERENT][:MAX_ROWS]
        return out

    @tool("Runs a line of Lua with the script functions (mem, text, reader) on the running game and gives what "
          "it returns: mem.u32be(0x80000000), text.sjis(mem.cstr(0x80123400)).",
          code=("string", "an expression or a chunk", True))
    def lua(self, code: str) -> dict:
        machine = self._need()
        own = self.reader is None
        reader = self.reader or rd.Reader(machine.read, platform=machine.platform, regions=_Regions(machine))
        try:
            return {"result": reader.eval(code)}
        except rd.ScriptError as e:
            return {"error": str(e)}
        finally:
            if own:
                reader.close()

    @tool("The picture with the script's layouts drawn over it: a box round each character's cell. Shows at a "
          "glance whether a layout sits on the text.", scale=("number", "enlarge or shrink it", False))
    def overlay(self, scale: float = 1.0):
        from PIL import ImageDraw
        image = self._picture().convert("RGB")
        if not self.layouts:
            raise HarnessError("the script has reported no layout that is still up")
        draw = ImageDraw.Draw(image)
        for layout in self.layouts.values():
            sx, sy = image.width / layout["space"][0], image.height / layout["space"][1]
            box = lambda r: [r[0] * sx, r[1] * sy, (r[0] + r[2]) * sx - 1, (r[1] + r[3]) * sy - 1]      # noqa: E731
            if layout.get("box"):
                draw.rectangle(box(layout["box"]), outline=(0, 160, 255))
            for cell in layout["glyphs"]:
                if cell:
                    draw.rectangle(box(cell), outline=(255, 0, 160))
        return ("image", self._png(image, scale))

    def close(self) -> None:
        """Closes the script's reader and ends the core. A core's end may never come back: see mcp.finish."""
        self._close_reader()
        machine, self.machine = self.machine, None
        if machine is not None and hasattr(machine, "close"):
            machine.close()


class _NoReader:
    """Stands in for a reader where nothing is run: `compare` with what was said in play."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def load(self, *_):
        pass

    def frame(self, *_):
        pass


class _Regions:
    """A game's regions as the reader binding wants them, read when asked for: a mapping of name to bytes."""

    def __init__(self, machine):
        self.machine = machine

    def get(self, name: str):
        return self.machine.regions().get(name)


def tools(session: Session) -> dict:
    """name -> (description, properties, bound method), in the order they are written above."""
    out = {}
    for name, member in vars(Session).items():
        if hasattr(member, "tool") and (name != "poke" or session.allow_poke):
            out[name] = (*member.tool, getattr(session, name))
    return out
