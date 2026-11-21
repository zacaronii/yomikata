"""Turn hook reports (docs/hook-finder.md, e.g. from an inbox's hooks/ folder) into catalog entries.

A report becomes part of an entry when its evidence is good enough (by default at least 5 lines checked against OCR
and 80% matching). New games get a new entry; an existing entry gains the hook, or the report's exe hash in an
existing hook's `versions`. Evidence pairs never leave the reports: the summary only counts."""

from __future__ import annotations

import json
from pathlib import Path

ORDER = ["id", "aliases", "platform", "title", "title_romaji", "versions", "encoding", "tables", "unit", "controls",
         "cleanup", "hooks", "status", "coverage", "sources", "notes"]


def find_entry(root: Path, platform: str, game_id: str) -> Path | None:
    p = root / "games" / platform / f"{game_id}.json"
    if p.exists():
        return p
    for q in sorted((root / "games" / platform).glob("*.json")):
        if game_id in json.loads(q.read_text(encoding="utf-8")).get("aliases", []):
            return q
    return None


def good_enough(r: dict, min_lines: int, min_rate: float) -> bool:
    ev = r["evidence"]
    return ev["lines_checked"] >= min_lines and ev["lines_matched"] >= min_rate * ev["lines_checked"]


def source_line(r: dict) -> str:
    who = " ".join(filter(None, [r.get("reporter", {}).get("app"), r.get("reporter", {}).get("version")])) or "a reader"
    emu = " ".join(filter(None, [(r.get("emulator") or {}).get("name"), (r.get("emulator") or {}).get("version")]))
    ev = r["evidence"]
    return (f"Hook {r['hook']['address']} ({r['hook']['register']}) found in play by {who}" + (f" in {emu}" if emu else "")
            + (f", disc version {r['disc_version']}" if r.get("disc_version") else "")
            + f": {ev['lines_matched']}/{ev['lines_checked']} lines matched OCR")


def merge(root: Path, reports_dir: Path, min_lines: int = 5, min_rate: float = 0.8) -> list[dict]:
    rows = []
    for f in sorted(Path(reports_dir).glob("*/*.json")):
        stored = json.loads(f.read_text(encoding="utf-8"))
        for r in stored["reports"]:
            row = {"id": r["id"], "address": r["hook"]["address"], "register": r["hook"]["register"],
                   "matched": f"{r['evidence']['lines_matched']}/{r['evidence']['lines_checked']}",
                   "disc_version": r.get("disc_version")}
            if not good_enough(r, min_lines, min_rate):
                rows.append({**row, "result": "skipped: not enough matching evidence"})
                continue
            hook = {k: v for k, v in r["hook"].items()}
            if r.get("exe_sha1"):
                hook["versions"] = [r["exe_sha1"]]
            path = find_entry(root, r["platform"], r["id"])
            if path is None:
                entry = {"id": r["id"], "platform": r["platform"], "title": r.get("title") or r["id"],
                         **({"versions": [{"exe_sha1": r["exe_sha1"], **({"note": f"disc version {r['disc_version']}"}
                                                                          if r.get("disc_version") else {})}]}
                            if r.get("exe_sha1") else {}),
                         "encoding": r["hook"]["charset"] if r["hook"]["charset"] != "table" else "unknown",
                         "hooks": [hook], "status": "auto", "sources": [source_line(r)]}
                path = root / "games" / r["platform"] / f"{r['id']}.json"
                result = "new entry"
            else:
                entry = json.loads(path.read_text(encoding="utf-8"))
                same = next((h for h in entry.get("hooks", [])
                             if h["address"].upper() == hook["address"].upper() and h["register"] == hook["register"]), None)
                if same:
                    if r.get("exe_sha1") and r["exe_sha1"] not in same.get("versions", []):
                        same["versions"] = same.get("versions", []) + [r["exe_sha1"]]
                        result = "hook confirmed on another version"
                    else:
                        rows.append({**row, "result": "already in the catalog"})
                        continue
                else:
                    entry["hooks"] = entry.get("hooks", []) + [hook]
                    result = "hook added"
                if r.get("exe_sha1") and r["exe_sha1"] not in [v.get("exe_sha1") for v in entry.get("versions", [])]:
                    entry["versions"] = entry.get("versions", []) + [
                        {"exe_sha1": r["exe_sha1"], **({"note": f"disc version {r['disc_version']}"} if r.get("disc_version") else {})}]
                entry["sources"] = entry.get("sources", []) + [source_line(r)]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({k: entry[k] for k in ORDER if k in entry}, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            rows.append({**row, "result": result})
    return rows


def summary(rows: list[dict]) -> str:
    out = ["| id | hook | disc version | matched OCR | result |", "|---|---|---|---|---|"]
    out += [f"| {r['id']} | {r['address']} {r['register']} | {r.get('disc_version') or ''} | {r['matched']} | {r['result']} |"
            for r in rows]
    return "\n".join(out)
