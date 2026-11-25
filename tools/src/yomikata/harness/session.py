"""What an agent can do with a game that is running: play it, look at it, search its memory, compare moments,
keep states, and try a script on it. Each tool is a method here; mcp.py offers them.

Everything it reads or writes on disk lies under one of four folders, and a path that leaves them is refused:
games and cores and the inbox's snapshots (read), and a scratch folder (written). What comes back holds game
text and pictures: for the agent's eyes and the scratch folder, never for the catalog."""

from __future__ import annotations

import gzip
import io
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import reader as rd
from ..run import MIRRORS, PLATFORM_ALIASES

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
CODECS = {"shift_jis": "cp932", "euc_jp": "euc_jp", "utf16le": "utf-16-le", "utf16be": "utf-16-be", "utf8": "utf-8"}
BUTTONS = ("a", "b", "x", "y", "l", "r", "start", "select", "up", "down", "left", "right")
MAX_FRAMES, MAX_READ, MAX_HITS, MAX_EVENTS = 36000, 4096, 200, 300
MAX_STEPS, MAX_SHOTS, MAX_ROWS = 200, 48, 60
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
    base: int
    ram: bytes
    frame: int
    regions: dict[str, bytes] = field(default_factory=dict)


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
        self.snaps: dict[str, Snap] = {}
        self.reader: rd.Reader | None = None
        self.script_name = None
        self.layouts: dict[str, dict] = {}
        self.call_seconds = CALL_SECONDS
        self.deadline: float | None = None      # set by whoever makes a call (mcp.py); None: no limit
        for folder in ("states", "snaps", "system", "saves"):
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
            if self.reader is not None:
                self.reader.frame(machine.frames)
                self._collect(self.reader.take(), events)
            why = watch() if watch is not None else None
            if why:
                return ran, why
        return ran, None

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
                "script": self.script_name}

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
        self._close_reader()
        self.machine, self.snaps = None, {}
        platform = PLATFORM_ALIASES.get(platform, platform)         # ngc is the catalog's gc
        self.machine = self.make_machine(core_path, game_path, platform,
                                         self.roots.system or self.roots.scratch / "system",
                                         {str(k): str(v) for k, v in (options or {}).items()})
        self.game = game
        if state:
            self.state_load(state)
        return self.status()

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
                          "still, with limit), times (repeat it, default 1), snap (true, or a name), shot (true)", True),
          prefix=("string", "names snaps taken with snap: true, as <prefix>-000, -001, … (default: step)", False),
          columns=("integer", "pictures across the sheet (default 4)", False),
          scale=("number", "each picture's scale on the sheet (default 1: text stays readable)", False))
    def steps(self, steps: list, prefix: str = "step", columns: int = 4, scale: float = 1.0):
        machine, prefix = self._need(), self._name(prefix)
        if not isinstance(steps, list) or not steps or not all(isinstance(s, dict) for s in steps):
            raise HarnessError("steps is a list of objects")
        known = {"buttons", "stick", "hold", "wait", "until", "times", "snap", "shot"}
        plan = []
        for step in steps:
            if set(step) - known:
                raise HarnessError(f"a step has: {', '.join(sorted(known))}")
            self._buttons(step.get("buttons"))
            if "until" in step and not (isinstance(step["until"], dict) and "limit" in step["until"]):
                raise HarnessError("until is an object with a limit, as run_until takes")
            plan += [step] * max(1, min(int(step.get("times", 1)), MAX_STEPS))
        if len(plan) > MAX_STEPS:
            raise HarnessError(f"at most {MAX_STEPS} steps in a call")
        events, done, shots, why, left = [], [], [], None, 0
        for number, step in enumerate(plan):
            held, where, before = self._buttons(step.get("buttons")), self._stick(step.get("stick")), len(events)
            row, why = {"step": number}, None
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
            elif not why and int(step.get("wait", 0)) > 0:
                _, why = self._advance(min(int(step["wait"]), MAX_FRAMES), set(), (0.0, 0.0), events)
            row["frames"] = machine.frames
            if len(events) > before:
                row["events"] = len(events) - before
            if why == "time":
                done.append({**row, "stopped": "time"})
                left = len(plan) - number           # this step and those after it
                break
            if step.get("snap"):
                name = step["snap"] if isinstance(step["snap"], str) else f"{prefix}-{number:03d}"
                row["snap"] = self.snap(name)["snap"]
            if step.get("shot") and len(shots) < MAX_SHOTS and machine.picture() is not None:
                shots.append((str(number), self._picture()))
            done.append(row)
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
          region=("string", "a named region to read by offset instead, e.g. vram", False))
    def read(self, address: str, size: int, region: str = "") -> dict:
        at, size = int(address, 16), max(0, min(int(size), MAX_READ))
        if region:
            data = self._need().regions().get(region)
            if data is None:
                raise HarnessError(f"the core gives out no {region}")
            return {"offset": hex(at), "hex": data[at:at + size].hex()}
        return {"address": hex(at), "hex": self._read(at, size).hex()}

    @tool("Where a text or some bytes stand in main memory.",
          text=("string", "the text to look for", False),
          hex=("string", "or bytes, as hex", False),
          encodings=("array", f"for a text: any of {', '.join(CODECS)} (default: all)", False))
    def find(self, text: str = "", hex: str = "", encodings: list | None = None) -> dict:
        base, ram = self._need().ram()
        needles = {}
        if hex:
            needles["hex"] = bytes.fromhex(hex)
        for name in (encodings or list(CODECS)) if text else ():
            if name not in CODECS:
                raise HarnessError(f"encodings are: {', '.join(CODECS)}")
            try:
                needles[name] = text.encode(CODECS[name])
            except UnicodeEncodeError:
                pass
        if not needles or not all(needles.values()):
            raise HarnessError("give a text or hex bytes to look for")
        out = {}
        for name, needle in needles.items():
            hits, at = [], ram.find(needle)
            while at >= 0 and len(hits) < MAX_HITS:
                hits.append(f"0x{base + at:08X}")
                at = ram.find(needle, at + 1)
            if hits:
                out[name] = hits
        return {"found": out}

    @tool("Keeps this moment's memory under a name, to compare with others, and writes it to scratch as an "
          "inbox-style snapshot (ram, video memory, the frame) that `yomikata run` and `check` take.",
          name=("string", "a name for it", True),
          ocr=("array", "the lines on screen, if you read them off the frame", False),
          no_text=("boolean", "true when the screen shows no text", False))
    def snap(self, name: str, ocr: list | None = None, no_text: bool = False) -> dict:
        machine, name = self._need(), self._name(name)
        base, ram = machine.ram()
        snap = self.snaps[name] = Snap(base, ram, machine.frames, machine.regions())
        folder = self.roots.scratch / "snaps"
        (folder / f"{name}.ram.gz").write_bytes(gzip.compress(ram, 1))
        for region, data in snap.regions.items():
            (folder / f"{name}.{region}.gz").write_bytes(gzip.compress(data, 1))
        meta = {"platform": machine.platform, "base_address": f"0x{base:X}", "frame": machine.frames,
                "ocr": list(ocr or [])}
        if no_text:
            meta["no_text"] = True
        elif not ocr:
            meta["ocr_unavailable"] = True
        (folder / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        if machine.picture() is not None:
            (folder / f"{name}.png").write_bytes(self._png(self._picture()))
        return {"snap": name, "frame": machine.frames, "bytes": len(ram), "regions": sorted(snap.regions)}

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

    @tool("Places in memory that differ between two snaps, narrowed by others: the same as the first in some, "
          "the same as the second in others. Finds a flag, a counter, a cursor.",
          a=("string", "a snap's name", True), b=("string", "another's", True),
          same_as_a=("array", "snaps where the place must hold what it holds in a", False),
          same_as_b=("array", "snaps where it must hold what it holds in b", False),
          unit=("integer", "bytes compared together: 1, 2 or 4 (default 1)", False))
    def changed(self, a: str, b: str, same_as_a: list | None = None, same_as_b: list | None = None,
                unit: int = 1) -> dict:
        import numpy as np
        if unit not in (1, 2, 4):
            raise HarnessError("unit is 1, 2 or 4")
        def cells(name):
            snap = self.snaps.get(name)
            if snap is None:
                raise HarnessError(f"no snap called {name}")
            data = np.frombuffer(snap.ram, dtype=np.uint8)
            return data[:len(data) // unit * unit].reshape(-1, unit)
        first, second = cells(a), cells(b)
        if first.shape != second.shape:
            raise HarnessError("the snaps aren't of the same memory")
        keep = (first != second).any(axis=1)
        for name in same_as_a or ():
            keep &= (cells(name) == first).all(axis=1)
        for name in same_as_b or ():
            keep &= (cells(name) == second).all(axis=1)
        places = np.nonzero(keep)[0]
        base = self.snaps[a].base
        return {"count": int(len(places)),
                "places": [{"address": f"0x{base + int(i) * unit:08X}", "a": first[i].tobytes().hex(),
                            "b": second[i].tobytes().hex()} for i in places[:MAX_HITS]]}

    @tool("Words in memory that point at an address, or into the stretch after it.",
          address=("string", "the address pointed at, hex", True),
          span=("integer", "also pointers up to this many bytes past it (default 0)", False),
          big_endian=("boolean", "the console's byte order (default: true for gc and wii, else false)", False))
    def refs(self, address: str, span: int = 0, big_endian: bool | None = None) -> dict:
        import numpy as np
        machine = self._need()
        base, ram = machine.ram()
        if big_endian is None:
            big_endian = machine.platform in ("gc", "wii")
        target = int(address, 16)
        words = np.frombuffer(ram[:len(ram) // 4 * 4], dtype=">u4" if big_endian else "<u4")
        places = np.nonzero((words >= target) & (words <= target + max(0, int(span))))[0]
        return {"count": int(len(places)),
                "refs": [{"at": f"0x{base + 4 * int(i):08X}", "points_at": f"0x{int(words[i]):08X}"}
                         for i in places[:MAX_HITS]]}

    # ---- states

    @tool("Saves the game's state under a name, in scratch.", name=("string", "a name for it", True))
    def state_save(self, name: str) -> dict:
        path = self._state_file(name)
        state = self._need().save_state()
        path.write_bytes(gzip.compress(state, 1))
        return {"state": name, "bytes": len(state)}

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
        if self.reader is not None:
            self.reader.state_loaded()
            self._collect(self.reader.take(), [])
        return {"loaded": name, "frames": machine.frames}

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
          show=("boolean", "also give what was read at each snap", False))
    def compare(self, source: str, name: str = "script.lua", data: dict | None = None, snaps: list | None = None,
                frames: int = 40, entry: dict | None = None, tables: dict | None = None, version: str = "",
                show: bool = False) -> dict:
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
            try:
                with rd.Reader(read, platform=platform, game_id=Path(name).stem, regions=_regions(path),
                               data=blobs) as reader:
                    try:
                        reader.load(source, name)
                        for tick in range(max(1, min(int(frames), 600))):
                            reader.frame(tick + 1)
                    except rd.ScriptError as e:
                        reading.error = str(e)
                    reading.events = reader.take()
            except rd.ReaderUnavailable as e:
                raise HarnessError(str(e)) from None
            readings.append(reading)
            said = "\n".join(e["text"] for e in reading.story())
            unknown = meta.get("ocr_unavailable") or ("ocr" not in meta and not meta.get("no_text"))
            seen = None if unknown else list(meta.get("ocr") or [])
            rows.append({"snap": n, "read": said, "seen": seen, "error": reading.error,
                         "agreement": None if seen is None else round(agreement(said, seen), 2)})
        lined, empty = [r for r in rows if r["seen"]], [r for r in rows if r["seen"] == []]
        out = {"script": {"snapshots": len(rows),
                          "reading something": sum(1 for r in rows if r["read"]),
                          "reading nothing": sum(1 for r in rows if not r["read"] and not r["error"]),
                          "the script didn't load": sum(1 for r in rows if r["error"]),
                          "with lines read off the screen": len(lined),
                          "of those, agreeing 80% or more": sum(1 for r in lined if r["agreement"] >= 0.8),
                          "marked as showing no text": len(empty),
                          "of those, reading nothing": sum(1 for r in empty if not r["read"])}}
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
        if hasattr(member, "tool"):
            out[name] = (*member.tool, getattr(session, name))
    return out
