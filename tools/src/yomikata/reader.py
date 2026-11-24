"""The reader runtime (runtime/, docs/script-api.md) from Python: the same library the apps embed, so a script
is run here by the code that runs it there.

The library is built from `runtime/` (its README says how) and found through `YOMIKATA_READER_LIB`, else beside
this package, else in the repository's `runtime/build`."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Callable

ABI = 1
TEXT, LAYOUT, CURSOR, LOG = 1, 2, 3, 4
KINDS = {TEXT: "text", LAYOUT: "layout", CURSOR: "cursor", LOG: "log"}

_READ = ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t)
_REGION_SIZE = ctypes.CFUNCTYPE(ctypes.c_int64, ctypes.c_void_p, ctypes.c_char_p)
_REGION_READ = ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint64, ctypes.c_void_p,
                                ctypes.c_size_t)
_DATA = ctypes.CFUNCTYPE(ctypes.c_int64, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t)
_EVENT = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t)
_HEADER_LINE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p)


class _Host(ctypes.Structure):
    _fields_ = [("user", ctypes.c_void_p), ("read", _READ), ("region_size", _REGION_SIZE),
                ("region_read", _REGION_READ), ("data", _DATA), ("event", _EVENT)]


class _Options(ctypes.Structure):
    _fields_ = [("abi", ctypes.c_uint32), ("platform", ctypes.c_char_p), ("game_id", ctypes.c_char_p),
                ("build_id", ctypes.c_char_p), ("has_base", ctypes.c_int), ("base", ctypes.c_uint64),
                ("load_instructions", ctypes.c_uint64), ("call_instructions", ctypes.c_uint64),
                ("memory", ctypes.c_uint64)]


class ReaderUnavailable(RuntimeError):
    """The library isn't built or can't be loaded."""


class ScriptError(RuntimeError):
    """A script didn't load: a syntax error, its own "another build" error, a region it needs, a limit."""


def _names() -> list[str]:
    if sys.platform == "darwin":
        return ["libyomikata_reader.dylib"]
    if sys.platform == "win32":
        return ["yomikata_reader.dll", "libyomikata_reader.dll"]
    return ["libyomikata_reader.so"]


def library_path() -> Path:
    given = os.environ.get("YOMIKATA_READER_LIB")
    if given:
        return Path(given)
    here = Path(__file__).resolve().parent
    places = [here, *(p / "runtime" / "build" for p in here.parents)]
    for place in places:
        for name in _names():
            if (place / name).is_file():
                return place / name
    raise ReaderUnavailable("the reader library isn't built: see runtime/README.md, or point YOMIKATA_READER_LIB at it")


_lib = None


def library():
    global _lib
    if _lib is None:
        try:
            lib = ctypes.CDLL(str(library_path()))
        except OSError as e:
            raise ReaderUnavailable(str(e)) from e
        lib.yk_version.restype = ctypes.c_char_p
        lib.yk_script_level.restype = ctypes.c_int
        lib.yk_open.restype = ctypes.c_void_p
        lib.yk_open.argtypes = [ctypes.POINTER(_Host), ctypes.POINTER(_Options)]
        lib.yk_close.argtypes = [ctypes.c_void_p]
        lib.yk_load.restype = ctypes.c_int
        lib.yk_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        lib.yk_unload.argtypes = [ctypes.c_void_p]
        lib.yk_error.restype = ctypes.c_char_p
        lib.yk_error.argtypes = [ctypes.c_void_p]
        lib.yk_frame.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        lib.yk_state_loaded.argtypes = [ctypes.c_void_p]
        lib.yk_picture.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        lib.yk_eval.restype = ctypes.c_char_p
        lib.yk_eval.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_int)]
        lib.yk_header.argtypes = [ctypes.c_char_p, ctypes.c_size_t, _HEADER_LINE, ctypes.c_void_p]
        _lib = lib
    return _lib


def version() -> str:
    return library().yk_version().decode()


def script_level() -> int:
    """What this runtime offers a script: a script that needs a higher level doesn't load (docs/script-api.md)."""
    return library().yk_script_level()


def header(source: str | bytes) -> list[tuple[str, str]]:
    """A script's header lines as (key, rest), read without running it."""
    raw = source.encode("utf-8") if isinstance(source, str) else source
    out: list[tuple[str, str]] = []
    line = _HEADER_LINE(lambda _user, key, rest: out.append((key.decode("utf-8", "replace"), rest.decode("utf-8", "replace"))))
    library().yk_header(raw, len(raw), line, None)
    return out


def memory(base: int, data: bytes, mirrors: tuple[int, ...] = ()) -> Callable[[int, int], bytes]:
    """A read over one stretch of memory at `base`. `mirrors` are other addresses the same bytes answer at."""
    def read(address: int, size: int) -> bytes:
        for start in (base, *mirrors):
            at = address - start
            if 0 <= at < len(data):
                return data[at:at + size]
        return b""
    return read


class Reader:
    """One game's reader. `read(address, size)` gives the bytes there (fewer, or none, where memory isn't);
    `regions` are named stretches such as video memory; `data` the script's data files by name.

    Events gather in `events` as (kind, dict): kind is "text", "layout", "cursor" or "log"."""

    def __init__(self, read: Callable[[int, int], bytes], *, platform: str | None = None, game_id: str = "",
                 build_id: str | None = None, base: int | None = None, regions: dict[str, bytes] | None = None,
                 data: dict[str, bytes] | None = None, load_instructions: int = 0, call_instructions: int = 0,
                 memory_limit: int = 0):
        self.read = read
        self.regions = regions if regions is not None else {}
        self.data = data if data is not None else {}
        self.events: list[tuple[str, dict]] = []
        self.raw: list[tuple[int, bytes]] = []      # the events as the library gave them, for comparing runs

        def copy(out, chunk: bytes, size: int) -> int:
            chunk = chunk[:size]
            ctypes.memmove(out, chunk, len(chunk))
            return len(chunk)

        def on_read(_user, address, out, size):
            return copy(out, self.read(address, size), size)

        def on_region_size(_user, name):
            region = self.regions.get(name.decode())
            return -1 if region is None else len(region)

        def on_region_read(_user, name, offset, out, size):
            region = self.regions.get(name.decode())
            return 0 if region is None else copy(out, region[offset:offset + size], size)

        def on_data(_user, name, out, capacity):
            blob = self.data.get(name.decode())
            if blob is None:
                return -1
            if capacity:
                copy(out, blob, capacity)
            return len(blob)

        def on_event(_user, kind, text, length):
            line = ctypes.string_at(text, length)
            self.raw.append((kind, line))
            self.events.append((KINDS.get(kind, str(kind)), json.loads(line)))

        # Kept on the object: the library calls them for as long as the reader lives.
        self._host = _Host(None, _READ(on_read), _REGION_SIZE(on_region_size), _REGION_READ(on_region_read),
                           _DATA(on_data), _EVENT(on_event))
        encode = lambda s: None if s is None else s.encode()        # noqa: E731
        self._options = _Options(ABI, encode(platform), encode(game_id), encode(build_id),
                                 0 if base is None else 1, base or 0, load_instructions, call_instructions, memory_limit)
        self._lib = library()
        self._handle = self._lib.yk_open(ctypes.byref(self._host), ctypes.byref(self._options))
        if not self._handle:
            raise ReaderUnavailable("the reader library refused to open (another ABI?)")

    def load(self, source: str | bytes, name: str = "script") -> None:
        raw = source.encode("utf-8") if isinstance(source, str) else source
        if not self._lib.yk_load(self._handle, name.encode(), raw, len(raw)):
            raise ScriptError(self._lib.yk_error(self._handle).decode("utf-8", "replace"))

    def unload(self) -> None:
        self._lib.yk_unload(self._handle)

    def frame(self, number: int) -> None:
        self._lib.yk_frame(self._handle, number)

    def state_loaded(self) -> None:
        self._lib.yk_state_loaded(self._handle)

    def picture(self, width: int, height: int) -> None:
        self._lib.yk_picture(self._handle, width, height)

    def eval(self, code: str) -> str:
        """Runs a chunk in the script's state and gives what it returned, tab-separated. Raises ScriptError."""
        raw, ok = code.encode("utf-8"), ctypes.c_int(0)
        out = self._lib.yk_eval(self._handle, raw, len(raw), ctypes.byref(ok)).decode("utf-8", "replace")
        if not ok.value:
            raise ScriptError(out)
        return out

    def take(self, kind: str | None = None) -> list[dict]:
        """The events so far, of one kind or (kind, event) pairs of all, and forgets them."""
        events, self.events = self.events, []
        if kind is None:
            return events           # type: ignore[return-value]
        return [e for k, e in events if k == kind]

    def close(self) -> None:
        if self._handle:
            self._lib.yk_close(self._handle)
            self._handle = None

    def __enter__(self) -> "Reader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
