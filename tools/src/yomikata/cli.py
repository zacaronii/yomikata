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
    p = sub.add_parser("validate", help="check entries against the schema and the catalog rules")
    p.add_argument("--root", type=Path, default=Path("."))
    p = sub.add_parser("index", help="write index.json; with --check, fail if it's out of date")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "identify":
        status = 0
        for img in args.images:
            try:
                print(json.dumps({"image": img.name, **identify(img, args.scratch).as_dict()}, ensure_ascii=False))
            except (DiscError, OSError, ValueError) as e:
                print(json.dumps({"image": img.name, "error": str(e)}), file=sys.stderr)
                status = 1
        return status
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
