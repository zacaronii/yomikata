"""A minimal headless libretro frontend (ctypes, no screen, no sound).

It loads a core and a game, restores a savestate, runs frames (optionally holding buttons and a stick), and reads
the console's memory and the last frame. That's all the analysis needs: the point is to reproduce a moment a reader
captured during play, as often as we like. A core that draws with the graphics card gets a context without a
window (glcontext.py). Run it in an isolated container: the core is native code downloaded from libretro."""

from __future__ import annotations

import ctypes as C
import tempfile
from pathlib import Path

# environment commands (libretro.h)
ENV_GET_OVERSCAN, ENV_GET_CAN_DUPE, ENV_SET_PIXEL_FORMAT = 2, 3, 10
ENV_GET_SYSTEM_DIRECTORY, ENV_GET_VARIABLE, ENV_GET_VARIABLE_UPDATE = 9, 15, 17
ENV_GET_SAVE_DIRECTORY = 31
ENV_SET_GEOMETRY = 37
ENV_SET_VARIABLES = 16
ENV_GET_LOG_INTERFACE = 27
# A core's log function: printf-like, (level, format, ...). The arguments after the format aren't read: the
# format alone is kept, which says what the core was doing.
LOG_CB = C.CFUNCTYPE(None, C.c_int, C.c_char_p)
ENV_SET_HW_RENDER, ENV_SET_MEMORY_MAPS, ENV_GET_PREFERRED_HW_RENDER = 14, 36, 56
HW_OPENGL, HW_OPENGL_CORE = 1, 3
HW_FRAME = C.c_void_p(-1).value                 # the frame is in the framebuffer, not in memory
DEVICE_JOYPAD, DEVICE_ANALOG, DEVICE_POINTER, JOYPAD_MASK = 1, 5, 6, 256
# A touch screen (a DS's lower screen) answers as libretro's pointer: while the pseudo-button "touch" is held, the
# pointer is pressed at the stick's place, -1 to 1 across the core's whole picture each way (as the pointer's
# -0x7FFF to 0x7FFF), so a touch goes into the record of the way as a button and a place, as a push does.
TOUCH = "touch"
MEMORY_SYSTEM_RAM, MEMORY_VIDEO_RAM = 2, 3
JOYPAD = {"b": 0, "y": 1, "select": 2, "start": 3, "up": 4, "down": 5, "left": 6, "right": 7,
          "a": 8, "x": 9, "l": 10, "r": 11,       # libretro RetroPad ids (PS1: a = circle, b = cross)
          "l2": 12, "r2": 13, "l3": 14, "r3": 15}  # the second shoulder buttons, and the sticks pressed in


class GameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p), ("size", C.c_size_t), ("meta", C.c_char_p)]


class SystemInfo(C.Structure):
    _fields_ = [("library_name", C.c_char_p), ("library_version", C.c_char_p), ("valid_extensions", C.c_char_p),
                ("need_fullpath", C.c_bool), ("block_extract", C.c_bool)]


class SystemAvInfo(C.Structure):
    _fields_ = [("base_width", C.c_uint), ("base_height", C.c_uint), ("max_width", C.c_uint),
                ("max_height", C.c_uint), ("aspect_ratio", C.c_float), ("fps", C.c_double),
                ("sample_rate", C.c_double)]


class GameGeometry(C.Structure):
    _fields_ = [("base_width", C.c_uint), ("base_height", C.c_uint), ("max_width", C.c_uint),
                ("max_height", C.c_uint), ("aspect_ratio", C.c_float)]


class Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class HwRender(C.Structure):
    _fields_ = [("context_type", C.c_int), ("context_reset", C.CFUNCTYPE(None)),
                ("get_current_framebuffer", C.CFUNCTYPE(C.c_size_t)),
                ("get_proc_address", C.CFUNCTYPE(C.c_void_p, C.c_char_p)),
                ("depth", C.c_bool), ("stencil", C.c_bool), ("bottom_left_origin", C.c_bool),
                ("version_major", C.c_uint), ("version_minor", C.c_uint), ("cache_context", C.c_bool),
                ("context_destroy", C.CFUNCTYPE(None)), ("debug_context", C.c_bool)]


class MemoryDescriptor(C.Structure):
    _fields_ = [("flags", C.c_uint64), ("ptr", C.c_void_p), ("offset", C.c_size_t), ("start", C.c_size_t),
                ("select", C.c_size_t), ("disconnect", C.c_size_t), ("len", C.c_size_t), ("addrspace", C.c_char_p)]


class MemoryMap(C.Structure):
    _fields_ = [("descriptors", C.POINTER(MemoryDescriptor)), ("num", C.c_uint)]


def _reduce(address: int, mask: int) -> int:
    """An address with the bits of `mask` taken out and those above them moved down: what a core means by
    bits that are "disconnected" (a LoROM cartridge's bit 15: each bank's upper half follows the last's)."""
    while mask:
        low = (mask - 1) & ~mask
        address = (address & low) | ((address >> 1) & ~low)
        mask = (mask & (mask - 1)) >> 1
    return address


def locate(descriptors: list[dict], address: int) -> tuple[int, int] | None:
    """Where one of the console's addresses is in a core's memory map, as libretro defines one: the number of
    the first descriptor it belongs to, and the offset into that descriptor's memory. An address belongs
    where it agrees with `start` in the bits of `select` (without a select: from start for len bytes); the
    `disconnect` bits are then taken out, and what is past `len` wraps, the highest bit first (mirrors)."""
    for number, d in enumerate(descriptors):
        select, start, length = d.get("select", 0), d["start"], d["len"]
        if select:
            if (start ^ address) & select:
                continue
        elif not start <= address < start + length:
            continue
        at = _reduce((address - start) & ((1 << 64) - 1), d.get("disconnect", 0))
        while at >= length and at:
            at -= 1 << (at.bit_length() - 1)
        if at < length:
            return number, at
    return None


ENV_CB = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VIDEO_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AUDIO_CB = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AUDIO_BATCH_CB = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t)
POLL_CB = C.CFUNCTYPE(None)
STATE_CB = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)


class Core:
    def __init__(self, core_path: Path, system_dir: Path | None = None, options: dict[str, str] | None = None):
        self.lib = C.CDLL(str(core_path))
        self.system_dir = str(system_dir or tempfile.mkdtemp(prefix="yomikata-system-")).encode()
        self.options = {k.encode(): v.encode() for k, v in (options or {}).items()}
        self.held: set[str] = set()
        self.stick: tuple[float, float] = (0.0, 0.0)        # the left stick, each way from -1 to 1 (up is -1)
        self.asked: set[tuple[int, int, int, int]] = set()  # what the core has asked of the pad, for finding out
        self.maps: list[tuple[int, int, int]] = []          # (address, size, where the core keeps it)
        self.descriptors: list[dict] = []                    # the same as the core gave them, with select and disconnect
        self.variables: dict[str, str] = {}                 # the core's options as it declares them: key -> "what; a|b"
        self.gl = None                                      # the context of a core that draws with the card
        self.hw: HwRender | None = None
        self.frame: tuple[bytes, int, int, int] | None = None
        self.pixel_format = 0
        self.fps = 60.0
        self.base: tuple[int, int] | None = None            # the game's own picture's size, as the core says it
        self.log: list[str] = []                             # what the core has logged (formats only), the last 200
        self._defaults: dict[bytes, bytes] = {}             # each option's default, as the core declared it
        self._cbs = [ENV_CB(self._env), VIDEO_CB(self._video), AUDIO_CB(lambda l, r: None),
                     AUDIO_BATCH_CB(lambda d, n: n), POLL_CB(lambda: None), STATE_CB(self._input),
                     LOG_CB(self._log)]
        L = self.lib
        L.retro_set_environment(self._cbs[0])
        L.retro_set_video_refresh(self._cbs[1])
        L.retro_set_audio_sample(self._cbs[2])
        L.retro_set_audio_sample_batch(self._cbs[3])
        L.retro_set_input_poll(self._cbs[4])
        L.retro_set_input_state(self._cbs[5])
        L.retro_init()
        L.retro_serialize_size.restype = C.c_size_t
        L.retro_serialize.argtypes = [C.c_void_p, C.c_size_t]
        L.retro_serialize.restype = C.c_bool
        L.retro_unserialize.argtypes = [C.c_void_p, C.c_size_t]
        L.retro_unserialize.restype = C.c_bool
        L.retro_load_game.argtypes = [C.POINTER(GameInfo)]
        L.retro_load_game.restype = C.c_bool
        L.retro_get_memory_data.restype = C.c_void_p
        L.retro_get_memory_size.restype = C.c_size_t

    # --- callbacks
    def _env(self, cmd, data):
        cmd &= 0xFFFF
        if cmd in (ENV_GET_SYSTEM_DIRECTORY, ENV_GET_SAVE_DIRECTORY):
            C.cast(data, C.POINTER(C.c_char_p))[0] = self.system_dir
            return True
        if cmd == ENV_SET_GEOMETRY:                    # the game's picture changed its size (a border came)
            geometry = C.cast(data, C.POINTER(GameGeometry))[0]
            if geometry.base_width and geometry.base_height:
                self.base = (geometry.base_width, geometry.base_height)
            return True
        if cmd == ENV_SET_PIXEL_FORMAT:
            self.pixel_format = C.cast(data, C.POINTER(C.c_int))[0]
            return True
        if cmd == ENV_SET_VARIABLES:
            var, i = C.cast(data, C.POINTER(Variable)), 0
            while var[i].key:
                self.variables[var[i].key.decode(errors="replace")] = (var[i].value or b"").decode(errors="replace")
                # "What it is; first|second|...": the first value is the default, as a frontend takes it.
                self._defaults[var[i].key] = (var[i].value or b"").split(b";", 1)[-1].strip().split(b"|")[0]
                i += 1
            return True
        if cmd == ENV_GET_VARIABLE:
            # An option not given is answered with the core's own default: some cores (mednafen's) don't
            # check that an answer came, and crash without one.
            var = C.cast(data, C.POINTER(Variable))
            val = self.options.get(var[0].key, self._defaults.get(var[0].key))
            if val is None:
                return False
            var[0].value = val
            return True
        if cmd == ENV_GET_LOG_INTERFACE:
            # A log function to call: some cores call it without checking they were given one.
            C.cast(data, C.POINTER(C.c_void_p))[0] = C.cast(self._cbs[6], C.c_void_p).value
            return True
        if cmd == ENV_GET_VARIABLE_UPDATE:
            C.cast(data, C.POINTER(C.c_bool))[0] = False
            return True
        if cmd in (ENV_GET_CAN_DUPE, ENV_GET_OVERSCAN):
            C.cast(data, C.POINTER(C.c_bool))[0] = True
            return True
        if cmd == ENV_GET_PREFERRED_HW_RENDER:
            C.cast(data, C.POINTER(C.c_uint))[0] = HW_OPENGL_CORE
            return True
        if cmd == ENV_SET_HW_RENDER:
            return self._hw_render(C.cast(data, C.POINTER(HwRender)))
        if cmd == ENV_SET_MEMORY_MAPS:
            m = C.cast(data, C.POINTER(MemoryMap))[0]
            self.maps = [(d.start, d.len, d.ptr) for d in (m.descriptors[i] for i in range(m.num)) if d.ptr and d.len]
            self.descriptors = [{"start": d.start, "len": d.len, "ptr": d.ptr + d.offset, "select": d.select,
                                 "disconnect": d.disconnect}
                                for d in (m.descriptors[i] for i in range(m.num)) if d.ptr and d.len]
            return True
        return False

    def _log(self, level, fmt):
        self.log = (self.log + [(fmt or b"").decode(errors="replace").strip()])[-200:]

    def _hw_render(self, asked) -> bool:
        """A core asks for a context to draw with: OpenGL is given where there is a driver, nothing else."""
        from . import glcontext
        want = asked[0]
        if want.context_type not in (HW_OPENGL, HW_OPENGL_CORE):
            return False
        try:
            if self.gl is None:
                core_profile = want.context_type == HW_OPENGL_CORE
                self.gl = glcontext.Context(want.version_major or 3, want.version_minor or 3, core_profile)
        except glcontext.GLError as e:
            self.gl_error = str(e)
            return False
        self._hw_cbs = [type(want.get_current_framebuffer)(lambda: self.gl.framebuffer),
                        type(want.get_proc_address)(lambda name: self.gl.address(name))]
        want.get_current_framebuffer, want.get_proc_address = self._hw_cbs
        self.hw = HwRender.from_buffer_copy(want)
        return True

    def _video(self, data, width, height, pitch):
        if data == HW_FRAME and self.gl is not None:
            self.frame = (None, width, height, 0)           # read from the framebuffer when asked for
        elif data:
            self.frame = (C.string_at(data, pitch * height), width, height, pitch)

    def _input(self, port, device, index, button):
        device &= 0xFF                              # a core may ask by a kind of its own of pad or stick
        self.asked.add((port, device, index, button))
        if port != 0:
            return 0
        if device == DEVICE_POINTER:
            if index != 0:
                return 0
            touching = TOUCH in self.held
            if button in (0, 1):                    # x, y
                return max(-0x7FFF, min(0x7FFF, round(self.stick[button] * 0x7FFF))) if touching else 0
            if button == 2:                         # pressed
                return 1 if touching else 0
            if button == 3:                         # how many pointers are down
                return 1 if touching else 0
            return 0
        if device == DEVICE_ANALOG:
            if index == 0 and button in (0, 1):
                return max(-0x7FFF, min(0x7FFF, round(self.stick[button] * 0x7FFF)))
            if index == 2:                          # a button, as how far it is pressed
                return 0x7FFF if any(JOYPAD.get(b) == button for b in self.held) else 0
            return 0
        if device != DEVICE_JOYPAD:
            return 0
        if button == JOYPAD_MASK:
            return sum(1 << JOYPAD[b] for b in self.held if b in JOYPAD)
        return 1 if any(JOYPAD.get(b) == button for b in self.held) else 0

    # --- control
    def load_game(self, path: Path) -> None:
        system = SystemInfo()
        self.lib.retro_get_system_info(C.byref(system))
        info = GameInfo(str(path).encode(), None, 0, None)
        if not system.need_fullpath:                # the core wants the file's bytes (cartridge cores do)
            self._game = C.create_string_buffer(path.read_bytes())      # kept: a core may go on reading it
            info.data, info.size = C.cast(self._game, C.c_void_p), len(self._game) - 1
        if not self.lib.retro_load_game(C.byref(info)):
            why = getattr(self, "gl_error", None)       # a core that draws with the card and was given no context
            raise RuntimeError(f"the core couldn't load {path}" + (f": it asked for OpenGL, and {why}" if why else ""))
        av = SystemAvInfo()
        self.lib.retro_get_system_av_info(C.byref(av))
        self.fps = av.fps
        if av.base_width and av.base_height:
            self.base = (av.base_width, av.base_height)
        self.lib.retro_set_controller_port_device(0, DEVICE_JOYPAD)
        if getattr(self, "hw", None) is not None:
            self.gl.make_framebuffer(max(av.max_width, av.base_width, 640), max(av.max_height, av.base_height, 480))
            self.hw.context_reset()
        self._loaded = True

    def close(self) -> None:
        """Unloads the game and ends the core, as a frontend does on quitting; once. A core may not come back
        from this (flycast's doesn't always): the harness waits for it only a little (harness/mcp.py)."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        if getattr(self, "_loaded", False):
            if self.hw is not None and self.hw.context_destroy:
                self.hw.context_destroy()
            self.lib.retro_unload_game()
        self.lib.retro_deinit()
        if self.gl is not None:
            self.gl.close()

    def state_size(self) -> int:
        return int(self.lib.retro_serialize_size())

    def load_state(self, state: bytes) -> None:
        buf = C.create_string_buffer(state, len(state))
        if not self.lib.retro_unserialize(buf, len(state)):
            raise RuntimeError(f"the core refused the savestate ({len(state)} bytes; it expects {self.state_size()})")

    def save_state(self) -> bytes:
        n = self.state_size()
        buf = C.create_string_buffer(n)
        if not self.lib.retro_serialize(buf, n):
            raise RuntimeError("the core couldn't save its state")
        return buf.raw

    def run(self, frames: int = 1, hold: set[str] | None = None, stick: tuple[float, float] = (0.0, 0.0)) -> None:
        self.held, self.stick = set(hold or ()), stick
        for _ in range(frames):
            self.lib.retro_run()
        self.held, self.stick = set(), (0.0, 0.0)

    def ram(self) -> bytes:
        ptr = self.lib.retro_get_memory_data(MEMORY_SYSTEM_RAM)
        size = self.lib.retro_get_memory_size(MEMORY_SYSTEM_RAM)
        if not ptr or not size:
            if self.maps:                           # a core that only describes its memory: the largest stretch
                start, size, ptr = max(self.maps, key=lambda m: m[1])
                return C.string_at(ptr, size)
            raise RuntimeError("the core doesn't expose system RAM")
        return C.string_at(ptr, size)

    def ram_base(self) -> int | None:
        """Where `ram()` starts in the console's addresses, for a core that says (else the platform's is used)."""
        if self.lib.retro_get_memory_size(MEMORY_SYSTEM_RAM) or not self.maps:
            return None
        return max(self.maps, key=lambda m: m[1])[0]

    def read(self, address: int, size: int) -> bytes:
        """Bytes at one of the console's addresses, from a core that describes its memory; fewer where it ends."""
        found = locate(self.descriptors, address)
        if found is None:
            return b""
        d, at = self.descriptors[found[0]], found[1]
        # As far as the stretch goes on in memory; and with disconnected bits, no further than the next of them
        # (a LoROM bank's end: what follows in the console's addresses isn't what follows in the cartridge).
        most = d["len"] - at
        if d["disconnect"]:
            lowest = d["disconnect"] & -d["disconnect"]
            most = min(most, lowest - ((address - d["start"]) & (lowest - 1)))
        return C.string_at(d["ptr"] + at, max(0, min(size, most)))

    def video_ram(self) -> bytes | None:
        """The console's video memory, from a core that gives it out (cartridge consoles' cores do)."""
        ptr = self.lib.retro_get_memory_data(MEMORY_VIDEO_RAM)
        size = self.lib.retro_get_memory_size(MEMORY_VIDEO_RAM)
        return C.string_at(ptr, size) if ptr and size else None

    def frame_rgb(self):
        """The last frame as an RGB numpy array (the core sends XRGB8888, RGB565 or 0RGB1555)."""
        import numpy as np
        if self.frame is None:
            return None
        raw, w, h, pitch = self.frame
        if raw is None:                             # in the framebuffer: RGBA, the bottom row first unless told
            a = np.frombuffer(self.gl.read(w, h), dtype=np.uint8).reshape(-1, min(w, self.gl.size[0]), 4)[:, :, :3]
            return (a[::-1] if self.hw.bottom_left_origin else a).copy()
        if self.pixel_format == 1:                  # XRGB8888
            a = np.frombuffer(raw, dtype=np.uint8).reshape(h, pitch // 4, 4)[:, :w]
            return a[:, :, [2, 1, 0]].copy()
        a = np.frombuffer(raw, dtype="<u2").reshape(h, pitch // 2)[:, :w].astype(np.uint32)
        if self.pixel_format == 2:                  # RGB565
            r, g, b = (a >> 11) & 31, (a >> 5) & 63, a & 31
            return np.stack([r << 3, g << 2, b << 3], axis=-1).astype(np.uint8)
        r, g, b = (a >> 10) & 31, (a >> 5) & 31, a & 31
        return np.stack([r << 3, g << 3, b << 3], axis=-1).astype(np.uint8)
