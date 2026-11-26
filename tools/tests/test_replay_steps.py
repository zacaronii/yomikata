import argparse
import gzip

import numpy as np
import pytest
from PIL import Image

from yomikata import cli
from yomikata.contrast import contrast, find_text
from yomikata.replay import steps
from yomikata.replay.steps import Step


class FakeCore:
    """Counts frames; RAM byte 0 is the number of frames a button was held, byte 1 the frame count."""

    def __init__(self, drawn=True):
        self.frames, self.held, self.log, self.drawn = 0, 0, [], drawn

    def run(self, frames=1, hold=None):
        self.frames += frames
        self.held += frames if hold else 0
        self.log.append((frames, sorted(hold or ())))
        self.drawn = True

    def ram(self):
        return bytes([self.held % 256, self.frames % 256]) + bytes(14)

    def frame_rgb(self):
        return np.full((240, 320, 3), self.frames % 256, dtype=np.uint8) if self.drawn else None

    def save_state(self):
        return b"state at %d" % self.frames


def test_parse_reads_steps_and_repeats():
    assert steps.parse("wait:600, start:4, snap") == [Step("wait", 600), Step("hold", 4, frozenset({"start"})), Step("snap")]
    assert steps.parse("up+a:2,save:before-choice,sheet") == \
        [Step("hold", 2, frozenset({"up", "a"})), Step("save", name="before-choice"), Step("sheet")]
    assert steps.parse("2x(a:4,2x(wait:1),snap),wait:3") == \
        [Step("hold", 4, frozenset({"a"})), Step("wait", 1), Step("wait", 1), Step("snap")] * 2 + [Step("wait", 3)]
    assert steps.parse("") == []


@pytest.mark.parametrize("text", ["jump:3", "a", "wait:x", "snap:2", "save:../x", "2x(a:1", "a:1)", "up+fire:2"])
def test_parse_refuses_what_it_doesnt_know(text):
    with pytest.raises(ValueError):
        steps.parse(text)


def test_run_writes_snaps_states_and_a_sheet(tmp_path):
    core = FakeCore()
    lines = steps.run(core, steps.parse("wait:10,3x(a:4,wait:6,snap),save:here,sheet"), tmp_path, {"held": 0, "frames": 1})
    assert core.log[:3] == [(10, []), (4, ["a"]), (6, [])]
    assert lines == ["s000 held=4 frames=20", "s001 held=8 frames=30", "s002 held=12 frames=40"]
    assert gzip.decompress((tmp_path / "s001.ram.gz").read_bytes())[:2] == bytes([8, 30])
    assert Image.open(tmp_path / "s002.png").getpixel((0, 0)) == (40, 40, 40)
    assert gzip.decompress((tmp_path / "here.state.gz").read_bytes()) == b"state at 40"
    assert gzip.decompress((tmp_path / "end.state.gz").read_bytes()) == b"state at 40"
    assert Image.open(tmp_path / "sheet.png").size == (1280, 240)                    # three snaps and the sheet frame


def test_a_snap_before_the_first_frame_keeps_its_place_on_the_sheet(tmp_path):
    steps.run(FakeCore(drawn=False), steps.parse("snap,wait:5,snap"), tmp_path)      # right after a savestate
    sheet = Image.open(tmp_path / "sheet.png")
    assert sheet.getpixel((10, 10)) == (0, 0, 0) and sheet.getpixel((330, 10)) == (5, 5, 5)
    assert (tmp_path / "s000.ram.gz").is_file() and (tmp_path / "s001.png").is_file()


def test_contrast_finds_the_byte_that_says_so():
    def ram(waiting, noise):
        r = bytearray(64)
        r[10], r[20], r[30], r[40] = (1 if waiting else noise % 3 * 2), noise, 7, (5 if waiting else 5 * (noise % 2))
        return bytes(r)
    hits = contrast([ram(True, n) for n in (1, 2, 3)], [ram(False, n) for n in (4, 5, 6)])
    assert hits == [(10, 1, [0, 2, 4])]             # 20 changes within the group, 30 never differs, 40 is 5 elsewhere too


def test_find_text_gives_offsets_per_charset():
    ram = bytes(100) + "学校".encode("cp932") + bytes(50) + "学校".encode("utf-16-le") + bytes(9) + "学校".encode("cp932")
    assert find_text(ram, "学校") == {"shift_jis": [100, 167], "utf16le": [154]}
    assert find_text(ram, "先生") == {}
    script_only = bytes(100) + "学校".encode("cp932") + bytes(200)
    assert find_text(ram, "学校", absent=[script_only]) == {"shift_jis": [167], "utf16le": [154]}


def test_cli_prints_addresses_not_text(tmp_path, capsys):
    a, b = tmp_path / "a.ram.gz", tmp_path / "b.ram"
    a.write_bytes(gzip.compress(bytes(16) + "学校".encode("cp932") + b"\x01" + bytes(11)))
    b.write_bytes(bytes(32))
    assert cli.main(["find", "学校", str(a), str(b)]) == 0
    assert capsys.readouterr().out == "a.ram.gz shift_jis 0x80000010\n"
    assert cli.main(["find", "学校", str(a), "--absent", str(a)]) == 0
    assert capsys.readouterr().out == ""
    assert cli.main(["contrast", "--same", str(a), "--other", str(b), "--platform", "psp"]) == 0
    assert capsys.readouterr().out.splitlines()[-2:] == ["0x08000014 1, otherwise 0", "5 byte(s)"]


def test_cli_takes_core_options_as_key_value():
    assert cli._option("pcsx_rearmed_memcard1=libretro") == ("pcsx_rearmed_memcard1", "libretro")
    assert cli._option("a=b=c") == ("a", "b=c")
    for odd in ("libretro", "=libretro"):
        with pytest.raises(argparse.ArgumentTypeError):
            cli._option(odd)


def test_a_cartridge_core_gets_the_file(tmp_path):
    """A core that doesn't ask for a full path is handed the game's bytes; one that does gets only the path."""
    from yomikata.replay.libretro import Core

    class FakeLib:
        def __init__(self, need_fullpath):
            self.need_fullpath, self.got = need_fullpath, None

        def retro_get_system_info(self, info):
            info._obj.need_fullpath = self.need_fullpath

        def retro_load_game(self, info):
            self.got = bytes((C.c_char * info._obj.size).from_address(info._obj.data)) if info._obj.data else None
            return True

        def retro_get_system_av_info(self, av):
            av._obj.fps = 60.0

        def retro_set_controller_port_device(self, port, device):
            self.pad = (port, device)

    import ctypes as C
    game = tmp_path / "game.sfc"
    game.write_bytes(b"made up\x00bytes")
    for need_fullpath, want in ((False, b"made up\x00bytes"), (True, None)):
        core = Core.__new__(Core)
        core.lib = FakeLib(need_fullpath)
        core.load_game(game)
        assert core.lib.got == want
    # A core that won't load says why when its OpenGL context couldn't be made (Dolphin without Mesa's EGL).
    class Refusing(FakeLib):
        def retro_load_game(self, info):
            return False
    core = Core.__new__(Core)
    core.lib, core.gl_error = Refusing(True), "EGL has no display to offer"
    with pytest.raises(RuntimeError, match="couldn't load .*asked for OpenGL, and EGL has no display"):
        core.load_game(game)


def test_a_snap_keeps_video_memory_when_the_core_gives_it(tmp_path):
    class WithVideo(FakeCore):
        def video_ram(self):
            return b"made up video memory"

    steps.run(WithVideo(), steps.parse("wait:2,snap"), tmp_path / "a")
    assert gzip.decompress((tmp_path / "a/s000.vram.gz").read_bytes()) == b"made up video memory"
    steps.run(FakeCore(), steps.parse("wait:2,snap"), tmp_path / "b")
    assert not (tmp_path / "b/s000.vram.gz").exists()
