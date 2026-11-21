"""A minimal headless libretro frontend (ctypes, no screen, no sound).

It loads a core and a game, restores a savestate, runs frames (optionally holding buttons), and reads the console's
RAM and the last frame. That's all the analysis needs: the point is to reproduce a moment a reader captured during
play, as often as we like. Run it in an isolated container: the core is native code downloaded from libretro."""

from __future__ import annotations

import ctypes as C
import tempfile
from pathlib import Path

# environment commands (libretro.h)
ENV_GET_OVERSCAN, ENV_GET_CAN_DUPE, ENV_SET_PIXEL_FORMAT = 2, 3, 10
ENV_GET_SYSTEM_DIRECTORY, ENV_GET_VARIABLE, ENV_GET_VARIABLE_UPDATE = 9, 15, 17
ENV_GET_SAVE_DIRECTORY = 31
MEMORY_SYSTEM_RAM = 2
JOYPAD = {"b": 0, "y": 1, "select": 2, "start": 3, "up": 4, "down": 5, "left": 6, "right": 7,
          "a": 8, "x": 9, "l": 10, "r": 11}       # libretro RetroPad ids (PS1: a = circle, b = cross)


class GameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p), ("size", C.c_size_t), ("meta", C.c_char_p)]


class SystemAvInfo(C.Structure):
    _fields_ = [("base_width", C.c_uint), ("base_height", C.c_uint), ("max_width", C.c_uint),
                ("max_height", C.c_uint), ("aspect_ratio", C.c_float), ("fps", C.c_double),
                ("sample_rate", C.c_double)]


class Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


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
        self.frame: tuple[bytes, int, int, int] | None = None
        self.pixel_format = 0
        self.fps = 60.0
        self._cbs = [ENV_CB(self._env), VIDEO_CB(self._video), AUDIO_CB(lambda l, r: None),
                     AUDIO_BATCH_CB(lambda d, n: n), POLL_CB(lambda: None), STATE_CB(self._input)]
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
        if cmd == ENV_SET_PIXEL_FORMAT:
            self.pixel_format = C.cast(data, C.POINTER(C.c_int))[0]
            return True
        if cmd == ENV_GET_VARIABLE:
            var = C.cast(data, C.POINTER(Variable))
            val = self.options.get(var[0].key)
            if val is None:
                return False
            var[0].value = val
            return True
        if cmd == ENV_GET_VARIABLE_UPDATE:
            C.cast(data, C.POINTER(C.c_bool))[0] = False
            return True
        if cmd in (ENV_GET_CAN_DUPE, ENV_GET_OVERSCAN):
            C.cast(data, C.POINTER(C.c_bool))[0] = True
            return True
        return False

    def _video(self, data, width, height, pitch):
        if data:
            self.frame = (C.string_at(data, pitch * height), width, height, pitch)

    def _input(self, port, device, index, button):
        if port != 0 or device != 1:
            return 0
        return 1 if any(JOYPAD[b] == button for b in self.held) else 0

    # --- control
    def load_game(self, path: Path) -> None:
        info = GameInfo(str(path).encode(), None, 0, None)
        if not self.lib.retro_load_game(C.byref(info)):
            raise RuntimeError(f"the core couldn't load {path}")
        av = SystemAvInfo()
        self.lib.retro_get_system_av_info(C.byref(av))
        self.fps = av.fps

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

    def run(self, frames: int = 1, hold: set[str] | None = None) -> None:
        self.held = set(hold or ())
        for _ in range(frames):
            self.lib.retro_run()
        self.held = set()

    def ram(self) -> bytes:
        ptr = self.lib.retro_get_memory_data(MEMORY_SYSTEM_RAM)
        size = self.lib.retro_get_memory_size(MEMORY_SYSTEM_RAM)
        if not ptr or not size:
            raise RuntimeError("the core doesn't expose system RAM")
        return C.string_at(ptr, size)

    def frame_rgb(self):
        """The last frame as an RGB numpy array (the core sends XRGB8888, RGB565 or 0RGB1555)."""
        import numpy as np
        if self.frame is None:
            return None
        raw, w, h, pitch = self.frame
        if self.pixel_format == 1:                  # XRGB8888
            a = np.frombuffer(raw, dtype=np.uint8).reshape(h, pitch // 4, 4)[:, :w]
            return a[:, :, [2, 1, 0]].copy()
        a = np.frombuffer(raw, dtype="<u2").reshape(h, pitch // 2)[:, :w].astype(np.uint32)
        if self.pixel_format == 2:                  # RGB565
            r, g, b = (a >> 11) & 31, (a >> 5) & 63, a & 31
            return np.stack([r << 3, g << 2, b << 3], axis=-1).astype(np.uint8)
        r, g, b = (a >> 10) & 31, (a >> 5) & 31, a & 31
        return np.stack([r << 3, g << 3, b << 3], axis=-1).astype(np.uint8)
