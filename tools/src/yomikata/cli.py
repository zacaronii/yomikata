"""`yomikata` command line: identify images, validate the catalog, build the index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import catalog
from .disc import DiscError
from .identify import identify
from .layout import RAM_BASE


def _option(text: str) -> tuple[str, str]:
    key, eq, value = text.partition("=")
    if not (key and eq):
        raise argparse.ArgumentTypeError(f"{text!r} isn't KEY=VALUE")
    return key, value


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
    p = sub.add_parser("font", help="a font out of video memory as a labelled sheet (scratch), and a table skeleton to fill in")
    p.add_argument("vram", type=Path, help="video memory: a snapshot's vram part, or the .vram `yomikata vram` wrote (gzipped or not)")
    p.add_argument("-o", "--out", type=Path, required=True, help="the sheet (PNG). The game's font: a scratch folder")
    p.add_argument("--bpp", type=int, default=2, choices=(2, 4), help="tiles: bits a pixel")
    p.add_argument("--packed", action="store_true", help="tiles: a nibble a pixel (Game Boy Advance) instead of bit planes")
    p.add_argument("--base", help="tiles: where they start in video memory (hex). Without it, found from --cells")
    p.add_argument("--cells", type=Path, help="tiles: a RAM snapshot holding a text buffer of tile numbers (see `yomikata where`)")
    p.add_argument("--at", help="tiles: the buffer's address (hex)")
    p.add_argument("--count", type=int, default=64, help="tiles: how many 2-byte cells of the buffer to look at")
    p.add_argument("--ram-base", help="address of the RAM snapshot's first byte (default: its .json)")
    p.add_argument("--mask", default="0x3FF", help="tiles: the bits of a cell that are the tile number (default 0x3FF)")
    p.add_argument("--first", default="0", help="first code on the sheet (hex); for a grid, the first cell's code")
    p.add_argument("--last", help="tiles: last tile on the sheet (hex)")
    p.add_argument("--tall", type=int, help="tiles: characters are two tiles tall, the lower half this many tiles on")
    p.add_argument("--grid", help="PS1: X,Y,WIDTH,HEIGHT,COLUMNS,ROWS of the cells in the 4-bit view (`yomikata vram`'s -nibbles.png)")
    p.add_argument("--tbl", type=Path, help="write a table skeleton: a line per code, filled where --known has it")
    p.add_argument("--unit", type=int, default=2, help="table: bytes per code")
    p.add_argument("--big-endian", action="store_true", help="table: codes are high byte first in memory")
    p.add_argument("--known", type=Path, help="table: HEX=character lines already found (`yomikata where --table`, another table)")
    p.add_argument("--attributes", default="0", help="table: the list once per value OR'd into the codes, e.g. 0,0x400,0x1800")
    p = sub.add_parser("replay", help="run a libretro core headless, from a savestate or from power-on; record RAM and frames (docs/replay.md)")
    p.add_argument("--core", type=Path, required=True, help="the libretro core (.so), e.g. pcsx_rearmed_libretro.so")
    p.add_argument("--game", type=Path, required=True, help="the game image (the one the savestate belongs to)")
    p.add_argument("--state", type=Path, help="savestate (<sha1>.state.gz from an inbox, or raw); without one the game boots")
    p.add_argument("--system", type=Path, help="the core's system folder, e.g. with your console's BIOS (default: <out>/system)")
    p.add_argument("--option", action="append", default=[], metavar="KEY=VALUE", type=_option,
                   help="a core option, e.g. pcsx_rearmed_memcard1=libretro for a game that wants a memory card (repeatable)")
    p.add_argument("--steps", help="a script instead of --press/--frames, e.g. 'wait:600,start:4,30x(a:4,wait:40,snap)'")
    p.add_argument("--watch", default="", help="with --steps: addresses (hex, comma separated) whose byte is printed at each snap")
    p.add_argument("--platform", default="ps1", choices=sorted(RAM_BASE), help="whose RAM addresses --watch takes")
    p.add_argument("--frames", type=int, default=120, help="frames to record")
    p.add_argument("--press", default="", help="buttons to press first, e.g. 'a' to advance a line (RetroPad names)")
    p.add_argument("--wait", type=int, default=10, help="frames to run before pressing")
    p.add_argument("-o", "--out", type=Path, required=True, help="folder for ram.npy, frames.npy and PNGs every 10 frames; with --steps, for the snaps")
    p = sub.add_parser("contrast", help="bytes that hold one value in some RAM snapshots and never in the others (finds flags)")
    p.add_argument("--same", nargs="+", type=Path, required=True, help="snapshots where it holds (.ram.gz or raw)")
    p.add_argument("--other", nargs="+", type=Path, required=True, help="snapshots where it doesn't")
    p.add_argument("--platform", default="ps1", choices=sorted(RAM_BASE), help="whose RAM the snapshots are")
    p.add_argument("--limit", type=int, default=40)
    p = sub.add_parser("find", help="where a string stands in RAM snapshots, in the standard encodings")
    p.add_argument("text", help="text read off the frame (it isn't stored anywhere)")
    p.add_argument("snapshots", nargs="+", type=Path, help=".ram.gz or raw")
    p.add_argument("--absent", nargs="+", type=Path, default=[], help="snapshots of moments without the text: places that hold it there too are skipped")
    p.add_argument("--platform", default="ps1", choices=sorted(RAM_BASE), help="whose RAM the snapshots are")
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
        from .locate import find_standard, locate, propose
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
        if standard:
            charset, hits = next(iter(standard.items()))
            got = propose(snaps, hits, charset, bases[0]) if len(set(bases)) == 1 else None
            if got:
                rows = got["message"].get("rows")
                print("The lines stand in one fixed buffer. As a layout's `message`"
                      + (f" ({got['rows_seen']} of its {rows['count']} rows were seen with text; the rest were empty up to"
                         " where other data begins)" if rows and rows["count"] > got["rows_seen"] else "")
                      + (" (max_bytes is a guess: twice the longest line seen)" if not rows else "") + ":")
                print("   " + json.dumps(got["message"], ensure_ascii=False))
                print("   read back against OCR: " + ", ".join(
                    f"{p.name.split('.')[0][:8]} " + ("empty, the screen's text is elsewhere" if a is None else f"{a:.0%}")
                    + (f" (and {more} characters the screen didn't show: still typing?)" if more > 3 else "")
                    for p, a, more in zip(args.snapshots, got["agreement"], got["longer"])))
            else:
                print("No place was seen to change from one line to another, so the window's buffer can't be told from "
                      "script or backlog copies yet: snapshots of more lines will settle it.")
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
    if args.cmd == "font":
        import gzip
        from . import font
        load = lambda p: (lambda b: gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b)(p.read_bytes())
        vram, first = load(args.vram), int(args.first, 16)
        if args.grid:
            x, y, w, h, columns, rows = (int(v) for v in args.grid.split(","))
            cells = font.grid(vram, x, y, w, h, columns, rows)
            glyphs = {first + n: g for n, g in enumerate(cells)}
        else:
            mask, base, columns = int(args.mask, 16), int(args.base, 16) if args.base else None, 16
            if base is None:
                if not (args.cells and args.at):
                    print("give --base, or --cells and --at to find it", file=sys.stderr)
                    return 1
                meta_path = args.cells.with_name(args.cells.name.split(".")[0] + ".json")
                meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
                start = args.ram_base or meta.get("base_address")
                start = int(start, 16) if start else RAM_BASE.get(meta.get("platform"), 0)
                ram, at = load(args.cells), int(args.at, 16) - start
                used = [int.from_bytes(ram[at + 2 * i:at + 2 * i + 2], "little") & mask for i in range(args.count)]
                found = font.find_base(vram, used, args.bpp, args.packed)
                if not found:
                    print("no base fits those cells: wrong --bpp, or the buffer doesn't hold tile numbers", file=sys.stderr)
                    return 1
                ties = [b for b, score in found if score == found[0][1]]
                if len(ties) > 1:
                    print("these bases fit the buffer's cells equally well: " + ", ".join(f"0x{b:X}" for b in ties)
                          + ". Try each with --base; more cells (--count) with different characters may settle it",
                          file=sys.stderr)
                    return 1
                base = found[0][0]
                print(f"tiles at 0x{base:X} ({found[0][1]} of the buffer's tiles are different pictures there"
                      + (f"; next best 0x{found[1][0]:X} with {found[1][1]}" if len(found) > 1 else "") + ")")
            last = int(args.last, 16) if args.last else mask
            cut = font.tiles(vram, base, args.bpp, last + 1 + (args.tall or 0), args.packed)
            if args.tall:
                glyphs = {t: g for t, g in font.pair(cut[first:], args.tall).items()}
                glyphs = {first + t: g for t, g in glyphs.items() if first + t <= last}
            else:
                glyphs = {t: cut[t] for t in range(first, min(last + 1, len(cut)))}
        blanks = {code for code, g in glyphs.items() if font.blank(g)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        font.sheet(glyphs, columns).save(args.out)
        print(f"{len(glyphs)} glyphs, {len(blanks)} blank -> {args.out} (the game's font: don't commit it)")
        if args.tbl:
            known = font.known_pairs(args.known.read_text(encoding="utf-8"), args.unit, args.big_endian,
                                     None if args.grid else int(args.mask, 16)) if args.known else {}
            attributes = [int(a, 16) for a in args.attributes.split(",")]
            args.tbl.write_text(font.skeleton(sorted(glyphs), args.unit, known, blanks, attributes, args.big_endian), encoding="utf-8")
            print(f"{args.tbl}: {len(glyphs) - len(blanks)} codes to read, {sum(1 for c in glyphs if c in known)} already known")
        return 0
    if args.cmd == "replay":
        import gzip
        import numpy as np
        from PIL import Image
        from .replay.libretro import Core
        args.out.mkdir(parents=True, exist_ok=True)
        core = Core(args.core, args.system or args.out / "system", dict(args.option))
        core.load_game(args.game)
        state = b""
        if args.state:
            raw = args.state.read_bytes()
            state = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
            core.load_state(state)
        if args.steps:
            from .replay import steps
            base = RAM_BASE[args.platform]
            watch = {a: int(a, 16) - base for a in args.watch.split(",") if a}
            for line in steps.run(core, steps.parse(args.steps), args.out, watch):
                print(line)
            return 0
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
        print(f"recorded {args.frames} frames" + (f" (state {len(state)} bytes, core expects {core.state_size()})" if state else ""))
        return 0
    if args.cmd in ("contrast", "find"):
        from .contrast import contrast, find_text
        from .layout import Ram
        base = RAM_BASE[args.platform]
        if args.cmd == "find":
            absent = [Ram.load(p, base).data for p in args.absent]
            for p in args.snapshots:
                for charset, at in find_text(Ram.load(p, base).data, args.text, absent).items():
                    print(p.name, charset, " ".join(f"0x{base + o:08X}" for o in at))
            return 0
        hits = contrast([Ram.load(p, base).data for p in args.same], [Ram.load(p, base).data for p in args.other])
        for offset, value, others in hits[:args.limit]:
            print(f"0x{base + offset:08X} {value}, otherwise {' '.join(map(str, others[:8]))}")
        print(f"{len(hits)} byte(s)")
        return 0
    if args.cmd == "layout":
        from . import layout, tbl
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        meta_path = args.ram.with_name(args.ram.name.split(".")[0] + ".json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        lays = layout.layouts_for(entry, args.version or meta.get("exe_sha1"), meta.get("id"))
        if not lays:
            print("no layout for that version", file=sys.stderr)
            return 1
        base = args.base or meta.get("base_address")
        base = int(base, 16) if base else layout.RAM_BASE.get(entry["platform"])
        if base is None:
            print(f"give --base for {entry['platform']}", file=sys.stderr)
            return 1
        table = tbl.load([args.root / t for t in entry["tables"]]) if entry.get("tables") else None
        load = lambda paths: tbl.load([args.root / t for t in paths])
        ram = layout.Ram.load(args.ram, base)
        read = layout.read_layout(layout.live(lays, ram), entry, ram, table, load)
        print(json.dumps(read, ensure_ascii=False, indent=1))
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
