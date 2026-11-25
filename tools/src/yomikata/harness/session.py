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
from dataclasses import dataclass, field
from pathlib import Path

from .. import reader as rd
from ..run import PLATFORM_ALIASES

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
CODECS = {"shift_jis": "cp932", "euc_jp": "euc_jp", "utf16le": "utf-16-le", "utf16be": "utf-16-be", "utf8": "utf-8"}
BUTTONS = ("a", "b", "x", "y", "l", "r", "start", "select", "up", "down", "left", "right")
MAX_FRAMES, MAX_READ, MAX_HITS, MAX_EVENTS = 36000, 4096, 200, 300


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
        if self.reader is None:
            machine.run(int(frames), held, where)
        else:                                   # a script is loaded: it is ticked after each frame
            for _ in range(int(frames)):
                machine.run(1, held, where)
                self.reader.frame(machine.frames)
                self._collect(self.reader.take(), events)
        return {"frames": machine.frames, **({"events": events} if self.reader is not None else {})}

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
