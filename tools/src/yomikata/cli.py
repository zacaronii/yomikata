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
    p = sub.add_parser("locate", help="find OCR'd lines in RAM snapshots: in the standard encodings, else by their pattern of repeated characters")
    p.add_argument("snapshots", nargs="+", type=Path, help="<sha1>.ram.gz files from an inbox (their .json beside them)")
    p.add_argument("--show-hits", action="store_true", help="print each match's segment (script text: review only)")
    p.add_argument("--pattern", action="store_true", help="search by pattern even when a standard encoding matched")
    p = sub.add_parser("vram", help="PS1 video memory from a PCSX ReARMed savestate, saved as PNG views for finding fonts")
    p.add_argument("state", type=Path, help="<sha1>.state.gz from an inbox (or a raw savestate)")
    p.add_argument("--ram", type=Path, help="the snapshot's RAM (<sha1>.ram.gz), to confirm the layout")
    p.add_argument("-o", "--out", type=Path, required=True, help="prefix for <prefix>-rgb.png, <prefix>-nibbles.png, <prefix>.vram")
    p = sub.add_parser("replay", help="run a libretro core headless from a savestate; record RAM and frames (docs/replay.md)")
    p.add_argument("--core", type=Path, required=True, help="the libretro core (.so), e.g. pcsx_rearmed_libretro.so")
    p.add_argument("--game", type=Path, required=True, help="the game image the savestate belongs to")
    p.add_argument("--state", type=Path, required=True, help="savestate (<sha1>.state.gz from an inbox, or raw)")
    p.add_argument("--frames", type=int, default=120, help="frames to record")
    p.add_argument("--press", default="", help="buttons to press first, e.g. 'a' to advance a line (RetroPad names)")
    p.add_argument("--wait", type=int, default=10, help="frames to run before pressing")
    p.add_argument("-o", "--out", type=Path, required=True, help="folder for ram.npy, frames.npy and PNGs every 10 frames")
    p = sub.add_parser("layout", help="read an entry's layout from a RAM snapshot (checks a layout before a PR)")
    p.add_argument("entry", type=Path, help="games/<platform>/<id>.json")
    p.add_argument("ram", type=Path, help="RAM snapshot (<sha1>.ram.gz from an inbox, or raw)")
    p.add_argument("--base", default=None, help="address of the snapshot's first byte (default: its .json, else the platform's RAM)")
    p.add_argument("--version", help="exe_sha1 to pick the layout for (default: its .json's, else the only version of its id, else the general one)")
    p.add_argument("--root", type=Path, default=Path("."), help="catalog root, for table charsets")
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
        from .layout import RAM_BASE
        from .locate import find_standard, locate
        snaps, bases = [], []
        for p in args.snapshots:
            meta = json.loads(Path(str(p).replace(".ram.gz", ".json")).read_text(encoding="utf-8"))
            raw = p.read_bytes()
            snaps.append((gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw, meta["ocr"]))
            bases.append(int(meta["base_address"], 16) if meta.get("base_address") else RAM_BASE.get(meta.get("platform"), 0))
        standard = find_standard(snaps)
        for enc, hits in standard.items():
            print(f"== {enc}: {sum(len(h['segment']) for h in hits)} characters of the lines found as they are, at")
            for n, p in enumerate(args.snapshots):
                mine = sorted((h for h in hits if h["snapshot"] == n), key=lambda h: h["offset"])
                print(f"   {p.name.split('.')[0][:8]}: " + (" ".join(
                    f"0x{bases[n] + h['offset']:08X}" + (f"({h['segment']})" if args.show_hits else f"+{len(h['segment'])}")
                    for h in mine) or "nothing"))
        if standard and not args.pattern:
            print("A standard encoding matched, so the search by pattern (for a game's own table) was skipped; --pattern runs it.")
            return 0
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
    if args.cmd == "vram":
        import gzip
        from PIL import Image
        from .vram import extract_vram, views
        load = lambda p: (lambda b: gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b)(p.read_bytes())
        got = extract_vram(load(args.state), load(args.ram) if args.ram else None)
        args.out.with_suffix(".vram").write_bytes(got.vram)
        for name, arr in views(got.vram).items():
            Image.fromarray(arr).save(f"{args.out}-{name}.png")
        print(f"video memory at offset {got.vram_offset:#x}" + (f", RAM at {got.ram_offset:#x}" if got.ram_offset is not None else ""))
        return 0
    if args.cmd == "replay":
        import gzip
        import numpy as np
        from PIL import Image
        from .replay.libretro import Core
        args.out.mkdir(parents=True, exist_ok=True)
        core = Core(args.core, args.out / "system")
        core.load_game(args.game)
        raw = args.state.read_bytes()
        state = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        core.load_state(state)
        core.run(args.wait)
        if args.press:
            core.run(3, hold=set(args.press.split(",")))
        rams, frames = [], []
        for f in range(args.frames):
            core.run(1)
            rams.append(np.frombuffer(core.ram(), dtype=np.uint8).copy())
            frames.append(core.frame_rgb())
            if f % 10 == 0:
                Image.fromarray(frames[-1]).save(args.out / f"frame-{f:04d}.png")
        np.save(args.out / "ram.npy", np.stack(rams))
        np.save(args.out / "frames.npy", np.stack(frames))
        print(f"recorded {args.frames} frames (state {len(state)} bytes, core expects {core.state_size()})")
        return 0
    if args.cmd == "layout":
        from . import layout, tbl
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        meta_path = args.ram.with_name(args.ram.name.split(".")[0] + ".json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        lay = layout.pick(entry, args.version or meta.get("exe_sha1"), meta.get("id"))
        if lay is None:
            print("no layout for that version", file=sys.stderr)
            return 1
        base = args.base or meta.get("base_address")
        base = int(base, 16) if base else layout.RAM_BASE.get(entry["platform"])
        if base is None:
            print(f"give --base for {entry['platform']}", file=sys.stderr)
            return 1
        table = tbl.load([args.root / t for t in entry["tables"]]) if entry.get("tables") else None
        print(json.dumps(layout.read_layout(lay, entry, layout.Ram.load(args.ram, base), table), ensure_ascii=False, indent=1))
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
