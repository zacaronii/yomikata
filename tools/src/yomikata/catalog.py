"""The catalog on disk: load and validate entries and tables, and build index.json."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from . import tbl

ALLOWED = {"games": {".json"}, "tables": {".tbl"}}


@dataclass
class Problem:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def entries(root: Path):
    for p in sorted((root / "games").glob("*/*.json")):
        yield p, json.loads(p.read_text(encoding="utf-8"))


def validate(root: Path) -> list[Problem]:
    root = Path(root)
    schema = json.loads((root / "schema" / "entry.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    problems: list[Problem] = []
    seen: dict[str, str] = {}

    # Only entries and tables live in games/ and tables/: no images, executables or script dumps.
    for folder, exts in ALLOWED.items():
        for p in (root / folder).rglob("*"):
            if p.is_file() and p.suffix not in exts and p.name != ".gitkeep":
                problems.append(Problem(str(p.relative_to(root)), f"only {', '.join(sorted(exts))} files belong here"))

    for p in sorted((root / "tables").rglob("*.tbl")):
        try:
            tbl.parse(p.read_text(encoding="utf-8"), str(p.relative_to(root)))
        except (tbl.TableError, UnicodeDecodeError) as e:
            problems.append(Problem(str(p.relative_to(root)), str(e)))

    for p in sorted((root / "games").glob("*/*.json")):
        rel = str(p.relative_to(root))
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            problems.append(Problem(rel, f"not valid JSON: {err}"))
            continue
        for err in sorted(validator.iter_errors(e), key=lambda x: list(x.path)):
            where = "/".join(map(str, err.path)) or "(entry)"
            problems.append(Problem(rel, f"{where}: {err.message}"))
        if not isinstance(e, dict):
            continue
        if p.stem != e.get("id") or p.parent.name != e.get("platform"):
            problems.append(Problem(rel, "file must be games/<platform>/<id>.json"))
        for i in [e.get("id")] + e.get("aliases", []):
            if i in seen:
                problems.append(Problem(rel, f"id {i} is also used by {seen[i]}"))
            seen[i] = rel
        for t in e.get("tables", []):
            if not (root / t).is_file():
                problems.append(Problem(rel, f"table {t} doesn't exist"))
        if e.get("tables") and "unit" not in e:
            problems.append(Problem(rel, "tables need a unit (bytes per code)"))
        if e.get("encoding") == "table" and not e.get("tables") and e.get("status") != "partial":
            problems.append(Problem(rel, "a table encoding needs tables, or status partial"))
        listed = {v.get(k) for v in e.get("versions", []) for k in ("exe_sha1", "header_crc32")}
        for n, lay in enumerate(e.get("layouts", [])):
            for v in lay.get("versions", []):
                if v not in listed:
                    problems.append(Problem(rel, f"layouts/{n}: version {v} isn't in the entry's versions"))
            sets = lay["tables"].get("sets") if isinstance(lay.get("tables"), dict) else None
            for t in sorted({t for paths in (sets or {}).values() for t in paths}):
                if not (root / t).is_file():
                    problems.append(Problem(rel, f"layouts/{n}: table {t} doesn't exist"))
            if sets and "unit" not in e:
                problems.append(Problem(rel, f"layouts/{n}: tables need a unit (bytes per code)"))
            texts = [lay.get(k) for k in ("message", "speaker", "choices")]
            if any(isinstance(t, dict) and t.get("charset") == "table" for t in texts) and not (e.get("tables") or sets):
                problems.append(Problem(rel, f"layouts/{n}: a table charset needs the entry's tables, or the layout's own"))
    return problems


def build_index(root: Path) -> dict:
    """id (and alias) -> entry path and SHA-256, so clients fetch one small file first and refetch only changes."""
    root = Path(root)
    out: dict[str, dict] = {}
    aliases: dict[str, dict] = {}
    for p, e in entries(root):
        rec = {"path": str(p.relative_to(root)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
               "platform": e["platform"], "encoding": e["encoding"], "status": e["status"]}
        out[e["id"]] = rec
        for a in e.get("aliases", []):
            aliases.setdefault(a, {**rec, "alias_of": e["id"]})
    for a, rec in aliases.items():                    # a main id always wins over another entry's alias
        out.setdefault(a, rec)
    return {"format": 1, "entries": dict(sorted(out.items()))}


def write_index(root: Path) -> bool:
    """Write index.json; True if it changed."""
    path = Path(root) / "index.json"
    text = json.dumps(build_index(root), ensure_ascii=False, indent=1) + "\n"
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old != text:
        path.write_text(text, encoding="utf-8")
    return old != text
