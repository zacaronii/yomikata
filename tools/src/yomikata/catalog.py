"""The catalog on disk: load and validate entries and tables, and build index.json."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from . import tbl
from .bundle import BundleError, bundle, header

SCRIPT_ID = re.compile(r"[A-Z0-9][A-Z0-9-]{3,19}")
SCRIPT_NEEDS = ("title", "reads", "version", "build", "no hooks")
DATA_CALL = re.compile(r"""reader\.data\(\s*(["'])([a-z0-9_]{1,16})\1\s*\)""")
# What a script here may not do (docs/script-api.md): stop the game at an instruction, or write its memory.
FORBIDDEN = {re.compile(r"\breader\.hook\b"): "reader.hook: a script here only reads memory",
             re.compile(r"\bmem\.w(8|16|32|64)\b"): "mem.w…: a script never writes the game's memory"}

# Entries, and scripts with their data files (docs/script-api.md); tables.
ALLOWED = {"games": {".json", ".lua", ".tbl", ".charset"}, "tables": {".tbl"}}


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

    # A script's own table (games/<platform>/<id>.tbl, docs/script-api.md) and the entry's (tables/<platform>/<id>.tbl)
    # are the same table for as long as both are kept: they may not drift apart.
    for p in sorted((root / "games").glob("*/*.tbl")):
        other = root / "tables" / p.parent.name / p.name
        if other.is_file() and other.read_bytes() != p.read_bytes():
            problems.append(Problem(str(p.relative_to(root)), f"differs from {other.relative_to(root)}"))

    for p in sorted([*(root / "tables").rglob("*.tbl"), *(root / "games").glob("*/*.tbl")]):
        try:
            tbl.parse(p.read_text(encoding="utf-8"), str(p.relative_to(root)))
        except (tbl.TableError, UnicodeDecodeError) as e:
            problems.append(Problem(str(p.relative_to(root)), str(e)))

    problems += validate_scripts(root)

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


def scripts(root: Path):
    """Each script's source file: games/<platform>/<id>.lua."""
    return sorted(Path(root).glob("games/*/*.lua"))


def script_data(root: Path, path: Path) -> dict[str, Path]:
    """The data files a script asks for by reader.data("name"): name -> games/<platform>/<id>.<name>."""
    names = sorted({m.group(2) for m in DATA_CALL.finditer(path.read_text(encoding="utf-8"))})
    return {name: path.with_suffix("." + name) for name in names}


def validate_scripts(root: Path) -> list[Problem]:
    """A script's form: its name, its header lines, that it bundles, that its data files are there, and that it
    asks for nothing a script here may not do. Whether it reads its game is for snapshots to say."""
    problems = []
    for path in scripts(root):
        rel = str(path.relative_to(root))
        source = path.read_text(encoding="utf-8")
        if not SCRIPT_ID.fullmatch(path.stem):
            problems.append(Problem(rel, "a script is named after its game's id: games/<platform>/<id>.lua"))
        keys = [key for key, _ in header(source)]
        for key in SCRIPT_NEEDS:
            if key not in keys:
                problems.append(Problem(rel, f"no `-- reader: {key}` line"))
        for pattern, why in FORBIDDEN.items():
            if pattern.search(source):
                problems.append(Problem(rel, why))
        try:
            bundle(path, root)
        except BundleError as e:
            problems.append(Problem(rel, str(e)))
        for name, data in script_data(root, path).items():
            if not data.is_file():
                problems.append(Problem(rel, f"reader.data(\"{name}\") needs {data.relative_to(root)}"))
    return problems


def build_scripts(root: Path) -> dict[str, str]:
    """What is published of the scripts: scripts/<platform>/<id>.lua -> the script with its modules in it."""
    root = Path(root)
    return {f"scripts/{p.parent.name}/{p.name}": bundle(p, root) for p in scripts(root)}


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
    index = {"format": 1, "entries": dict(sorted(out.items()))}
    listed = {}
    for path in scripts(root):
        published = f"scripts/{path.parent.name}/{path.name}"
        lines = header(path.read_text(encoding="utf-8"))
        first = lambda key: next((rest for k, rest in lines if k == key), "")        # noqa: E731
        listed[path.stem] = {
            "path": published, "sha256": hashlib.sha256(bundle(path, root).encode("utf-8")).hexdigest(),
            "platform": path.parent.name, "title": first("title"), "reads": first("reads"),
            "builds": [rest for k, rest in lines if k == "build"],
            "needs": sorted({word for k, rest in lines if k == "needs" for word in rest.replace(",", " ").split()}),
            "data": {name: {"path": str(data.relative_to(root)), "sha256": hashlib.sha256(data.read_bytes()).hexdigest()}
                     for name, data in script_data(root, path).items() if data.is_file()}}
    if listed:
        index["scripts"] = dict(sorted(listed.items()))
    return index


def stale(root: Path) -> list[str]:
    """What `yomikata index` would change: index.json, and published scripts that differ, are missing or are left
    over from a script that is gone."""
    root = Path(root)
    out = []
    current = (root / "index.json").read_text(encoding="utf-8") if (root / "index.json").exists() else ""
    if current != json.dumps(build_index(root), ensure_ascii=False, indent=1) + "\n":
        out.append("index.json")
    wanted = build_scripts(root)
    for rel, text in wanted.items():
        path = root / rel
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            out.append(rel)
    out += [str(p.relative_to(root)) for p in sorted(root.glob("scripts/*/*")) if str(p.relative_to(root)) not in wanted]
    return out


def write_index(root: Path) -> bool:
    """Write index.json; True if it changed."""
    root = Path(root)
    changed = bool(stale(root))
    path = root / "index.json"
    path.write_text(json.dumps(build_index(root), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    wanted = build_scripts(root)
    for rel, text in wanted.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    for left in root.glob("scripts/*/*"):               # a script that is gone takes its published file with it
        if str(left.relative_to(root)) not in wanted:
            left.unlink()
    return changed
