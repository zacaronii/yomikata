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
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .. import reader as rd
from .. import snapfile
from ..run import MIRRORS, PLATFORM_ALIASES
from . import search

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
BUTTONS = ("a", "b", "x", "y", "l", "r", "start", "select", "up", "down", "left", "right")
MAX_FRAMES, MAX_READ, MAX_HITS, MAX_EVENTS = 36000, 4096, 200, 300
MAX_STEPS, MAX_SHOTS, MAX_ROWS = 200, 48, 60
MAX_POKE = 256                  # bytes in one write
CHUNK = 30                      # frames run between two looks at the clock, when nothing is watched
CALL_SECONDS = 120.0            # how long one call may run the game before it stops and says so
STORY = ("dialogue", "choice")


class HarnessError(ValueError):
    """Something asked for can't be done; the message is for the agent."""


@dataclass
class Roots:
    games: Path
    cores: Path
    inbox: Path
    scratch: Path
    system: Path | None = None      # where a core looks for a BIOS, when it isn't scratch's own system/

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
        self.loaded = None                      # what load was last given: repeat loads it again from power-on
        self.once = False                       # a core was loaded that can't be loaded again in this process
        # The way here: where it starts ("power-on", or a state it can't see behind) and every move since, as
        # [frames, buttons, stick] or {"poke": address, "hex": bytes}. A waypoint keeps it.
        self.route: dict = {"base": "power-on", "moves": []}
        self.cursor_at: tuple[int, int, int] | None = None
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
            if len(out) < MAX_EVENTS and not (kind == "log" and event.get("level") == "info"):
                out.append({"kind": kind, "frame": self.machine.frames, **event})

    def begin_call(self) -> None:
        """A call starts now: what runs the game stops by itself `call_seconds` from here."""
        self.deadline = time.monotonic() + self.call_seconds

    def _late(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def _advance(self, frames: int, held: set[str], where: tuple[float, float], events: list, watch=None):
        """Runs up to `frames` frames, ticking the script after each. Stops early when `watch()` gives a reason
        after a frame, or when the call's time is up ("time"). Gives the frames run and the reason, or None."""
        machine, ran = self.machine, 0
        while ran < frames:
            if self._late():
                return ran, "time"
            step = 1 if (self.reader is not None or watch is not None) else min(CHUNK, frames - ran)
            machine.run(step, held, where)
            ran += step
            self._moved(step, held, where)
            if self.reader is not None:
                self.reader.frame(machine.frames)
                self._collect(self.reader.take(), events)
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
        return int.from_bytes(data, "big" if self.machine.platform in ("gc", "wii") else "little")

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

    @tool("Which release a game file is: its platform, its id, and what names its build (the SHA-1 of a disc's "
          "boot executable, a cartridge's header checksum). The id names the script's file; the build goes "
          "in its `-- reader: build` line.",
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
          core=("string", "the core's file under the cores folder, e.g. pcsx_rearmed_libretro.so", True),
          platform=("string", "the catalog's name for the console: ps1, gc, sfc, …", True),
          state=("string", "a state saved here (its name), or a path under the inbox's snapshots ending in .state.gz", False),
          options=("object", "core options, name to value", False))
    def load(self, game: str, core: str, platform: str, state: str = "", options: dict | None = None) -> dict:
        if self.make_machine is None:
            raise HarnessError("this harness has no cores")
        game_path, core_path = self.roots.inside("games", game), self.roots.inside("cores", core)
        if not game_path.is_file() or not core_path.is_file():
            raise HarnessError("no such game or core")
        if self.once:
            raise HarnessError("the core loaded before can't be followed by another load in the same harness: "
                               "start the harness again (states and snaps in scratch are kept)")
        self._close_reader()
        self.machine, self.snaps, self.pokes, self.first_snap = None, {}, 0, None
        platform = PLATFORM_ALIASES.get(platform, platform)         # ngc is the catalog's gc
        self.machine = self.make_machine(core_path, self._unpacked(game_path), platform,
                                         self.roots.system or self.roots.scratch / "system",
                                         {str(k): str(v) for k, v in (options or {}).items()})
        self.game, self.loaded = game, (game, core, platform, dict(options or {}))
        self.route = {"base": "power-on", "moves": []}
        self.core_hash = hashlib.sha256(core_path.read_bytes()).hexdigest()
        self.once = bool(getattr(self.machine, "once", False))
        if state:
            self.state_load(state)
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
          scale=("number", "each picture's scale on the sheet (default 1: text stays readable)", False))
    def steps(self, steps: list, prefix: str = "step", columns: int = 4, scale: float = 1.0,
              on: dict | None = None):
        machine, prefix = self._need(), self._name(prefix)
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
        out = {"frames": machine.frames, "steps": done, **({"stopped": "time", "left": left} if why == "time" else {}),
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
          size=("integer", f"how many bytes (at most {MAX_READ})", True),
          region=("string", "a named region to read by offset instead, e.g. vram", False),
          table=("string", "also read the bytes as text in the game's codes: a table's text (HEX=character lines)", False),
          rules=("array", "and/or by rules, worded as code_rule gives them: \"code = 0xA420 - JIS\", "
                          "\"(code & 0x1FFF) = 0x100 - ASCII\", \"code = JIS + 0x5CE0\"; the first that gives a "
                          "character is taken, so the narrowest first", False),
          unit=("integer", "bytes a code, for text: 1 or 2 (default 2)", False),
          big_endian=("boolean", "a code's byte order, for text (default true: as the bytes lie)", False),
          mask=("string", "bits of a code that say which character, hex, e.g. 1FFF: the rest are left out "
                          "before the table is asked", False))
    def read(self, address: str, size: int, region: str = "", table: str = "", rules: list | None = None,
             unit: int = 2, big_endian: bool = True, mask: str = "") -> dict:
        at, size = int(address, 16), max(0, min(int(size), MAX_READ))
        if region:
            data = self._need().regions().get(region)
            if data is None:
                raise HarnessError(f"the core gives out no {region}")
            return {"offset": hex(at), "hex": data[at:at + size].hex()}
        data = self._read(at, size)
        out = {"address": hex(at), "hex": data.hex()}
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
        for name, base, ram in self._memories(snaps):
            found = {}
            for kind, needle in needles.items():
                hits = search.places(ram, needle, MAX_HITS)
                if hits:
                    found[kind] = [f"0x{base + at:08X}" for at in hits]
            for part, its in parts.items():
                for kind, needle in its.items():
                    hits = search.places(ram, needle, 8)
                    if hits and kind not in found:
                        found.setdefault("pieces", {}).setdefault(kind, {})[part] = [f"0x{base + at:08X}" for at in hits]
            out[name] = found
        return {"found": out[""]} if not snaps else {"found": out}

    @tool("The stretches of memory that read as Japanese, longest first, with where each is and in which "
          "encoding: what to hold against the screen when you have no line to look for yet. Only standard "
          "encodings; a game with codes of its own shows nothing here (see find_relative).",
          least=("integer", "the fewest Japanese characters a stretch must have (default 8)", False),
          snaps=("array", "look in these snaps instead of live memory", False))
    def text_runs(self, least: int = 8, snaps: list | None = None) -> dict:
        out = {}
        for name, base, ram in self._memories(snaps):
            out[name] = [{"address": f"0x{base + at:08X}", "encoding": kind, "text": text[:120]}
                         for at, kind, text in search.runs(ram, max(4, int(least)), 40)]
        return {"runs": out[""]} if not snaps else {"runs": out}

    @tool("Looks for a line you read off the screen when the game's codes are its own: the places where the same "
          "characters have the same code and different ones different codes, each with the codes it would give "
          "the characters (the start of a table). One line needs repeated characters, the longer the better. "
          "Surer: `lines`, two or more snaps each with the line on its screen; one place must fit them all, and "
          "characters the lines share must have the same code. Write wide spaces where the screen has them.",
          text=("string", "the line on screen now; a line break stands for `gap` cells", False),
          lines=("object", "or several moments: a snap's name (or `now` for live memory) to the line on its screen", False),
          unit=("integer", "bytes a code: 1, 2 or 4 (default 1; try 2 as well)", False),
          order=("string", "unicode (kana as in JIS, small and voiced ones between) or gojuon (the 46 plain kana in a row)", False),
          stride=("integer", "codes are this many cells apart (default 1; 2 where each character has a second cell)", False),
          gap=("integer", "cells a line break in the line stands for (default 1: one code for the break)", False),
          big_endian=("boolean", "the byte order of 2- and 4-byte codes (default: the console's)", False),
          apart=("array", "or several lines of one scene, each wherever it lies in memory (a script read where "
                          "it is): two of them must share three characters or more. Kana stretches are safest", False),
          snap=("string", "with apart: the snap to look in (default: live memory)", False),
          window=("integer", "with apart: how far from the first line the others may lie, in bytes (default 65536)", False))
    def find_relative(self, text: str = "", lines: dict | None = None, unit: int = 1, order: str = "",
                      stride: int = 1, gap: int = 1, big_endian: bool | None = None, apart: list | None = None,
                      snap: str = "", window: int = 0x10000) -> dict:
        if apart:
            _, base, ram = self._memories([snap] if snap else None)[0]
            if big_endian is None:
                big_endian = getattr(self.machine, "platform", "") in ("gc", "wii")
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
        wanted = lines or {"now": text}
        memories, base = [], 0
        for name, line in wanted.items():
            _, base, ram = self._memories(None if name == "now" else [name])[0]
            memories.append((ram, str(line)))
        if big_endian is None:
            big_endian = getattr(self.machine, "platform", "") in ("gc", "wii")
        shape = lambda hits, unit: [{"address": f"0x{base + at:08X}",                          # noqa: E731
                                     "codes": {ch: f"{code:0{2 * unit}X}" for ch, code in codes.items()}}
                                    for at, codes in hits]
        try:
            total, hits = search.relative(memories, int(unit), bool(big_endian), order, int(stride), int(gap))
            out = {"count": total, "places": shape(hits, int(unit))}
        except search.SearchError as e:
            if not str(e).startswith("too little"):
                raise HarnessError(str(e)) from None
            total, out = 0, {"count": 0, "places": [], "note": str(e)}
        if not total:
            # The same line the other ways a game may keep it: codes of two bytes, the other byte order, kana
            # as far apart as in an order (up or down). Only the ways that fit a few places are given.
            ways = []
            for size in (1, 2):
                for big in ((False, True) if size > 1 else (False,)):
                    for by in ("", "unicode", "gojuon"):
                        if (size, big, by) == (int(unit), bool(big_endian), order) and "note" not in out:
                            continue
                        try:
                            count, some = search.relative(memories, size, big, by, int(stride), int(gap), 3)
                        except search.SearchError:
                            continue
                        if 0 < count <= 50:
                            ways.append({"unit": size, "big_endian": big, "order": by, "count": count,
                                         "places": shape(some, size)})
            if ways:
                out["other_ways"] = sorted(ways, key=lambda w: w["count"])
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
                                                      bool(big_endian), order, int(stride), int(gap), 5)
                    except search.SearchError:
                        continue
                    if count:
                        parts.append({"rows": list(range(i + 1, i + size + 1)), "count": count,
                                      "places": shape(some, int(unit))})
                if parts:
                    break
            out["parts"] = parts
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
        big = platform in ("gc", "wii")
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
        base, ram = machine.ram()
        regions = machine.regions()
        self.snaps[name] = Snap(base, machine.frames)
        folder = self.roots.scratch / "snaps"
        # All but the session's first are written as what differs from it (snapfile.py): a few kilobytes, not
        # all of memory again. Writing over the first makes this one the first.
        first = self.first_snap if self.first_snap and self.first_snap[0] != name else None
        if first is not None and not (folder / f"{first[0]}.ram.gz").is_file():
            first = None
        snapfile.write(folder / f"{name}.ram.gz", ram, (f"{first[0]}.ram.gz", first[1]) if first else None)
        for region, data in regions.items():
            other = (f"{first[0]}.{region}.gz", first[2][region]) if first and region in first[2] else None
            snapfile.write(folder / f"{name}.{region}.gz", data, other)
        if first is None:
            self.first_snap = (name, ram, regions)
        meta = {"platform": machine.platform, "base_address": f"0x{base:X}", "frame": machine.frames,
                "ocr": list(ocr or [])}
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
                **({"memory_writes": self.pokes} if self.pokes else {})}

    @tool("Says what a snap's screen showed, once you have read it off its picture: the lines, or that there is "
          "no text. `compare` holds what a script reads against these.",
          name=("string", "the snap's name", True),
          ocr=("array", "the lines on screen", False),
          no_text=("boolean", "true when the screen shows no text", False))
    def snap_lines(self, name: str, ocr: list | None = None, no_text: bool = False) -> dict:
        path = self.roots.inside("scratch", f"snaps/{self._name(name)}.json")
        if not path.is_file():
            raise HarnessError(f"no snap called {name}")
        if bool(ocr) == bool(no_text):
            raise HarnessError("give the lines, or no_text: true")
        meta = json.loads(path.read_text(encoding="utf-8"))
        for key in ("ocr_unavailable", "no_text"):
            meta.pop(key, None)
        meta["ocr"] = [str(line) for line in ocr or []]
        if no_text:
            meta["no_text"] = True
        path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return {"snap": name, "lines": len(meta["ocr"]), "no_text": bool(no_text)}

    @tool("The pictures of several snaps as one sheet, each under its name: many moments for one look.",
          snaps=("array", "snaps' names, in this session or written to scratch before", True),
          columns=("integer", "pictures across (default 4)", False),
          scale=("number", "each picture's scale (default 1: text stays readable)", False))
    def sheet(self, snaps: list, columns: int = 4, scale: float = 1.0):
        from PIL import Image
        if not snaps or len(snaps) > MAX_SHOTS:
            raise HarnessError(f"1 to {MAX_SHOTS} snaps")
        shots = []
        for name in snaps:
            path = self.roots.inside("scratch", f"snaps/{self._name(name)}.png")
            if not path.is_file():
                raise HarnessError(f"no picture was kept for {name}")
            shots.append((name, Image.open(path).convert("RGB")))
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
          "a pixel shown in greys.",
          x=("integer", "left edge, in words (default 0)", False), y=("integer", "top (default 0)", False),
          w=("integer", "width in words (default all)", False), h=("integer", "height (default all)", False),
          bits=("integer", "16 for colours (default), or 4 or 8: a texture's pixels", False),
          scale=("number", "enlarge or shrink it", False))
    def vram_picture(self, x: int = 0, y: int = 0, w: int = 1024, h: int = 512, bits: int = 16, scale: float = 1.0):
        import numpy as np
        from PIL import Image
        words = self._vram(x, y, w, h)
        if int(bits) == 16:
            image = Image.fromarray(np.stack([(words >> s & 31) << 3 for s in (0, 5, 10)], axis=2).astype(np.uint8))
        else:
            pixels = self._texels(words, int(bits))
            top = max(1, int(pixels.max()))
            image = Image.fromarray((pixels * (255 // top)).astype(np.uint8))
        return ("image", self._png(image, scale))

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
          stick=("boolean", "move with the left stick instead of the pad's arrows", False))
    def cursor_find(self, frames: int = 6, stick: bool = False) -> dict:
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
        base, start = machine.ram()
        shots = [start] + [moved(d)[1] for d in ("right", "left", "down", "up")]
        found = {}
        for size in (2, 4, 1):
            kind = {1: "u1", 2: "<i2", 4: "<i4"}[size] if machine.platform not in ("gc", "wii") else {1: "u1", 2: ">i2", 4: ">i4"}[size]
            cut = len(start) // size * size
            s0, r, l, d, u = (np.frombuffer(x[:cut], dtype=kind).astype(np.int64) for x in shots)
            xs = np.nonzero((r > s0) & (l < r) & (d == l) & (u == l))[0]
            ys = np.nonzero((r == s0) & (l == s0) & (d > l) & (u < d))[0]
            if len(xs) and len(ys):
                found = {"size": size, "x": [int(i) * size + base for i in xs[:8]], "y": [int(i) * size + base for i in ys[:8]],
                         "x_moved": {int(i) * size + base: int(r[i] - s0[i]) for i in xs[:8]},
                         "y_moved": {int(i) * size + base: int(d[i] - l[i]) for i in ys[:8]}}
                break
        if not found:
            raise HarnessError("no place followed the moves: is a cursor on screen, and does the pad move it?")
        # The pair that lie closest together: a cursor's x and y are kept side by side.
        x, y = min(((a, b) for a in found["x"] for b in found["y"]), key=lambda p: abs(p[0] - p[1]))
        self.cursor_at = (x, y, found["size"])
        return {"x_address": f"0x{x:08X}", "y_address": f"0x{y:08X}", "size": found["size"],
                "x": self._cell(x, found["size"]), "y": self._cell(y, found["size"]),
                "moved": {"right": found["x_moved"][x], "down": found["y_moved"][y], "frames": frames},
                "candidates": {"x": [f"0x{a:08X}" for a in found["x"]], "y": [f"0x{a:08X}" for a in found["y"]]},
                "frame": machine.frames}

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

    @tool("Runs the game and lists each change of a place in memory with its frame: how a pointer, a counter "
          "or a flag goes while something happens (a line types, a page turns).",
          address=("string", "the place, hex", True), frames=("integer", "how many frames to run", True),
          size=("integer", "bytes: 1, 2 or 4 (default 4)", False),
          buttons=("array", "buttons held for the first `hold` frames", False),
          hold=("integer", "frames the buttons are held (default 4)", False))
    def watch(self, address: str, frames: int, size: int = 4, buttons: list | None = None, hold: int = 4) -> dict:
        machine = self._need()
        if size not in (1, 2, 4) or not 0 < int(frames) <= MAX_FRAMES:
            raise HarnessError(f"size is 1, 2 or 4; 1 to {MAX_FRAMES} frames")
        at, held, events = int(address, 16), self._buttons(buttons), []
        changes, last = [], [self._cell(at, size)]
        first = last[0]

        def note():
            now = self._cell(at, size)
            if now != last[0]:
                last[0] = now
                if len(changes) < MAX_HITS:
                    changes.append({"frame": machine.frames, "value": None if now is None else f"0x{now:0{2 * size}X}"})
            return None
        why = None
        if held:
            ran, why = self._advance(max(1, min(int(hold), int(frames))), held, (0.0, 0.0), events, note)
        if not why:
            _, why = self._advance(int(frames) - (min(int(hold), int(frames)) if held else 0) or 1, set(), (0.0, 0.0),
                                   events, note)
        return {"frames": machine.frames, "from": None if first is None else f"0x{first:0{2 * size}X}",
                "changes": changes, **({"stopped": why} if why else {}),
                **({"events": events} if self.reader is not None else {})}

    @tool("Words in memory that point at an address, or into the stretch after it. With snaps, the same for "
          "each of them, a row for each word: which moments it points there in, and where. A word that "
          "points into a line in some moments only is what follows the line as it types or is shown.",
          address=("string", "the address pointed at, hex", True),
          span=("integer", "also pointers up to this many bytes past it (default 0)", False),
          big_endian=("boolean", "the console's byte order (default: true for gc and wii, else false)", False),
          snaps=("array", "look in these snaps instead of live memory", False))
    def refs(self, address: str, span: int = 0, big_endian: bool | None = None, snaps: list | None = None) -> dict:
        import numpy as np
        if big_endian is None:
            big_endian = self._need().platform in ("gc", "wii")
        target = int(address, 16)

        def pointing(base, ram):
            words = np.frombuffer(ram[:len(ram) // 4 * 4], dtype=">u4" if big_endian else "<u4")
            places = np.nonzero((words >= target) & (words <= target + max(0, int(span))))[0]
            return {base + 4 * int(i): int(words[i]) for i in places}

        if not snaps:
            found = pointing(*self._need().ram())
            return {"count": len(found), "refs": [{"at": f"0x{at:08X}", "points_at": f"0x{to:08X}"}
                                                  for at, to in list(found.items())[:MAX_HITS]]}
        each = {name: pointing(base, ram) for name, base, ram in self._memories(snaps)}
        rows = {}
        for name, found in each.items():
            for at, to in found.items():
                rows.setdefault(at, {})[name] = f"0x{to:08X}"
        # First those that point there in some moments only, or at another place from one to the next (a
        # cursor going along the line); last those that never move.
        order = sorted(rows, key=lambda at: (len(rows[at]) == len(each) and len(set(rows[at].values())) == 1,
                                             len(rows[at]), at))
        return {"count": len(rows), "snaps": len(each),
                "refs": [{"at": f"0x{at:08X}", "in": len(rows[at]), "points_at": rows[at]} for at in order[:MAX_HITS]]}

    @tool("The words from an address on, and where each that points into memory leads: the text that stands "
          "there, when it reads as text. For a stack or a record whose pointers are to strings.",
          address=("string", "where the words start, hex", True),
          count=("integer", "how many words (default 32, at most 256)", False),
          big_endian=("boolean", "the console's byte order (default: true for gc and wii, else false)", False))
    def pointed(self, address: str, count: int = 32, big_endian: bool | None = None) -> dict:
        machine = self._need()
        base, ram = machine.ram()
        if big_endian is None:
            big_endian = machine.platform in ("gc", "wii")
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
        core_file = self.roots.inside("cores", point["core"])
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
          live=("boolean", "count what the script said in play on the way to each snap (kept with the snap when "
                           "it was taken with a script loaded) instead of running it over the still snap: for a "
                           "script that reads a line only as it appears", False))
    def compare(self, source: str = "", name: str = "script.lua", data: dict | None = None, snaps: list | None = None,
                frames: int = 40, entry: dict | None = None, tables: dict | None = None, version: str = "",
                show: bool = False, live: bool = False) -> dict:
        from .. import compare as cmp
        from ..check import agreement, sidecars
        from ..run import Reading, _load, _regions
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
            read = rd.memory(int(meta["base_address"], 16), _load(path), MIRRORS.get(platform, ()))
            reading = Reading(n)
            if live:
                if "said" not in meta:
                    raise HarnessError(f"{n} was taken with no script loaded: nothing was said on the way to it")
                reading.events = [("text", {"channel": e.get("channel") or "dialogue", "speaker": e.get("speaker") or "",
                                            "text": e.get("text") or ""}) for e in meta["said"]]
            try:
                with (_NoReader() if live else rd.Reader(read, platform=platform, game_id=Path(name).stem,
                                                         regions=_regions(path), data=blobs)) as reader:
                    try:
                        reader.load(source, name)
                        for tick in range(max(1, min(int(frames), 600))):
                            reader.frame(tick + 1)
                    except rd.ScriptError as e:
                        reading.error = str(e)
                    if not live:
                        reading.events = reader.take()
            except rd.ReaderUnavailable as e:
                raise HarnessError(str(e)) from None
            readings.append(reading)
            # All that was read: each text with its speaker's name, as the screen shows both.
            said = "\n".join(part for e in reading.story() for part in (e.get("speaker") or "", e["text"]) if part)
            unknown = meta.get("ocr_unavailable") or ("ocr" not in meta and not meta.get("no_text"))
            seen = None if unknown else list(meta.get("ocr") or [])
            rows.append({"snap": n, "read": said, "seen": seen, "error": reading.error,
                         "written": bool(meta.get("memory_writes")),
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
        self._close_reader()
        self.machine = None


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
