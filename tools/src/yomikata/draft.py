"""Draft catalog entries from `yomikata scan` output: one entry per game (discs in the same folder are one game; the
first disc's id is the entry, the others are aliases), its encoding from the scan, status `auto`. Existing entries are
never overwritten. Prints a Markdown summary for the pull request (counts only, no text)."""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from . import __version__
from .kana import HIRAGANA_RULES

NOTES = {
    "compressed": "Most of the disc is compressed data and little script reads on it; the script is likely compressed "
                  "on disc. A memory sample from play will settle the encoding.",
    "unknown": "Little or no script reads on disc in a standard encoding: it may be compressed or use the game's own "
               "table. A memory sample from play will settle it.",
}
SINGLE_BYTE_NOTE = "Hiragana is stored as single bytes (the half-width katakana codes, voiced marks as bytes of " \
                   "their own); katakana and kanji are ordinary two-byte Shift-JIS. `cleanup` turns it back."


def group(results: list[dict]) -> "OrderedDict[str, list[dict]]":
    games: OrderedDict[str, list[dict]] = OrderedDict()
    for r in results:
        path = r.get("path") or r["image"]
        key = str(Path(path).parent) if "/" in path else path
        games.setdefault(key, []).append(r)
    return games


def draft(results: list[dict], root: Path, titles: dict | None = None, threshold: int = 2000) -> list[dict]:
    titles = titles or {}
    out = []
    for key, discs in group(results).items():
        discs = [d for d in discs if "id" in d]
        if not discs:
            continue
        main = discs[0]
        path = root / "games" / main["platform"] / f"{main['id']}.json"
        chars = {enc: max(d["chars"].get(enc, 0) for d in discs) for enc in discs[0]["chars"]}
        best = max(chars, key=chars.get)
        encoding = best if chars[best] >= threshold else \
            ("compressed" if any(d["encoding"] == "compressed" for d in discs) else "unknown")
        single = encoding == "shift_jis" and any(d.get("single_byte_hiragana") for d in discs)
        halfwidth = max(d.get("halfwidth_kana", 0) for d in discs)
        t = titles.get(Path(key).name, {}) or titles.get(Path(main["image"]).stem, {})
        entry = {"id": main["id"], **({"aliases": [d["id"] for d in discs[1:]]} if len(discs) > 1 else {}),
                 "platform": main["platform"], "title": t.get("title") or Path(key).name,
                 **({"title_romaji": t["title_romaji"]} if t.get("title_romaji") else {}),
                 "versions": [{**({"id": d["id"]} if len(discs) > 1 else {}), "exe": d["exe"], "exe_sha1": d["exe_sha1"]}
                              for d in discs if d.get("exe_sha1")],
                 "encoding": encoding, **({"cleanup": HIRAGANA_RULES} if single else {}), "status": "auto",
                 "sources": [f"yomikata scan {__version__} of the disc files: " +
                             ", ".join(f"{enc} {n:,}" for enc, n in chars.items() if n) + " characters of prose-like text" +
                             (f", {halfwidth:,} of them hiragana stored as single bytes" if single else "")
                             if any(chars.values()) else f"yomikata scan {__version__}: no prose-like text on disc"]}
        if encoding in NOTES:
            entry["notes"] = NOTES[encoding]
        if single:
            entry["notes"] = SINGLE_BYTE_NOTE
        written = not path.exists()
        if written:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        out.append({"id": main["id"], "title": entry["title"], "discs": len(discs), "encoding": encoding,
                    "chars": chars, "written": written})
    return out


def summary(rows: list[dict]) -> str:
    lines = ["| id | title | discs | encoding | prose chars on disc (SJIS / UTF-16 / UTF-8) | |", "|---|---|---|---|---|---|"]
    for r in rows:
        c = r["chars"]
        lines.append(f"| {r['id']} | {r['title']} | {r['discs']} | {r['encoding']} | "
                     f"{c.get('shift_jis', 0):,} / {c.get('utf16le', 0):,} / {c.get('utf8', 0):,} | "
                     f"{'new' if r['written'] else 'kept existing'} |")
    return "\n".join(lines)
