"""The sample inbox: readers (Genkan) upload memory samples here instead of handing files over by hand.

A sample pairs a line a reader OCR'd with the bytes found in memory at that moment, which is what pins down a game's
table when its script is compressed on disc. Samples contain script text, so they are kept in the inbox's own data
folder and never in the catalog repository.

POST /api/samples   one game's samples (Bearer token), merged with what's stored, duplicates dropped
GET  /api/samples   what's stored: per game, sample count and last upload (Bearer token)
POST /api/snapshots a RAM snapshot taken with a line on screen (multipart: meta JSON + ram file), stored per game
GET  /api/snapshots what's stored per game
POST /api/hooks     a hook a reader found and checked in play (docs/hook-finder.md); stored per game
GET  /api/hooks     the hook reports stored, per game
GET  /health

If the library is mounted, an upload without exe_sha1 gets it filled from the serial: the inbox identifies the
library's images once (cached by path, size and mtime) and looks the serial up."""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import zlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from .identify import identify

log = logging.getLogger(__name__)
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,19}$")
PLATFORMS = {"ps1", "ps2", "psp", "nds", "3ds", "gc", "wii", "saturn", "dc", "pce-cd", "sfc", "fc", "fds", "gba", "gb",
             "gbc", "n64", "md"}
# What a reader may call a platform, where it isn't the catalog's name (Genkan and RomM say ngc for the GameCube).
PLATFORM_ALIASES = {"ngc": "gc"}
# RomM-style library folders for each catalog platform (only these are identified)
LIBRARY_DIRS = {"ps1": "psx", "ps2": "ps2", "psp": "psp", "nds": "nds", "3ds": "3ds"}
IMAGE_EXT = {".chd", ".cue", ".iso", ".3ds", ".cci", ".nds", ".zip"}
MAX_BODY = 4 * 1024 * 1024
MAX_SAMPLES = 500                     # kept per game (oldest dropped first)


def write_json(p: Path, data: dict) -> None:
    """Write via a temporary file and swap it in, so a reader never sees half a file."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Where a game's own data can be: main RAM (with its usual mirrors). A match anywhere else (the BIOS ROM, I/O) is a
# coincidence, not the game's text, so such samples are refused with the reason.
RAM = {
    "ps1": [(0x00000000, 0x00200000), (0x80000000, 0x80200000), (0xA0000000, 0xA0200000), (0x1F800000, 0x1F800400)],
    "ps2": [(0x00000000, 0x02000000), (0x20000000, 0x22000000), (0x30000000, 0x32000000)],
    "psp": [(0x08000000, 0x0A000000), (0x48000000, 0x4A000000), (0x88000000, 0x8A000000)],
    "sfc": [(0x7E0000, 0x800000)],              # work RAM at its own bank addresses (not the low mirror in each bank)
    "gba": [(0x02000000, 0x02040000), (0x03000000, 0x03008000)],        # the 256 KB on the board, the 32 KB in the CPU
    "gb": [(0xC000, 0xE000)],
    "gbc": [(0xC000, 0xE000)],                  # as the game sees it: the bank switched in at 0xD000
    "nds": [(0x02000000, 0x02400000)],
    "gc": [(0x80000000, 0x81800000), (0xC0000000, 0xC1800000)],        # MEM1, and the same 24 MB uncached
    "wii": [(0x80000000, 0x81800000), (0xC0000000, 0xC1800000)],       # MEM1, as the GameCube's
    # The high 1 MB of work RAM, and the same past the cache. (The low 1 MB is the part `lwram`, below.)
    "saturn": [(0x06000000, 0x06100000), (0x26000000, 0x26100000)],
    "dc": [(0x8C000000, 0x8D000000), (0x0C000000, 0x0D000000), (0xAC000000, 0xAD000000)],   # 16 MB, two mirrors
    # The Super System Card's 192 KB and the CD's own 64 KB after it, read as one (the console's 8 KB: `wram`).
    "pce-cd": [(0x0D0000, 0x110000)],
    # The 8 MB of RAM (the Expansion Pak's included), and the same uncached. Kept in the console's byte order
    # (big-endian), as the harness reads it.
    "n64": [(0x80000000, 0x80800000), (0xA0000000, 0xA0800000)],
}


# Optional parts kept with a snapshot, and their size limits once uncompressed.
EXTRA_PARTS = {"state": 64 * 1024 * 1024,      # the emulator's savestate (holds video memory on PCSX ReARMed;
                                               # PPSSPP's is 40 MB: the 32 MB of RAM and the rest of the machine)
               "vram": 2 * 1024 * 1024,        # video memory, when the core gives it out
               "screen": 4 * 1024 * 1024}      # the frame on screen (PNG)
# Memory a platform keeps beside main memory, as the harness's snaps keep it (`<name>.<region>.gz`, read at its
# console address by `yomikata run` and the harness): sent as a part of that name, at most this many bytes.
REGION_PARTS = {"saturn": {"lwram": 0x100000},          # the low 1 MB of work RAM (0x00200000)
                "gb": {"io": 0x100}, "gbc": {"io": 0x100},  # the I/O registers and high RAM (0xFF00-0xFFFF)
                "pce-cd": {"wram": 0x2000}}             # the console's own 8 KB (0x1F0000)
REGION_NAMES = sorted({name for parts in REGION_PARTS.values() for name in parts})
# The largest RAM snapshot accepted per platform (main RAM size), and how many are kept per game.
SNAPSHOT_MAX = {"ps1": 0x200000, "ps2": 0x2000000, "psp": 0x2000000, "sfc": 0x20000, "gba": 0x40000,
                "gb": 0x2000, "gbc": 0x2000, "nds": 0x400000, "gc": 0x1800000, "wii": 0x1800000,
                "saturn": 0x100000, "dc": 0x1000000, "pce-cd": 0x40000, "n64": 0x800000}
# Video memory is smaller than the general limit on most consoles; a part larger than the console has is refused.
# (A core may hand out a Game Boy game the Color's 16 KB: the cap is the larger.)
VRAM_MAX = {"ps1": 0x100000, "sfc": 0x10000, "gba": 0x18000, "gb": 0x4000, "gbc": 0x4000, "nds": 0xA4000,
            "pce-cd": 0x10000}
# A savestate holds the whole machine. Dolphin's is 92-93 MB (17 MB gzipped): the GameCube's cap is its own.
STATE_MAX = {"gc": 128 * 1024 * 1024, "wii": 128 * 1024 * 1024}
VRAM_FORMAT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
SNAPSHOTS_PER_GAME = 40


def gunzip(data: bytes, limit: int, name: str) -> bytes:
    """`data` uncompressed if it is gzipped, stopping at `limit` bytes so a small upload can't unpack into a huge
    one. A result of limit + 1 bytes means "too large" to the caller's size check."""
    if data[:2] != b"\x1f\x8b":
        return data
    unpacker = zlib.decompressobj(wbits=31)
    try:
        out = unpacker.decompress(data, limit + 1)
    except zlib.error:
        out = None
    if out is None or (len(out) <= limit and not unpacker.eof):      # broken, or cut short
        raise ValueError(f"{name} looks gzipped but doesn't decompress")
    return out


GC_MAGIC = bytes.fromhex("C2339F3D")           # at 0x1C of a GameCube disc's header


def gamecube_header(meta: dict, ram: bytes) -> dict:
    """A GameCube keeps its disc's header at the start of memory: the id, the disc number and version, a magic
    word. A dump that starts there must show them, which also proves its bytes are in the console's order (a core
    on a little-endian device may hand memory out with each word turned round). Gives what to add to the meta."""
    if int(meta["base_address"], 16) & 0x3FFFFFFF or len(ram) < 0x20:
        return {}
    if ram[0x1C:0x20] == GC_MAGIC[::-1]:
        raise ValueError("ram has each 32-bit word turned round: send GameCube memory in the console's own byte "
                         "order (big-endian), as the game reads it")
    if ram[0x1C:0x20] != GC_MAGIC:
        raise ValueError("ram doesn't start with a GameCube disc header (no C2339F3D at 0x1C): is it MEM1 from "
                         "0x80000000, taken while a game runs?")
    found = ram[:6].decode("ascii", "replace")
    if found != meta["id"]:
        raise ValueError(f"meta.id is {meta['id']} but the memory is {found}'s (the six characters at its start)")
    return {"disc_number": ram[6], "disc_version": ram[7]}


def clean_snapshot_meta(meta: object) -> dict:
    if not isinstance(meta, dict):
        raise ValueError("meta must be a JSON object")
    game_id, platform = meta.get("id"), meta.get("platform")
    platform = PLATFORM_ALIASES.get(platform, platform) if isinstance(platform, str) else platform
    if not isinstance(game_id, str) or not ID_RE.fullmatch(game_id):
        raise ValueError("meta.id must be a release id such as SLPS-01001")
    if platform not in SNAPSHOT_MAX:
        raise ValueError(f"snapshots are taken for: {', '.join(sorted(SNAPSHOT_MAX))}")
    base = meta.get("base_address")
    if not isinstance(base, str) or not in_ram(platform, base):
        raise ValueError(f"meta.base_address must be the start of {platform} main RAM as hex, e.g. 0x80000000")
    lines = meta.get("ocr")
    if isinstance(lines, str):
        lines = [lines]
    no_text = meta.get("no_text") is True           # said outright, so a failed OCR can't pass for an empty screen
    # The reader read no line, which isn't the same as there being none: what is on screen isn't known.
    unavailable = meta.get("ocr_unavailable") is True
    if no_text and unavailable:
        raise ValueError("meta.no_text says the screen shows no text and meta.ocr_unavailable that it isn't known: "
                         "send one of them")
    if (no_text or unavailable) and lines not in ([], None):
        raise ValueError(f"meta.{'no_text' if no_text else 'ocr_unavailable'} goes with an empty meta.ocr")
    if not (no_text or unavailable) and (not isinstance(lines, list) or not lines or len(lines) > 10 or
                                         not all(isinstance(x, str) and x.strip() and len(x) <= 400 for x in lines)):
        raise ValueError("meta.ocr must be the line(s) on screen: 1-10 strings of at most 400 characters "
                         "(or no_text: true with none, for a screen that shows no text; or ocr_unavailable: true "
                         "with none, when no line could be read)")
    out = {"id": game_id, "platform": platform, "base_address": "0x" + base[2:].upper() if base.startswith("0x") else base,
           "ocr": lines or []}
    if no_text:
        out["no_text"] = True
    if unavailable:
        out["ocr_unavailable"] = True
    fmt = meta.get("vram_format")
    if fmt is not None:
        if not isinstance(fmt, str) or not VRAM_FORMAT_RE.fullmatch(fmt):
            raise ValueError("meta.vram_format must be a short name in lowercase letters, digits and hyphens")
        out["vram_format"] = fmt
    for k in ("title", "genkan_version", "captured_at", "exe_sha1", "note", "app", "emulator"):
        v = meta.get(k)
        if isinstance(v, (str, int, float)) and len(str(v)) <= 200:
            out[k] = v
    return out


def in_ram(platform: str, address: object) -> bool:
    if platform not in RAM or not isinstance(address, str):
        return True                                   # no rule for this platform, or no address given
    try:
        a = int(address, 16)
    except ValueError:
        return False
    return any(lo <= a < hi for lo, hi in RAM[platform])


def clean_sample(s: object) -> dict:
    if not isinstance(s, dict):
        raise ValueError("each sample must be an object")
    ocr = s.get("ocr")
    raw = s.get("bytes_hex")
    if not isinstance(ocr, str) or not ocr.strip() or len(ocr) > 400:
        raise ValueError("sample.ocr must be a non-empty string (at most 400 characters)")
    if not isinstance(raw, str) or not re.fullmatch(r"(?:[0-9A-Fa-f]{2})+", raw) or len(raw) > 16384:
        raise ValueError("sample.bytes_hex must be hex (at most 8 KB of bytes)")
    out = {"ocr": ocr, "bytes_hex": raw.lower()}
    for k, v in s.items():                         # keep the reader's extra fields (address, unit, probe, …) if small
        if k not in out and isinstance(v, (str, int, float, bool)) and len(str(v)) <= 200:
            out[k] = v
    return out


def sample_key(s: dict) -> str:
    return hashlib.sha1(f"{s['ocr']}\0{s.get('address', '')}\0{s['bytes_hex']}".encode()).hexdigest()


class Library:
    """Serial -> exe hash for the images in a mounted library, identified once and cached."""

    def __init__(self, root: Path | None, cache: Path, scratch: Path | None = None):
        self.root, self.cache, self.scratch = root, cache, scratch
        self.lock = threading.Lock()
        self.known = json.loads(cache.read_text()) if cache.exists() else {}

    def lookup(self, platform: str, serial: str) -> dict | None:
        if not self.root or platform not in LIBRARY_DIRS:
            return None
        folder = self.root / LIBRARY_DIRS[platform]
        with self.lock:
            for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXT):
                st = path.stat()
                key = str(path.relative_to(self.root))
                rec = self.known.get(key)
                if not rec or rec.get("size") != st.st_size or rec.get("mtime") != int(st.st_mtime):
                    try:
                        r = identify(path, self.scratch).as_dict()
                    except Exception as e:                  # not identifiable: remember that too
                        r = {"error": str(e)[:200]}
                    rec = {"size": st.st_size, "mtime": int(st.st_mtime), **r}
                    self.known[key] = rec
                    self.cache.write_text(json.dumps(self.known, indent=1))
                if rec.get("id") == serial:
                    return rec
        return None


CHARSETS = {"shift_jis", "utf16le", "utf8", "table"}


def clean_hook_report(body: object) -> dict:
    """A hook report (docs/hook-finder.md), checked and normalised. Evidence lines stay in the inbox only."""
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")
    game_id, platform = body.get("id"), body.get("platform")
    if not isinstance(game_id, str) or not ID_RE.fullmatch(game_id):
        raise ValueError("id must be a release id such as NPJH50372")
    if platform not in PLATFORMS:
        raise ValueError(f"platform must be one of: {', '.join(sorted(PLATFORMS))}")
    hook = body.get("hook")
    if not isinstance(hook, dict):
        raise ValueError("hook is required: {address, register, charset}")
    address, register, charset = hook.get("address"), hook.get("register"), hook.get("charset")
    if not isinstance(address, str) or not re.fullmatch(r"0x[0-9A-Fa-f]{1,16}", address):
        raise ValueError("hook.address must be hex like 0x088540B0")
    if not isinstance(register, str) or not re.fullmatch(r"[a-z0-9]{1,4}", register):
        raise ValueError("hook.register must be a register name like a0")
    if charset not in CHARSETS:
        raise ValueError(f"hook.charset must be one of: {', '.join(sorted(CHARSETS))}")
    cleanup = hook.get("cleanup", [])
    if not isinstance(cleanup, list) or len(cleanup) > 30:
        raise ValueError("hook.cleanup must be a list of cleanup rules (docs/format.md)")
    ev = body.get("evidence")
    if not isinstance(ev, dict):
        raise ValueError("evidence is required: {lines_checked, lines_matched}")
    checked, matched = ev.get("lines_checked"), ev.get("lines_matched")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (checked, matched)) or matched > checked:
        raise ValueError("evidence.lines_checked / lines_matched must be counts, matched <= checked")
    pairs = ev.get("pairs", [])
    if not isinstance(pairs, list) or len(pairs) > 20 or not all(
            isinstance(p, dict) and all(isinstance(p.get(k, ""), str) and len(p.get(k, "")) <= 300 for k in ("ocr", "hook"))
            for p in pairs):
        raise ValueError("evidence.pairs: at most 20 {ocr, hook} pairs of short strings")
    reporter = body.get("reporter") or {}
    out = {"id": game_id, "platform": platform,
           "hook": {"address": "0x" + address[2:].upper(), "register": register, "charset": charset,
                    **({"cleanup": cleanup} if cleanup else {})},
           "evidence": {"lines_checked": checked, "lines_matched": matched, "pairs": pairs},
           "reporter": {k: str(reporter.get(k))[:80] for k in ("app", "version") if reporter.get(k)}}
    for k in ("disc_version", "title", "emulator", "exe_sha1", "notes"):
        v = body.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:300]
        elif k == "emulator" and isinstance(v, dict):
            out[k] = {kk: str(vv)[:80] for kk, vv in v.items() if kk in ("name", "version")}
    return out


class Inbox:
    def __init__(self, data: Path, library: Library | None = None):
        self.data, self.library = data, library
        self.lock = threading.Lock()

    def path(self, platform: str, game_id: str) -> Path:
        return self.data / "samples" / platform / f"{game_id}.json"

    def add(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("expected a JSON object")
        game_id, platform = body.get("id"), body.get("platform")
        platform = PLATFORM_ALIASES.get(platform, platform) if isinstance(platform, str) else platform
        if not isinstance(game_id, str) or not ID_RE.fullmatch(game_id):
            raise ValueError("id must be a release id such as SLPS-01001")
        if platform not in PLATFORMS:
            raise ValueError(f"platform must be one of: {', '.join(sorted(PLATFORMS))}")
        samples = body.get("samples")
        if not isinstance(samples, list) or not samples or len(samples) > MAX_SAMPLES:
            raise ValueError(f"samples must be a non-empty list (at most {MAX_SAMPLES})")
        new = [clean_sample(s) for s in samples]
        outside = [s["address"] for s in new if not in_ram(platform, s.get("address"))]
        if outside:
            raise ValueError(f"sample address {outside[0]} is outside {platform} main RAM (e.g. the BIOS ROM): "
                             "a match there is a coincidence, not the game's text")
        with self.lock:
            p = self.path(platform, game_id)
            stored = json.loads(p.read_text(encoding="utf-8")) if p.exists() else \
                {"id": game_id, "platform": platform, "samples": [], "uploads": []}
            for k in ("title", "romm_rom_id", "genkan_version", "exe_sha1"):
                if body.get(k):
                    stored[k] = body[k]
            seen = {sample_key(s) for s in stored["samples"]}
            added = 0
            for s in new:
                k = sample_key(s)
                if k not in seen:
                    stored["samples"].append({**s, "received_at": now()})
                    seen.add(k)
                    added += 1
            stored["samples"] = stored["samples"][-MAX_SAMPLES:]
            stored["uploads"] = (stored["uploads"] + [{"at": now(), "sent": len(new), "added": added}])[-50:]
            write_json(p, stored)
        if not stored.get("exe_sha1") and self.library:
            threading.Thread(target=self.fill_hash, args=(platform, game_id), daemon=True).start()
        return {"ok": True, "id": game_id, "added": added, "stored": len(stored["samples"])}

    def add_snapshot(self, meta: dict, ram: bytes, extras: dict[str, bytes] | None = None) -> dict:
        m = clean_snapshot_meta(meta)
        kept = {}
        regions = REGION_PARTS.get(m["platform"], {})
        for name, data in (extras or {}).items():
            if not data:
                continue
            if name in REGION_NAMES and name not in EXTRA_PARTS:
                if name not in regions:
                    raise ValueError(f"{name} isn't a part of a {m['platform']} snapshot"
                                     + (f" (its parts beside ram: {', '.join(sorted(regions))})" if regions else ""))
                raw = gunzip(data, regions[name], name)
                if len(raw) > regions[name]:
                    raise ValueError(f"{name} is larger than {m['platform']}'s "
                                     + (f"{regions[name] // 1024} KB" if regions[name] >= 1024 else f"{regions[name]} bytes"))
                kept[name] = raw
                continue
            if name not in EXTRA_PARTS:
                continue
            limit = {"vram": min(EXTRA_PARTS[name], VRAM_MAX.get(m["platform"], EXTRA_PARTS[name])),
                     "state": STATE_MAX.get(m["platform"], EXTRA_PARTS[name])}.get(name, EXTRA_PARTS[name])
            raw = gunzip(data, limit, name)
            if len(raw) > limit:
                raise ValueError(f"{name} is larger than " + (f"{limit // 1024 // 1024} MB" if limit % 0x100000 == 0
                                                              else f"{limit // 1024} KB"))
            if name == "screen" and raw[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("screen must be a PNG")
            kept[name] = raw
        ram = gunzip(ram, SNAPSHOT_MAX[m["platform"]], "ram")
        if not ram or len(ram) > SNAPSHOT_MAX[m["platform"]]:
            raise ValueError(f"ram must be 1 byte to {SNAPSHOT_MAX[m['platform']] // 1024 // 1024} MB "
                             f"({m['platform']} main RAM)")
        end = int(m["base_address"], 16) + len(ram) - 1
        if not in_ram(m["platform"], hex(end)):
            raise ValueError("base_address + size runs past main RAM")
        if m["platform"] == "gc":
            m.update(gamecube_header(m, ram))
        digest = hashlib.sha1(ram).hexdigest()
        folder = self.data / "snapshots" / m["platform"] / m["id"]
        with self.lock:
            if (folder / f"{digest}.json").exists():
                return {"ok": True, "id": m["id"], "added": False, "sha1": digest}
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{digest}.ram.gz").write_bytes(gzip.compress(ram, 6))
            for name, raw in kept.items():
                if name == "screen":
                    (folder / f"{digest}.screen.png").write_bytes(raw)
                else:
                    (folder / f"{digest}.{name}.gz").write_bytes(gzip.compress(raw, 6))
            write_json(folder / f"{digest}.json", {**m, "size": len(ram), "sha1": digest, "received_at": now(),
                                                   "parts": sorted(["ram", *kept])})
            metas = sorted(folder.glob("*.json"), key=lambda p: json.loads(p.read_text(encoding="utf-8"))["received_at"])
            for old in metas[:-SNAPSHOTS_PER_GAME]:            # keep the newest ones
                stem = old.name[: -len(".json")]
                for f in folder.glob(stem + ".*"):
                    f.unlink(missing_ok=True)
            count = len(list(folder.glob("*.json")))
        return {"ok": True, "id": m["id"], "added": True, "sha1": digest, "stored": count, "parts": sorted(["ram", *kept])}

    def snapshots(self) -> list[dict]:
        out = []
        with self.lock:
            for folder in sorted((self.data / "snapshots").glob("*/*")):
                metas = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
                if metas:
                    out.append({"id": folder.name, "platform": folder.parent.name, "snapshots": len(metas),
                                "lines": sum(len(m["ocr"]) for m in metas),
                                "no_text": sum(1 for m in metas if m.get("no_text")),
                                "ocr_unavailable": sum(1 for m in metas if m.get("ocr_unavailable")),
                                "with": {p: sum(1 for m in metas if p in m.get("parts", []))
                                         for p in [*EXTRA_PARTS, *REGION_NAMES]
                                         if p in EXTRA_PARTS or any(p in m.get("parts", []) for m in metas)},
                                "last_upload": max(m["received_at"] for m in metas)})
        return out

    def add_hook(self, body: dict) -> dict:
        r = clean_hook_report(body)
        with self.lock:
            p = self.data / "hooks" / r["platform"] / f"{r['id']}.json"
            stored = json.loads(p.read_text(encoding="utf-8")) if p.exists() else \
                {"id": r["id"], "platform": r["platform"], "reports": []}
            key = (r["hook"]["address"], r["hook"]["register"], r.get("disc_version"))
            reports = [x for x in stored["reports"]
                       if (x["hook"]["address"], x["hook"]["register"], x.get("disc_version")) != key]
            replaced = len(reports) != len(stored["reports"])
            stored["reports"] = reports + [{**r, "received_at": now()}]
            write_json(p, stored)
        if not r.get("exe_sha1") and self.library:
            threading.Thread(target=self.fill_hook_hash, args=(r["platform"], r["id"]), daemon=True).start()
        return {"ok": True, "id": r["id"], "replaced": replaced, "reports": len(stored["reports"])}

    def fill_hook_hash(self, platform: str, game_id: str) -> None:
        try:
            rec = self.library.lookup(platform, game_id)
        except Exception as e:
            log.warning("identifying the library for %s failed: %s", game_id, e)
            return
        if not rec:
            return
        with self.lock:
            p = self.data / "hooks" / platform / f"{game_id}.json"
            stored = json.loads(p.read_text(encoding="utf-8"))
            for x in stored["reports"]:
                x.setdefault("exe_sha1", rec.get("exe_sha1"))
            write_json(p, stored)

    def hooks(self) -> list[dict]:
        out = []
        with self.lock:
            files = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.data / "hooks").glob("*/*.json"))]
        for s in files:
            out.append({"id": s["id"], "platform": s["platform"], "reports": [
                {"address": r["hook"]["address"], "register": r["hook"]["register"], "charset": r["hook"]["charset"],
                 "disc_version": r.get("disc_version"), "matched": f"{r['evidence']['lines_matched']}/"
                 f"{r['evidence']['lines_checked']}", "exe_sha1": r.get("exe_sha1"), "received_at": r["received_at"]}
                for r in s["reports"]]})
        return out

    def fill_hash(self, platform: str, game_id: str) -> None:
        try:
            rec = self.library.lookup(platform, game_id)
        except Exception as e:
            log.warning("identifying the library for %s failed: %s", game_id, e)
            return
        if not rec:
            return
        with self.lock:
            p = self.path(platform, game_id)
            stored = json.loads(p.read_text(encoding="utf-8"))
            stored["exe_sha1"], stored["exe"] = rec.get("exe_sha1"), rec.get("exe")
            write_json(p, stored)
        log.info("%s: exe hash filled from the library", game_id)

    def summary(self) -> list[dict]:
        out = []
        with self.lock:
            files = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.data / "samples").glob("*/*.json"))]
        for s in files:
            out.append({"id": s["id"], "platform": s["platform"], "title": s.get("title"), "samples": len(s["samples"]),
                        "exe_sha1": s.get("exe_sha1"), "last_upload": (s.get("uploads") or [{}])[-1].get("at")})
        return out


def create_app(token: str, data: Path, library_root: Path | None = None, scratch: Path | None = None) -> FastAPI:
    if not token:
        raise SystemExit("set YOMIKATA_INBOX_TOKEN")
    data.mkdir(parents=True, exist_ok=True)
    lib = Library(library_root, data / "library.json", scratch) if library_root else None
    inbox = Inbox(data, lib)
    app = FastAPI(title="Yomikata inbox", docs_url=None, redoc_url=None)

    def auth(request: Request) -> None:
        got = request.headers.get("authorization", "")
        if not hmac.compare_digest(got.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "missing or wrong token")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/api/samples")
    async def upload(request: Request):
        auth(request)
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, f"at most {MAX_BODY // 1024 // 1024} MB per upload")
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "body must be JSON") from None
        try:
            return inbox.add(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/snapshots")
    async def snapshot(request: Request):
        auth(request)
        if int(request.headers.get("content-length") or 0) > 80 * 1024 * 1024:
            raise HTTPException(413, "a snapshot upload is at most 80 MB")
        try:
            form = await request.form(max_part_size=1024 * 1024)
        except Exception:
            raise HTTPException(400, "send multipart/form-data with a meta part (JSON) and a ram file part") from None
        ram, meta = form.get("ram"), form.get("meta")
        if ram is None or isinstance(ram, str) or meta is None:
            raise HTTPException(400, "send multipart/form-data with a meta part (JSON) and a ram file part")
        try:
            meta_obj = json.loads(meta if isinstance(meta, str) else (await meta.read()).decode("utf-8"))
        except ValueError:
            raise HTTPException(400, "meta must be JSON") from None
        extras = {}
        for name in [*EXTRA_PARTS, *REGION_NAMES]:
            part = form.get(name)
            if part is not None and not isinstance(part, str):
                extras[name] = await part.read()
        try:
            return inbox.add_snapshot(meta_obj, await ram.read(), extras)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/snapshots")
    async def snapshot_listing(request: Request):
        auth(request)
        return {"games": inbox.snapshots()}

    @app.post("/api/hooks")
    async def hook_report(request: Request):
        auth(request)
        raw = await request.body()
        if len(raw) > 256 * 1024:
            raise HTTPException(413, "a hook report is at most 256 KB")
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "body must be JSON") from None
        try:
            return inbox.add_hook(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/hooks")
    async def hook_listing(request: Request):
        auth(request)
        return {"games": inbox.hooks()}

    @app.get("/api/samples")
    async def listing(request: Request):
        auth(request)
        return {"games": inbox.summary()}

    return app


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    e = os.environ
    app = create_app(e.get("YOMIKATA_INBOX_TOKEN", ""), Path(e.get("YOMIKATA_DATA", "/data")),
                     Path(e["YOMIKATA_LIBRARY"]) if e.get("YOMIKATA_LIBRARY") else None,
                     Path(e["YOMIKATA_SCRATCH"]) if e.get("YOMIKATA_SCRATCH") else None)
    uvicorn.run(app, host=host, port=port, log_level="warning")
