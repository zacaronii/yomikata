"""`yomikata` command line: identify images, validate the catalog, build the index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import catalog
from .disc import DiscError
from .identify import identify


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="yomikata", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("identify", help="print each image's platform, id and executable hash (JSON lines)")
    p.add_argument("images", nargs="+", type=Path)
    p.add_argument("--scratch", type=Path, help="where CHDs are unpacked (deleted after each image)")
    p = sub.add_parser("scan", help="sort disc images by how their script is stored (JSON lines; no text is kept)")
    p.add_argument("images", nargs="+", type=Path)
    p.add_argument("--scratch", type=Path, help="where CHDs are unpacked (deleted after each image)")
    p.add_argument("--samples", action="store_true", help="include the longest run per encoding (for review only)")
    p = sub.add_parser("draft", help="write draft entries from `scan` output (never overwrites); prints a summary")
    p.add_argument("results", type=Path, help="JSON lines from `yomikata scan` (a `path` field groups discs by folder)")
    p.add_argument("--titles", type=Path, help="JSON: folder or image name -> {title, title_romaji}")
    p.add_argument("--root", type=Path, default=Path("."))
    p = sub.add_parser("hooks", help="merge hook reports (an inbox's hooks/ folder) into entries; prints a summary")
    p.add_argument("reports", type=Path, help="folder with <platform>/<id>.json hook reports")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--min-lines", type=int, default=5)
    p.add_argument("--min-rate", type=float, default=0.8)
    p = sub.add_parser("locate", help="find OCR'd lines in RAM snapshots by their pattern of repeated characters")
    p.add_argument("snapshots", nargs="+", type=Path, help="<sha1>.ram.gz files from an inbox (their .json beside them)")
    p.add_argument("--show-hits", action="store_true", help="print each match's segment (script text: review only)")
    p = sub.add_parser("validate", help="check entries against the schema and the catalog rules")
    p.add_argument("--root", type=Path, default=Path("."))
    p = sub.add_parser("index", help="write index.json; with --check, fail if it's out of date")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--check", action="store_true")
    p = sub.add_parser("serve", help="run the sample inbox (needs the server extra; settings from YOMIKATA_* env)")
    p.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .inbox import serve
        serve(port=args.port)
        return 0

    if args.cmd == "identify":
        status = 0
        for img in args.images:
            try:
                print(json.dumps({"image": img.name, **identify(img, args.scratch).as_dict()}, ensure_ascii=False))
            except (DiscError, OSError, ValueError) as e:
                print(json.dumps({"image": img.name, "error": str(e)}), file=sys.stderr)
                status = 1
        return status
    if args.cmd == "scan":
        from .scan import scan_disc
        status = 0
        for img in args.images:
            try:
                rel = identify(img, args.scratch).as_dict()
                res = scan_disc(img, args.scratch).as_dict()
                if not args.samples:
                    res.pop("sample")
                print(json.dumps({"image": img.name, **rel, **res}, ensure_ascii=False))
            except (DiscError, OSError, ValueError) as e:
                print(json.dumps({"image": img.name, "error": str(e)}), file=sys.stderr)
                status = 1
        return status
    if args.cmd == "draft":
        from .draft import draft, summary
        results = [json.loads(l) for l in args.results.read_text(encoding="utf-8").splitlines() if l.strip()]
        titles = json.loads(args.titles.read_text(encoding="utf-8")) if args.titles else None
        print(summary(draft(results, args.root, titles)))
        return 0
    if args.cmd == "hooks":
        from .hooks import merge, summary
        print(summary(merge(args.root, args.reports, args.min_lines, args.min_rate)))
        return 0
    if args.cmd == "locate":
        import gzip
        from .locate import locate
        snaps = []
        for p in args.snapshots:
            meta = json.loads(Path(str(p).replace(".ram.gz", ".json")).read_text(encoding="utf-8"))
            raw = p.read_bytes()
            snaps.append((gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw, meta["ocr"]))
        for r in locate(snaps):
            print(f"== {r.encoding}: {len(r.hits)} matches, {len(r.mapping)} codes, {len(r.conflicts)} conflicts")
            print("   kana layout (code - gojuon position -> count):", r.gojuon_offsets())
            print("   mapping:", " ".join(f"{c:04X}={ch}" for c, ch in sorted(r.mapping.items())))
            for c in r.conflicts[:10]:
                print("   conflict", c)
            if args.show_hits:
                for h in r.hits[:30]:
                    print(f"   @{h['offset']:#08x} {h['segment']}")
        return 0
    if args.cmd == "validate":
        problems = catalog.validate(args.root)
        for pr in problems:
            print(pr, file=sys.stderr)
        n = sum(1 for _ in catalog.entries(args.root))
        print(f"{n} entries, {len(problems)} problem(s)")
        return 1 if problems else 0
    if args.cmd == "index":
        if args.check:
            current = (args.root / "index.json").read_text(encoding="utf-8") if (args.root / "index.json").exists() else ""
            wanted = json.dumps(catalog.build_index(args.root), ensure_ascii=False, indent=1) + "\n"
            if current != wanted:
                print("index.json is out of date: run `yomikata index`", file=sys.stderr)
                return 1
            return 0
        print("index.json updated" if catalog.write_index(args.root) else "index.json unchanged")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
