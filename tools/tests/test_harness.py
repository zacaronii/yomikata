"""The harness on a made-up machine: memory that moves with the frames and the buttons, no core and no game."""

import io
import json
import os
import struct

import numpy as np
import pytest

from yomikata import reader as rd
from yomikata.harness import mcp
from yomikata.harness.session import HarnessError, Roots, Session, tools

BASE = 0x80000000
try:
    rd.library()
    READER = True
except rd.ReaderUnavailable:             # pragma: no cover
    if os.environ.get("YOMIKATA_REQUIRE_READER"):
        raise
    READER = False
needs_reader = pytest.mark.skipif(not READER, reason="the reader library isn't built")


class FakeMachine:
    """4 KB of big-endian memory: a frame counter at 0x10, a flag at 0x20 while `a` is held, the stick at 0x24,
    a pointer at 0x40 to a Shift-JIS line at 0x100 that changes every 10 frames."""
    LINES = ["こんにちは", "さようなら", "またあした"]

    def __init__(self, core_path, game, platform, system_dir, options):
        self.platform, self.options, self.frames = platform, options, 0
        self.memory = bytearray(0x1000)
        self._step(set(), (0.0, 0.0))

    def _step(self, buttons, stick):
        m = self.memory
        struct.pack_into(">I", m, 0x10, self.frames)
        m[0x20] = 1 if "a" in buttons else 0
        struct.pack_into(">bb", m, 0x24, round(stick[0] * 100), round(stick[1] * 100))
        struct.pack_into(">I", m, 0x40, BASE + 0x100)
        line = self.LINES[(self.frames // 10) % 3].encode("cp932")
        m[0x100:0x120] = line + bytes(0x20 - len(line))

    def run(self, frames, buttons, stick):
        for _ in range(frames):
            self.frames += 1
            self._step(buttons, stick)

    def picture(self):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        frame[:, :, 0] = self.frames % 256
        return frame

    def ram(self):
        return BASE, bytes(self.memory)

    def read(self, address, size):
        at = address - BASE
        return bytes(self.memory[at:at + size]) if 0 <= at < len(self.memory) else b""

    def regions(self):
        return {"vram": bytes(range(256))}

    def mirrors(self):
        return ()

    def save_state(self):
        return struct.pack(">I", self.frames) + bytes(self.memory)

    def load_state(self, state):
        import gzip
        state = gzip.decompress(state) if state[:2] == b"\x1f\x8b" else state
        if len(state) != 4 + len(self.memory):
            raise RuntimeError("the core refused the savestate")
        self.frames, self.memory = struct.unpack(">I", state[:4])[0], bytearray(state[4:])


@pytest.fixture
def session(tmp_path):
    for name in ("games/ngc", "cores", "inbox/gc/GXXJ01", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/ngc/Made Up (Japan).iso").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    (tmp_path / "secret.txt").write_text("outside")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), FakeMachine)
    s.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc")
    yield s
    s.close()


def test_a_system_folder_of_its_own(tmp_path):
    for name in ("games", "cores", "inbox", "scratch", "bios"):
        (tmp_path / name).mkdir()
    (tmp_path / "games/g.iso").write_bytes(b"x")
    (tmp_path / "cores/c.so").write_bytes(b"x")
    seen = []

    def make(core_path, game, platform, system_dir, options):
        seen.append(system_dir)
        return FakeMachine(core_path, game, platform, system_dir, options)
    roots = Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch")
    Session(roots, make).load("g.iso", "c.so", "ps1")
    roots.system = tmp_path / "bios"
    Session(roots, make).load("g.iso", "c.so", "ps1")
    assert seen == [tmp_path / "scratch/system", tmp_path / "bios"]


def test_load_and_status(session):
    status = session.status()
    assert status["game"] == "ngc/Made Up (Japan).iso" and status["platform"] == "gc" and status["frames"] == 0
    assert session.games() == {"entries": ["ngc/"]}
    assert session.games("ngc") == {"entries": ["Made Up (Japan).iso"]}


def test_paths_that_leave_their_folder_are_refused(session, tmp_path):
    for call in (lambda: session.games(".."), lambda: session.games("../scratch"),
                 lambda: session.load("../secret.txt", "fake_libretro.so", "gc"),
                 lambda: session.load("ngc/Made Up (Japan).iso", "../secret.txt", "gc"),
                 lambda: session.state_load("../../secret.state.gz"),
                 lambda: session.state_save("../x"), lambda: session.snap("a/b")):
        with pytest.raises(HarnessError):
            call()
    # A link out of the folder is followed before the check.
    (tmp_path / "games/ngc/link.iso").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(HarnessError, match="outside"):
        session.load("ngc/link.iso", "fake_libretro.so", "gc")


def test_run_read_and_find(session):
    assert session.run(5, ["a"], [0.5, -1]) == {"frames": 5}
    assert session.read("0x80000010", 4) == {"address": "0x80000010", "hex": "00000005"}
    assert session.read("0x80000020", 6)["hex"] == "0100000032" + "9c"
    assert session.read("0x10", 4, region="vram") == {"offset": "0x10", "hex": "10111213"}
    assert session.find(text="こんにちは")["found"] == {"shift_jis": ["0x80000100"]}
    assert session.find(hex="80000100")["found"] == {"hex": ["0x80000040"]}
    assert session.find(text="ない")["found"] == {}
    for bad in (lambda: session.run(0), lambda: session.run(5, ["z"]), lambda: session.find(),
                lambda: session.read("0x0", 4, region="oam")):
        with pytest.raises(HarnessError):
            bad()


def test_snaps_and_what_changed(session, tmp_path):
    session.run(3)
    session.snap("idle", no_text=True)
    session.run(1, ["a"])
    session.snap("pressed", ocr=["こんにちは"])
    session.run(1)
    session.snap("idle2")
    session.run(1, ["a"])
    session.snap("pressed2")
    loose = session.changed("idle", "pressed")
    assert {p["address"] for p in loose["places"]} == {"0x80000013", "0x80000020"}       # the counter too
    flag = session.changed("idle", "pressed", same_as_a=["idle2"], same_as_b=["pressed2"])
    assert flag == {"count": 1, "places": [{"address": "0x80000020", "a": "00", "b": "01"}]}
    words = session.changed("idle", "pressed", unit=4, same_as_a=["idle2"], same_as_b=["pressed2"])
    assert words["places"] == [{"address": "0x80000020", "a": "00000000", "b": "01000000"}]
    with pytest.raises(HarnessError):
        session.changed("idle", "nothing")
    # Written as an inbox's snapshots are, for `yomikata run` and `check`.
    meta = json.loads((tmp_path / "scratch/snaps/pressed.json").read_text())
    assert meta["platform"] == "gc" and meta["base_address"] == "0x80000000" and meta["ocr"] == ["こんにちは"]
    assert json.loads((tmp_path / "scratch/snaps/idle.json").read_text())["no_text"] is True
    assert json.loads((tmp_path / "scratch/snaps/idle2.json").read_text())["ocr_unavailable"] is True
    assert (tmp_path / "scratch/snaps/pressed.png").read_bytes()[:4] == b"\x89PNG"
    assert (tmp_path / "scratch/snaps/pressed.vram.gz").is_file()


def test_refs(session):
    assert session.refs("0x80000100") == {"count": 1, "refs": [{"at": "0x80000040", "points_at": "0x80000100"}]}
    assert session.refs("0x800000F0", span=0x20)["count"] == 1
    assert session.refs("0x80000100", big_endian=False)["count"] == 0


def test_states(session, tmp_path):
    session.run(7)
    assert session.state_save("seven")["state"] == "seven"
    session.run(20)
    assert session.state_load("seven") == {"loaded": "seven", "frames": 7}
    assert session.read("0x80000010", 4)["hex"] == "00000007"
    assert "seven" in session.status()["states"]
    # A reader's state from the inbox, by its path there.
    import gzip
    (tmp_path / "inbox/gc/GXXJ01/abc.state.gz").write_bytes(gzip.compress(struct.pack(">I", 99) + bytes(0x1000)))
    assert session.state_load("gc/GXXJ01/abc.state.gz")["frames"] == 99
    (tmp_path / "inbox/gc/GXXJ01/bad.state.gz").write_bytes(b"short")
    with pytest.raises(HarnessError, match="refused"):
        session.state_load("gc/GXXJ01/bad.state.gz")
    with pytest.raises(HarnessError):
        session.state_load("never-saved")


SCRIPT = """-- reader: title つくりもの (Made Up)
-- reader: no hooks
if mem.u32be(0x80000040) ~= 0x80000100 then error("this script is for another build of the game", 0) end
reader.on_state_loaded(function() reader.forget() end)
reader.on_frame(function()
    local bytes = mem.cstr(mem.u32be(0x80000040), 32)
    if reader.changed("line", bytes) then
        reader.emit{ text = text.sjis(bytes), window = "w" }
        local cells = {}
        for i = 1, text.len(text.sjis(bytes)) do cells[i] = { 8 * i, 16, 8, 8 } end
        reader.layout("w", { space = { 64, 48 }, glyphs = cells })
    end
end)
"""


@needs_reader
def test_a_script_on_the_running_game(session):
    tried = session.script_try(SCRIPT, "GXXJ01.lua")
    assert tried["loaded"] and ["no hooks", ""] in tried["header"]
    assert session.status()["script"] == "GXXJ01.lua"
    out = session.run(25)
    texts = [(e["frame"], e["text"]) for e in out["events"] if e["kind"] == "text"]
    assert texts == [(1, "こんにちは"), (10, "さようなら"), (20, "またあした")]
    assert session.overlay()[1][:4] == b"\x89PNG"
    assert session.lua("mem.u32be(0x80000010), text.sjis(mem.cstr(0x80000100))") == {"result": "25\tまたあした"}
    # After a savestate the script starts over.
    session.state_save("here")
    session.state_load("here")
    assert [e["text"] for e in session.run(1)["events"] if e["kind"] == "text"] == ["またあした"]
    # A script for another build doesn't load, and says why.
    tried = session.script_try("error('this script is for another build of the game', 0)")
    assert tried["loaded"] is False and "another build" in tried["error"]
    assert session.run(1) == {"frames": 27}


@needs_reader
def test_lua_without_a_script(session):
    assert session.lua("mem.u8(0x80000020)") == {"result": "0"}
    assert "error" in session.lua("mem.w8(0x80000020, 1)")
    assert session.lua("mem.region('vram').u8(5)") == {"result": "5"}


def test_screenshot(session):
    with pytest.raises(HarnessError):
        session.overlay()
    kind, png = session.screenshot(2)
    from PIL import Image
    assert kind == "image" and Image.open(io.BytesIO(png)).size == (128, 96)


def test_mcp(session):
    def ask(method, params=None, ident=1):
        return mcp.handle(session, {"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})
    assert ask("initialize")["result"]["serverInfo"]["name"] == "yomikata-harness"
    assert mcp.handle(session, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    listed = ask("tools/list")["result"]["tools"]
    assert {t["name"] for t in listed} == set(tools(session)) and len(listed) >= 14
    for t in listed:
        assert t["description"] and t["inputSchema"]["type"] == "object"
        assert set(t["inputSchema"]["required"]) <= set(t["inputSchema"]["properties"])
    run = next(t for t in listed if t["name"] == "run")
    assert run["inputSchema"]["required"] == ["frames"]
    result = ask("tools/call", {"name": "run", "arguments": {"frames": 2}})["result"]
    assert json.loads(result["content"][0]["text"]) == {"frames": 2}
    shot = ask("tools/call", {"name": "screenshot", "arguments": {}})["result"]["content"][0]
    assert shot["type"] == "image" and shot["mimeType"] == "image/png"
    for bad in ({"name": "run", "arguments": {"frames": 0}}, {"name": "run", "arguments": {"nonsense": 1}},
                {"name": "games", "arguments": {"platform": ".."}}, {"name": "no-such-tool"}):
        assert ask("tools/call", bad)["result"]["isError"] is True
    assert ask("resources/list")["error"]["code"] == -32601
    # A tool that breaks in a way nobody foresaw ends that call, not the session.
    session.status = lambda: {}["missing"]
    broken = ask("tools/call", {"name": "status"})["result"]
    assert broken["isError"] is True and "KeyError" in broken["content"][0]["text"]
    assert ask("ping")["result"] == {}


def test_mcp_over_lines(session):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), "",
             "not json",
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "status", "arguments": {}}})]
    out = io.StringIO()
    mcp.serve(session, io.StringIO("\n".join(lines) + "\n"), out)
    answers = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [a.get("id") for a in answers] == [1, None, 2]
    assert answers[1]["error"]["code"] == -32700
    assert json.loads(answers[2]["result"]["content"][0]["text"])["platform"] == "gc"


# ---- run_until, steps, sheets, the call's time, compare ----

def test_run_until_a_place_changes_or_holds_a_value(session):
    # The line's first bytes change every 10 frames; the frame counter's low byte counts.
    out = session.run_until(100, address="0x80000100", size=2)
    assert (out["stopped"], out["ran"], out["frames"]) == ("changed", 10, 10)
    out = session.run_until(100, address="0x80000013", equals="25")
    assert (out["stopped"], out["frames"], out["value"]) == ("equals", 25, "0x19")
    out = session.run_until(7, address="0x80000013", equals="0x99")
    assert (out["stopped"], out["ran"]) == ("limit", 7)
    out = session.run_until(50, address="0x80000020", buttons=["a"])            # the flag goes up with the button
    assert (out["stopped"], out["ran"]) == ("changed", 1)
    for bad in (lambda: session.run_until(10), lambda: session.run_until(0, still=3),
                lambda: session.run_until(10, address="0x80000010", size=3),
                lambda: session.run_until(10, event="text"), lambda: session.run_until(10, event="nonsense")):
        with pytest.raises(HarnessError):
            bad()


def test_run_until_the_picture_stands_still(session):
    session.machine.picture = lambda: np.full((4, 4, 3), min(session.machine.frames, 12), dtype=np.uint8)
    out = session.run_until(100, still=5)
    assert (out["stopped"], out["frames"]) == ("still", 17)          # it stops changing at 12; five more the same


@needs_reader
def test_run_until_the_script_says_something(session):
    assert session.script_try(SCRIPT)["loaded"]
    assert session.run(1)["events"][0]["text"] == "こんにちは"      # the line that is up is said at once
    out = session.run_until(100, event="dialogue")
    assert out["stopped"] == "event" and out["frames"] == 10
    assert [e["text"] for e in out["events"] if e["kind"] == "text"] == ["さようなら"]
    assert session.run_until(5, event="choice")["stopped"] == "limit"


def test_steps_in_one_call_with_snaps_and_a_sheet(session, tmp_path):
    kind, out, png = session.steps([{"buttons": ["a"], "hold": 3, "wait": 2, "snap": True, "shot": True},
                                    {"until": {"limit": 100, "address": "0x80000100", "size": 2}, "shot": True},
                                    {"wait": 4, "times": 3, "snap": True}], prefix="walk")
    assert kind == "both" and png[:4] == b"\x89PNG"
    assert [s["frames"] for s in out["steps"]] == [5, 10, 14, 18, 22]
    assert out["steps"][1]["stopped"] == "changed"
    assert [s.get("snap") for s in out["steps"]] == ["walk-000", None, "walk-002", "walk-003", "walk-004"]
    assert (tmp_path / "scratch/snaps/walk-003.ram.gz").is_file() and "walk-004" in session.status()["snaps"]
    from PIL import Image
    sheet = Image.open(io.BytesIO(png))
    assert sheet.size == (2 * 64, 48 + 12)                              # two pictures, a strip above each for its label
    assert session.steps([{"wait": 1}]) == {"frames": 23, "steps": [{"step": 0, "frames": 23}]}     # no pictures
    for bad in ([], [{"wait": 1, "jump": 2}], [{"buttons": ["z"]}], [{"until": {"address": "0x80000010"}}],
                [{"wait": 1, "times": 500}, {"wait": 1, "times": 500}], [{"until": {"limit": 5, "what": 1}}]):
        with pytest.raises(HarnessError):
            session.steps(bad)
    kind, png = session.sheet(["walk-000", "walk-002", "walk-003"], columns=2, scale=0.5)
    assert kind == "image" and Image.open(io.BytesIO(png)).size == (64, 72)
    with pytest.raises(HarnessError):
        session.sheet(["nothing"])


def test_a_call_stops_when_its_time_is_up(session):
    import time
    session.deadline = time.monotonic() - 1                 # the call's time is already up
    assert session.run(500) == {"frames": 0, "ran": 0, "stopped": "time"}
    assert session.run_until(500, address="0x80000100")["stopped"] == "time"
    out = session.steps([{"wait": 5, "snap": True}, {"wait": 5}])
    assert out["stopped"] == "time" and out["left"] == 2 and out["steps"] == [{"step": 0, "frames": 0, "stopped": "time"}]
    session.call_seconds = 30
    session.begin_call()
    assert session.run(100) == {"frames": 100}              # in time: as before
    # Through the server each call gets its own time.
    session.call_seconds = 0
    answer = mcp.call(session, "run", {"frames": 50})
    assert json.loads(answer["content"][0]["text"]) == {"frames": 100, "ran": 0, "stopped": "time"}


def test_a_call_that_never_comes_back_is_given_up(session):
    import time
    left = []
    session.run = None
    slow = lambda **_: time.sleep(0.6) or {"late": True}            # noqa: E731
    session.status.__func__.tool                                     # (the tools are looked up on the class)
    original = mcp.tools
    mcp.tools = lambda s: {**original(s), "sleep": ("sleeps", {}, slow)}
    try:
        lines = [json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "sleep"}}),
                 json.dumps({"jsonrpc": "2.0", "id": 8, "method": "ping"})]
        out = io.StringIO()
        mcp.serve(session, io.StringIO("\n".join(lines) + "\n"), out, give_up=0.1, leave=left.append, tick=0.05)
    finally:
        mcp.tools = original
    answers = [json.loads(line) for line in out.getvalue().splitlines()]
    assert left == [3]
    assert [a["id"] for a in answers] == [7, 8]                     # the late answer isn't sent a second time
    assert answers[0]["result"]["isError"] and "didn't come back" in answers[0]["result"]["content"][0]["text"]


@needs_reader
def test_compare_a_script_with_the_lines_on_screen_and_with_a_layout(session):
    session.steps([{"wait": 1, "snap": True}, {"wait": 10, "snap": True}, {"wait": 10, "snap": True}], prefix="c")
    session.snap_lines("c-000", ocr=["こんにちは"])
    session.snap_lines("c-001", ocr=["さようなら！"])               # a misread mark: still 80% and more
    session.snap_lines("c-002", no_text=True)                       # wrongly: the script does read a line there
    out = session.compare(SCRIPT, show=True)
    assert out["script"] == {"snapshots": 3, "reading something": 3, "reading nothing": 0, "the script didn't load": 0,
                             "with lines read off the screen": 2, "of those, agreeing 80% or more": 2,
                             "marked as showing no text": 1, "of those, reading nothing": 0}
    assert [r["read"] for r in out["rows"]] == ["こんにちは", "さようなら", "またあした"] and "layout" not in out
    entry = {"id": "GXXJ01", "platform": "gc", "encoding": "shift_jis",
             "layouts": [{"message": {"address": "0x80000100", "charset": "shift_jis", "max_bytes": 32,
                                      "terminator": "00"}}]}
    out = session.compare(SCRIPT, snaps=["c-000", "c-001"], entry=entry)
    assert out["layout"] == {"snapshots": 2, "the same": 2, "nothing in either": 0, "different": 0,
                             "the script didn't load": 0}
    entry["layouts"][0]["message"]["address"] = "0x80000102"        # a layout that reads from the second character
    out = session.compare(SCRIPT, snaps=["c-000"], entry=entry, show=True)
    assert out["layout"]["different"] == 1 and out["differing"][0]["layout"]["message"] == "んにちは"
    assert session.compare("error('no', 0)", snaps=["c-000"])["script"]["the script didn't load"] == 1
    for bad in (lambda: session.compare(SCRIPT, snaps=["nothing"]), lambda: session.snap_lines("nothing", no_text=True),
                lambda: session.snap_lines("c-000"), lambda: session.compare(SCRIPT, snaps=["c-000"], entry={"id": "x"})):
        with pytest.raises(HarnessError):
            bad()

