"""A script beside the layout it takes the place of: does it read the same from the same memory?

For each snapshot the entry's layout is read (layout.py, the reference) and the script is run (the reader
runtime), and what each gives is set side by side: the message against the dialogue the script emits, the
speaker's name, a choice's answers. The counts hold no text and are what a pull request quotes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .check import check
from .run import run

SAME, DIFFERENT, BOTH_EMPTY, FAILED = "the same", "different", "nothing in either", "the script didn't load"


@dataclass
class Row:
    name: str
    layout: dict                    # message, speaker, choices as the layout reads them
    script: dict                    # the same three from what the script emitted
    error: str | None = None

    @property
    def verdict(self) -> str:
        if self.error is not None:
            return FAILED
        if self.layout != self.script:
            return DIFFERENT
        return SAME if any(self.layout.values()) else BOTH_EMPTY

    def differing(self) -> list[str]:
        return [key for key in self.layout if self.layout[key] != self.script[key]]


@dataclass
class Result:
    rows: list[Row] = field(default_factory=list)

    def counts(self) -> dict:
        out = {"snapshots": len(self.rows)}
        for verdict in (SAME, BOTH_EMPTY, DIFFERENT, FAILED):
            out[verdict] = sum(1 for r in self.rows if r.verdict == verdict)
        return out

    @property
    def agrees(self) -> bool:
        return all(r.verdict in (SAME, BOTH_EMPTY) for r in self.rows)


def _from_layout(read: dict) -> dict:
    speaker = read.get("speaker")
    return {"message": read.get("message") or "", "speaker": speaker if isinstance(speaker, str) else "",
            "choices": list(read.get("choices") or [])}


def _from_script(texts: list[dict]) -> dict:
    said = [e for e in texts if e["channel"] == "dialogue"]
    answers = [line for e in texts if e["channel"] == "choice" for line in e["text"].split("\n")]
    return {"message": "\n".join(e["text"] for e in said), "speaker": said[-1]["speaker"] if said else "",
            "choices": answers}


def compare(script: Path, entry: dict, snapshots: list[Path], root: Path, base: int | None = None,
            version: str | None = None, frames: int = 2) -> Result:
    """Each snapshot through the entry's layout and through the script. A layout field the entry hasn't got
    (no speaker, no choices) isn't held against the script: only what the layout reads is compared."""
    reference = check(entry, snapshots, root, base, version)
    readings = run(script, snapshots, platform=entry["platform"], base=base, frames=frames)
    lays = entry.get("layouts", [])
    has = {key: any(key in lay for lay in lays) for key in ("speaker", "choices")}
    out = Result()
    for row, reading in zip(reference.rows, readings):
        layout, mine = _from_layout(row.read), _from_script(reading.texts())
        for key, there in has.items():
            if not there:
                layout.pop(key)
                mine.pop(key)
        out.rows.append(Row(row.name, layout, mine, reading.error))
    return out
