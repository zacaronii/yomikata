"""The sample inbox: readers (Genkan) upload memory samples here instead of handing files over by hand.

A sample pairs a line a reader OCR'd with the bytes found in memory at that moment, which is what pins down a game's
table when its script is compressed on disc. Samples contain script text, so they are kept in the inbox's own data
folder and never in the catalog repository.

POST /api/samples   one game's samples (Bearer token), merged with what's stored, duplicates dropped
GET  /api/samples   what's stored: per game, sample count and last upload (Bearer token)
POST /api/hooks     a hook a reader found and checked in play (docs/hook-finder.md); stored per game
GET  /api/hooks     the hook reports stored, per game
GET  /health

If the library is mounted, an upload without exe_sha1 gets it filled from the serial: the inbox identifies the
library's images once (cached by path, size and mtime) and looks the serial up."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from .identify import identify

log = logging.getLogger(__name__)
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,19}$")
PLATFORMS = {"ps1", "ps2", "psp", "nds", "3ds", "gc", "saturn", "sfc", "fc", "fds", "gba", "gb", "gbc", "n64", "md"}
# RomM-style library folders for each catalog platform (only these are identified)
LIBRARY_DIRS = {"ps1": "psx", "ps2": "ps2", "psp": "psp", "nds": "nds", "3ds": "3ds"}
IMAGE_EXT = {".chd", ".cue", ".iso", ".3ds", ".cci", ".nds", ".zip"}
MAX_BODY = 4 * 1024 * 1024
MAX_SAMPLES = 500                     # kept per game (oldest dropped first)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        if not isinstance(game_id, str) or not ID_RE.fullmatch(game_id):
            raise ValueError("id must be a release id such as SLPS-01001")
        if platform not in PLATFORMS:
            raise ValueError(f"platform must be one of: {', '.join(sorted(PLATFORMS))}")
        samples = body.get("samples")
        if not isinstance(samples, list) or not samples or len(samples) > MAX_SAMPLES:
            raise ValueError(f"samples must be a non-empty list (at most {MAX_SAMPLES})")
        new = [clean_sample(s) for s in samples]
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
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)
        if not stored.get("exe_sha1") and self.library:
            threading.Thread(target=self.fill_hash, args=(platform, game_id), daemon=True).start()
        return {"ok": True, "id": game_id, "added": added, "stored": len(stored["samples"])}

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
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)
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
            p.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")

    def hooks(self) -> list[dict]:
        out = []
        for p in sorted((self.data / "hooks").glob("*/*.json")):
            s = json.loads(p.read_text(encoding="utf-8"))
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
            p.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("%s: exe hash filled from the library", game_id)

    def summary(self) -> list[dict]:
        out = []
        for p in sorted((self.data / "samples").glob("*/*.json")):
            s = json.loads(p.read_text(encoding="utf-8"))
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
