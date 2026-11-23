"""Scripted runs: a list of steps played on a core, with RAM and the frame saved where the script says.

    wait:600, start:4, wait:120, 30x(a:4, wait:40, snap), save:before-choice

`wait:N` runs N frames; `<button>:N` holds a button for N frames (`up+a:2` holds two); `snap` saves the frame and
RAM; `sheet` only adds the frame to the contact sheet; `save:<name>` writes a savestate; `Nx(…)` repeats. A core
is deterministic, so the same steps from the same start always reach the same moment: a script is a way to get
back to a line, and to sample it at chosen delays after a press."""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from pathlib import Path

from .libretro import JOYPAD

REPEAT = re.compile(r"(\d+)x\(")
NAME = re.compile(r"[A-Za-z0-9._-]+")
TILE = (320, 240)


@dataclass(frozen=True)
class Step:
    kind: str                           # wait, hold, snap, sheet, save
    frames: int = 0
    buttons: frozenset[str] = frozenset()
    name: str = ""


def _step(word: str) -> Step:
    name, _, arg = word.partition(":")
    if name in ("snap", "sheet") and not arg:
        return Step(name)
    if name == "save" and NAME.fullmatch(arg):
        return Step("save", name=arg)
    if arg.isdigit():
        if name == "wait":
            return Step("wait", int(arg))
        buttons = frozenset(name.split("+"))
        if buttons <= JOYPAD.keys():
            return Step("hold", int(arg), buttons)
    raise ValueError(f"not a step: {word!r}")


def _sequence(s: str, i: int, depth: int) -> tuple[list[Step], int]:
    out: list[Step] = []
    while i < len(s):
        if s[i] == ")":
            if not depth:
                raise ValueError("a `)` without its repeat")
            return out, i + 1
        m = REPEAT.match(s, i)
        if m:
            inner, i = _sequence(s, m.end(), depth + 1)
            out += inner * int(m[1])
        else:
            j = min(k for k in (s.find(",", i), s.find(")", i), len(s)) if k >= 0)
            out.append(_step(s[i:j]))
            i = j
        if i < len(s) and s[i] == ",":
            i += 1
    if depth:
        raise ValueError("a repeat isn't closed")
    return out, i


def parse(text: str) -> list[Step]:
    return _sequence("".join(text.split()), 0, 0)[0]


def run(core, steps: list[Step], out: Path, watch: dict[str, int] | None = None) -> list[str]:
    """Play the steps. Writes s000.png and s000.ram.gz per snap, <name>.state.gz per save, end.state.gz, and
    sheet.png with every snapped or sheeted frame in order. Returns one line per snap: the watched bytes
    (`watch` is label -> offset into RAM)."""
    from PIL import Image
    out.mkdir(parents=True, exist_ok=True)
    tiles, lines, n = [], [], 0
    for step in steps:
        if step.kind == "wait":
            core.run(step.frames)
        elif step.kind == "hold":
            core.run(step.frames, hold=set(step.buttons))
        elif step.kind == "save":
            (out / f"{step.name}.state.gz").write_bytes(gzip.compress(core.save_state(), 1))
        else:
            frame = core.frame_rgb()                # None until the core has drawn one (right after a savestate)
            image = Image.fromarray(frame) if frame is not None else Image.new("RGB", TILE)
            tiles.append(image.resize(TILE))
            if step.kind == "snap":
                ram = core.ram()
                image.save(out / f"s{n:03d}.png")
                (out / f"s{n:03d}.ram.gz").write_bytes(gzip.compress(ram, 1))
                video = core.video_ram() if hasattr(core, "video_ram") else None
                if video:                               # with it a snap is what a reader's snapshot is
                    (out / f"s{n:03d}.vram.gz").write_bytes(gzip.compress(video, 1))
                lines.append(" ".join([f"s{n:03d}"] + [f"{k}={ram[at]}" for k, at in (watch or {}).items()]))
                n += 1
    (out / "end.state.gz").write_bytes(gzip.compress(core.save_state(), 1))
    if tiles:
        cols = 4
        sheet = Image.new("RGB", (TILE[0] * cols, TILE[1] * -(-len(tiles) // cols)))
        for i, tile in enumerate(tiles):
            sheet.paste(tile, (TILE[0] * (i % cols), TILE[1] * (i // cols)))
        sheet.save(out / "sheet.png")
    return lines
