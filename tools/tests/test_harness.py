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
        struct.pack_into(">hh", self.memory, 0x30, 100, 80)
        self._step(set(), (0.0, 0.0))

    def _step(self, buttons, stick):
        m = self.memory
        struct.pack_into(">I", m, 0x10, self.frames)
        m[0x20] = 1 if "a" in buttons else 0
        struct.pack_into(">bb", m, 0x24, round(stick[0] * 100), round(stick[1] * 100))
        struct.pack_into(">I", m, 0x40, BASE + 0x100)
        x, y = struct.unpack_from(">hh", m, 0x30)                       # a cursor the arrows move, two a frame
        if "b" in buttons:
            m[0x34] = 0                                                 # b closes the menu that holds the cursor
        if not m[0x34]:
            x += 2 * (("right" in buttons) - ("left" in buttons))
            y += 2 * (("down" in buttons) - ("up" in buttons))
        struct.pack_into(">hh", m, 0x30, max(0, min(x, 319)), max(0, min(y, 239)))
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

    def write(self, address, data):
        at = address - BASE
        size = max(0, min(len(data), len(self.memory) - at)) if 0 <= at < len(self.memory) else 0
        self.memory[at:at + size] = data[:size]
        return size

    def regions(self):
        return {"vram": bytes(range(256))}

    def core_options(self):
        given = {"given": self.options["fake_clock"]} if "fake_clock" in self.options else {}
        return {"fake_clock": {"what": "The clock", "values": ["real", "fixed"], **given}}

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
                             "marked as showing no text": 1, "of those, reading nothing": 0,
                             "taken after a memory write": 0}
    assert [r["read"] for r in out["rows"]] == ["こんにちは", "さようなら", "またあした"] and "layout" not in out
    named = SCRIPT.replace('window = "w" }', 'window = "w", speaker = "ミカ" }')
    assert session.compare(named, snaps=["c-000"], show=True)["rows"][0]["read"] == "ミカ\nこんにちは"   # with who says it
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


# ---- memory writes ----

def test_memory_writes_are_off_unless_asked_for(session):
    assert "poke" not in tools(session) and "memory_writes" not in session.status()
    with pytest.raises(HarnessError, match="--allow-poke"):
        session.poke("0x80000200", "01", "set the chapter flag")
    answer = mcp.call(session, "poke", {"address": "0x80000200", "hex": "01", "why": "set the chapter flag"})
    assert answer["isError"] and "no such tool" in answer["content"][0]["text"]


def test_a_memory_write_is_recorded_and_marks_what_follows(session, tmp_path):
    session.allow_poke = True
    assert "poke" in tools(session) and session.status()["memory_writes"] == 0
    session.run(3)
    session.snap("before")
    out = session.poke("0x80000200", "0102", "set the chapter flag to the second chapter")
    assert out == {"written": 2, "address": "0x80000200", "before": "0000", "write": 1}
    assert session.read("0x80000200", 2)["hex"] == "0102"
    session.run(2)
    assert session.read("0x80000200", 2)["hex"] == "0102"           # the game goes on with it
    session.poke("0x80000100", "8140", "a made-up line in the text buffer", synthetic=True)
    assert session.snap("after")["memory_writes"] == 2 and session.state_save("there")["memory_writes"] == 2
    assert session.status()["memory_writes"] == 2
    meta = lambda name: json.loads((tmp_path / f"scratch/snaps/{name}.json").read_text())      # noqa: E731
    assert "memory_writes" not in meta("before") and meta("after")["memory_writes"] == 2
    record = [json.loads(line) for line in (tmp_path / "scratch/record/memory-writes.jsonl").read_text().splitlines()]
    assert [(r.get("address"), r.get("before"), r.get("after"), r.get("synthetic")) for r in record[:2]] == [
        ("0x80000200", "0000", "0102", False), ("0x80000100", record[1]["before"], "8140", True)]
    assert record[0]["why"] == "set the chapter flag to the second chapter" and record[0]["frame"] == 3
    assert record[0]["game"] == "ngc/Made Up (Japan).iso" and record[2] == {
        "game": "ngc/Made Up (Japan).iso", "frame": 5, "state_saved": "there", "after_writes": 2}
    for bad in (lambda: session.poke("0x80000200", "01", "why"), lambda: session.poke("0x80000200", "", "set a flag here"),
                lambda: session.poke("0x80000200", "zz", "set a flag here"),
                lambda: session.poke("0x80000FFF", "0102", "runs off the end of memory"),
                lambda: session.poke("0x90000000", "01", "outside main memory"),
                lambda: session.poke("0x80000200", "00" * 257, "too much at once")):
        with pytest.raises(HarnessError):
            bad()
    assert session.status()["memory_writes"] == 2
    # A state saved after writes says so in a file beside it: loading it, now or in another session, sets the
    # count, and what is snapped from there is marked. A state from before the writes clears it.
    assert json.loads((tmp_path / "scratch/states/there.writes.json").read_text()) == {"memory_writes": 2}
    session.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc")
    session.state_save("clean")
    assert not (tmp_path / "scratch/states/clean.writes.json").exists()
    later = Session(session.roots, FakeMachine)
    later.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc")
    assert later.state_load("there")["memory_writes"] == 2 and later.snap("from-there")["memory_writes"] == 2
    assert meta("from-there")["memory_writes"] == 2
    assert "memory_writes" not in later.state_load("clean") and "memory_writes" not in later.snap("from-clean")
    later.close()
    session.allow_poke = True
    session.state_load("there")
    session.state_save("there")                                       # saved again, still after writes
    session.state_load("clean")
    session.state_save("there")                                       # and overwritten from a clean moment
    assert not (tmp_path / "scratch/states/there.writes.json").exists()
    # Loading the game anew starts the count again.
    session.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc")
    assert session.status()["memory_writes"] == 0


@needs_reader
def test_compare_says_which_snaps_follow_a_memory_write(session):
    session.allow_poke = True
    session.snap("clean")
    session.poke("0x80000200", "01", "set the chapter flag")
    session.snap("reached")
    out = session.compare(SCRIPT, show=True)
    assert out["script"]["taken after a memory write"] == 1
    assert [(r["snap"], r["written"]) for r in out["rows"]] == [("clean", False), ("reached", True)]


# ---- repeat ----

def test_repeat_says_whether_the_game_ends_up_the_same(session):
    session.run(7)
    out = session.repeat(frames=25)
    assert out == {"runs": 2, "frames": 25, "from": "this moment", "same": True, "bytes_differing": [0]}
    assert session.status()["frames"] == 32                         # it stands where the last run ended
    out = session.repeat(steps=[{"buttons": ["a"], "hold": 2, "wait": 3, "snap": True, "shot": True}, {"wait": 5}], runs=3)
    assert (out["same"], out["frames"], out["bytes_differing"]) == (True, 10, [0, 0])
    assert session.status()["snaps"] == []                          # a repeat keeps no snaps
    out = session.repeat(frames=12, power_on=True)
    assert (out["same"], out["from"], session.status()["frames"]) == (True, "power-on", 12)
    for bad in (lambda: session.repeat(), lambda: session.repeat(frames=5, steps=[{"wait": 1}])):
        with pytest.raises(HarnessError):
            bad()


def test_repeat_finds_a_game_that_does_not_repeat(session):
    import itertools
    clock = itertools.count(1)
    step = session.machine._step

    def with_a_clock(buttons, stick):                               # a cell that follows the world's time, not the game's
        step(buttons, stick)
        session.machine.memory[0x300] = next(clock) % 256
    session.machine._step = with_a_clock
    out = session.repeat(frames=5)
    assert out["same"] is False and out["bytes_differing"] == [1] and out["first_places"] == ["0x80000300"]
    session.machine.picture = lambda: np.full((4, 4, 3), next(clock) % 256, dtype=np.uint8)
    assert session.repeat(frames=5)["also_differing"] == ["picture"]


def test_repeat_gives_up_when_the_calls_time_is_short(session):
    import time
    session.deadline = time.monotonic() - 1
    with pytest.raises(HarnessError, match="shorter"):
        session.repeat(frames=50)


def test_differs_from_a_snap_of_another_session(session):
    session.run(10)
    session.snap("at-10")
    assert session.differs("at-10") == {"same": True, "bytes_differing": 0, "frame": 10, "snap_frame": 10,
                                        "first_places": []}
    other = Session(session.roots, FakeMachine)
    other.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc")
    other.run(10)
    assert other.differs("at-10")["same"]
    other.run(1)
    out = other.differs("at-10")
    assert (out["same"], out["bytes_differing"], out["first_places"]) == (False, 1, ["0x80000013"])
    with pytest.raises(HarnessError):
        other.differs("nothing")
    other.close()


def test_a_core_that_loads_only_once(session):
    session.machine.once = True
    session.once = True
    with pytest.raises(HarnessError, match="start the harness again"):
        session.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc")
    with pytest.raises(HarnessError, match="differs"):
        session.repeat(frames=5, power_on=True)
    assert session.repeat(frames=5)["same"]                         # from this moment it still can


# ---- finding text ----

def put(session, at, data):
    session.machine.memory[at:at + len(data)] = data


def test_find_in_every_encoding_and_with_the_games_own_table(session):
    line = "きょうはあめ"
    put(session, 0x400, line.encode("utf-16-be"))
    put(session, 0x500, b"".join(ch.encode("cp932")[::-1] for ch in line))             # 16-bit cells
    put(session, 0x600, bytes([0x21, 0x22, 0x23, 0x24, 0x25, 0x26]))                   # the game's own codes
    table = "\n".join(f"{0x21 + i:02X}={ch}" for i, ch in enumerate(line))
    found = session.find(text=line, table=table)["found"]
    assert found == {"utf16be": ["0x80000400"], "sjis_le16": ["0x80000500"], "table": ["0x80000600"]}
    assert session.find(text=line, encodings=["table"], table=table)["found"] == {"table": ["0x80000600"]}
    assert session.find(text="ない", table=table)["found"] == {}                       # not in the table: not looked for
    # A line that isn't there whole: its stretches that are.
    found = session.find(text="きょうは　ゆき", pieces=3, table=table)["found"]
    assert set(found) == {"pieces"}
    assert found["pieces"]["utf16be"] == {"きょう": ["0x80000400"], "ょうは": ["0x80000402"]}
    assert found["pieces"]["sjis_le16"] == {"きょう": ["0x80000500"], "ょうは": ["0x80000502"]}
    assert found["pieces"]["table"] == {"きょう": ["0x80000600"], "ょうは": ["0x80000601"]}
    for bad in (lambda: session.find(text="あ", encodings=["klingon"]), lambda: session.find(text="あ", encodings=["table"]),
                lambda: session.find(text="あ", table="not a table")):
        with pytest.raises(HarnessError):
            bad()


def test_find_and_runs_across_snaps(session):
    session.snap("one")                                               # こんにちは
    session.run(10)
    session.snap("two")                                               # さようなら
    found = session.find(text="さようなら", snaps=["one", "two"])["found"]
    assert found == {"one": {}, "two": {"shift_jis": ["0x80000100"]}}
    put(session, 0x700, "きょうはあめがふっています".encode("cp932"))
    put(session, 0x801, b"".join(ch.encode("cp932")[::-1] for ch in "あしたはゆきがふるでしょう"))    # at an odd address
    runs = session.text_runs()["runs"]
    assert [(r["address"], r["encoding"], r["text"]) for r in runs] == [
        ("0x80000700", "shift_jis", "きょうはあめがふっています"), ("0x80000801", "sjis_le16", "あしたはゆきがふるでしょう")]
    assert session.text_runs(snaps=["one"])["runs"] == {"one": []}                      # five characters: too short
    with pytest.raises(HarnessError):
        session.find(text="あ", snaps=["nothing"])


def test_find_relative_a_line_in_codes_of_the_games_own(session):
    line = "ははのははははがいたい"
    own = {ch: 0x130 + 7 * i for i, ch in enumerate(dict.fromkeys(line))}              # codes in no usual order
    put(session, 0x900, b"".join(struct.pack(">H", own[ch]) for ch in line))
    out = session.find_relative(line, unit=2)
    assert out["count"] == 1 and out["places"][0]["address"] == "0x80000900"
    assert out["places"][0]["codes"] == {ch: f"{code:04X}" for ch, code in own.items()}
    assert session.find_relative(line, unit=1)["count"] == 0
    # With a second cell after each character, as tile windows have.
    put(session, 0xA00, b"".join(struct.pack(">HH", own[ch], 0x7777) for ch in line))
    assert [p["address"] for p in session.find_relative(line, unit=2, stride=2)["places"]] == ["0x80000A00"]
    # Kana numbered as in JIS: a short line without repeats is found by how far apart its kana are.
    word = "かきくけこ"
    put(session, 0xB00, bytes(0x80 + (ord(ch) - ord("ぁ")) for ch in word))
    out = session.find_relative(word, order="unicode")
    assert out["count"] == 1 and out["places"][0]["address"] == "0x80000B00" and out["places"][0]["codes"]["か"] == "8A"
    put(session, 0xC00, bytes(0x10 + search_gojuon(ch) for ch in "さくらのはな"))
    assert [p["address"] for p in session.find_relative("さくらのはな", order="gojuon")["places"]] == ["0x80000C00"]
    for bad in (lambda: session.find_relative("あいう"),
                lambda: session.find_relative(line, unit=3), lambda: session.find_relative(word, order="iroha"),
                lambda: session.find_relative(), lambda: session.find_relative(line, lines={"now": line})):
        with pytest.raises(HarnessError):
            bad()


def test_find_relative_over_two_moments_and_over_line_breaks(session):
    # Two lines without a repeat in either, on screen at two moments at the same place: what they share ties
    # the place down.
    own = {ch: 0x40 + 3 * i for i, ch in enumerate(dict.fromkeys("\u3000ボロのバットをてにいれたはなす"))}
    row = lambda text: b"".join(struct.pack(">H", own[ch]) for ch in text)             # noqa: E731
    put(session, 0xD00, row("\u3000ボロのバットを"))
    put(session, 0xE00, row("のをなボすロ"))                                             # a decoy for the first line alone
    session.snap("first")
    put(session, 0xD00, row("\u3000てにいれたのはバット"))
    session.snap("second")
    with pytest.raises(HarnessError, match="too little"):
        session.find_relative("\u3000ボロのバットを", unit=2)
    out = session.find_relative(lines={"first": "\u3000ボロのバットを", "second": "\u3000てにいれたのはバット"}, unit=2)
    assert out["count"] == 1 and out["places"][0]["address"] == "0x80000D00"
    assert out["places"][0]["codes"]["バ"] == f"{own['バ']:04X}" and len(out["places"][0]["codes"]) == 14
    out = session.find_relative(lines={"first": "\u3000ボロのバットを", "now": "\u3000てにいれたのはバット"}, unit=2)
    assert out["count"] == 1
    # A line over two rows, with one code for the line break between them; and with three.
    put(session, 0xF00, row("はなのは") + b"\xFF\xFE" + row("なはいれた"))
    assert session.find_relative("はなのは\nなはいれた", unit=2)["places"][0]["address"] == "0x80000F00"
    assert session.find_relative("はなのは\nなはいれた", unit=2, gap=3)["count"] == 0
    assert session.find_relative("はなのは\nなはいれた", unit=2, gap=0)["count"] == 0
    # Codes of the game's among the line's: the whole doesn't fit, its rows two at a time do.
    put(session, 0xF40, row("はなのはな") + b"\xFF\xFD\xFF\xFE" + row("なはいれたは") + b"\xFF\xFE" + row("のはなのは"))
    out = session.find_relative("はなのはな\nなはいれたは\nのはなのは", unit=2)
    assert out["count"] == 0 and [(p["rows"], p["places"][0]["address"]) for p in out["parts"]] == [([2, 3], "0x80000F4E")]


def search_gojuon(ch):
    from yomikata.harness.search import GOJUON
    return GOJUON.index(ch)


def test_core_options(session):
    assert session.core_options() == {"options": {"fake_clock": {"what": "The clock", "values": ["real", "fixed"]}}}
    session.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc", options={"fake_clock": "fixed"})
    assert session.core_options()["options"]["fake_clock"]["given"] == "fixed"


# ---- waypoints ----

def test_a_waypoint_is_reached_by_its_state_or_by_its_moves(session, tmp_path):
    session.run(3)
    session.steps([{"buttons": ["a"], "hold": 2, "wait": 5}, {"until": {"limit": 50, "address": "0x80000100", "size": 2}}])
    session.run(4, ["b"], [0.5, 0])
    out = session.waypoint_save("first-line", note="the second line is up")
    assert out == {"waypoint": "first-line", "from": "power-on", "moves": 4, "frames": 24}
    kept = json.loads((tmp_path / "scratch/waypoints/first-line.json").read_text())
    assert kept["moves"] == [[3, [], [0.0, 0.0]], [2, ["a"], [0.0, 0.0]], [15, [], [0.0, 0.0]], [4, ["b"], [0.5, 0.0]]]
    assert kept["game"] == "ngc/Made Up (Japan).iso" and len(kept["core_sha256"]) == 64 and kept["frames"] == 24
    assert session.waypoints() == {"waypoints": {"first-line": {
        "game": "ngc/Made Up (Japan).iso", "core": "fake_libretro.so", "from": "power-on", "frames": 24,
        "note": "the second line is up"}}}
    memory = session.read("0x80000000", 0x200)
    session.run(50)
    # Back by its state: the core is the build that made it.
    out = session.waypoint_go("first-line")
    assert (out["via"], out["frames"], out["picture_same"]) == ("state", 24, True)
    assert session.read("0x80000000", 0x200) == memory
    # By its moves, in a harness that has nothing loaded: the game is loaded and played to it.
    other = Session(session.roots, FakeMachine)
    out = other.waypoint_go("first-line", replay=True)
    assert (out["via"], out["frames"], out["picture_same"], out["note"]) == ("replay", 24, True, "the second line is up")
    assert other.read("0x80000000", 0x200) == memory and other.status()["game"] == "ngc/Made Up (Japan).iso"
    # A way goes on from a waypoint: the next one holds all of it.
    other.run(6)
    assert other.waypoint_save("further")["moves"] == 5
    other.close()
    # Another build of the core: its state isn't tried, the moves are played, and a state of this build is kept.
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"a newer core")
    newer = Session(session.roots, FakeMachine)
    out = newer.waypoint_go("first-line")
    assert (out["via"], out["picture_same"]) == ("replay", True)
    assert newer.waypoint_go("first-line")["via"] == "state"
    newer.close()
    with pytest.raises(HarnessError):
        session.waypoint_go("nowhere")


def test_a_waypoint_whose_picture_is_no_longer_reached(session, tmp_path):
    session.run(5)
    session.waypoint_save("there")
    session.machine.picture = lambda: np.full((48, 64, 3), 9, dtype=np.uint8)      # the game shows something else now
    out = session.waypoint_go("there", replay=False)
    assert out["picture_same"] is False and out["pixels_differing"] == 1.0


def test_a_way_from_a_state_and_a_way_with_a_write(session, tmp_path):
    session.allow_poke = True
    session.run(4)
    session.state_save("early")                                       # a state keeps the way to it
    session.run(3)
    session.state_load("early")
    session.run(2, ["a"])
    assert session.waypoint_save("via-state")["moves"] == 2 and session.route["base"] == "power-on"
    # A state whose way isn't known (a reader's, from the inbox): the way starts at the state.
    (tmp_path / "scratch/states/early.route.json").unlink()
    session.state_load("early")
    session.run(2)
    out = session.waypoint_save("from-state")
    assert out["from"] == {"state": "early"} and out["moves"] == 1
    assert session.waypoint_go("from-state", replay=True)["picture_same"]
    # A write on the way is part of it, and of the waypoint.
    session.poke("0x80000200", "07", "set the chapter flag")
    session.run(1)
    out = session.waypoint_save("written")
    assert out["memory_writes"] == 1 and session.waypoints()["waypoints"]["written"]["memory_writes"] == 1
    session.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc")
    out = session.waypoint_go("written", replay=True)
    assert out["picture_same"] and session.read("0x80000200", 1)["hex"] == "07" and session.status()["memory_writes"] == 1
    session.allow_poke = False
    with pytest.raises(HarnessError, match="--allow-poke"):
        session.waypoint_go("written", replay=True)


def test_repeat_leaves_the_way_as_one_run(session):
    session.run(3)
    session.repeat(frames=5, runs=3)
    assert session.route["moves"] == [[8, [], [0.0, 0.0]]]


# ---- what a first script needs: which game this is, and what its build check can read ----

def test_identify_says_what_it_can(session, tmp_path):
    rom = bytearray(0x10000)
    rom[0xFFC0:0xFFD5] = b"MADE UP GAME         "
    rom[0xFFD5:0xFFE0] = bytes([0x31, 0x02, 0x0A, 0x03, 0x00, 0x33, 0x00, 0x34, 0x12, 0xCB, 0xED])
    rom[0xFFB0:0xFFB6] = b"01ZZ  "
    (tmp_path / "games/snes").mkdir()
    (tmp_path / "games/snes/Made Up (Japan).sfc").write_bytes(bytes(rom))
    out = session.identify("snes/Made Up (Japan).sfc")
    assert out["platform"] == "sfc" and len(out["header_crc32"]) == 8 and out["id"]
    with pytest.raises(HarnessError, match="couldn't be identified"):
        session.identify()                                          # the loaded "game" is six bytes of nothing
    with pytest.raises(HarnessError):
        session.identify("../secret.txt")


def test_suggest_build_words(session):
    m = session.machine.memory
    for at, word in ((0x200, 0x48001234), (0x400, 0x4BFFFF01), (0x800, 0x48000F55), (0xA00, 0x48000000)):
        struct.pack_into(">I", m, at, word)                         # branches of a PowerPC program; the last to nowhere
    session.run(1); session.snap("early")
    session.run(30); session.snap("title")
    struct.pack_into(">I", m, 0x400, 0x4BFFFF99)                    # a part of the program loaded later
    session.run(30); session.snap("play")
    out = session.suggest_build_words(["early", "title", "play"])
    assert out["candidates"] == 2 and [w["address"] for w in out["words"]] == ["0x80000200", "0x80000800"]
    assert out["lua"].startswith("if mem.u32be(0x80000200) ~= 0x48001234 or mem.u32be(0x80000800) ~= 0x48000F55 then")
    assert out["snap_frames"] == [1, 31, 61]
    # Another build has the first of them too: only what differs there will do.
    session.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc")
    struct.pack_into(">I", session.machine.memory, 0x200, 0x48001234)
    session.snap("other")
    out = session.suggest_build_words(["early", "title", "play"], others=["other"])
    assert [w["address"] for w in out["words"]] == ["0x80000800"]
    with pytest.raises(HarnessError, match="three or more"):
        session.suggest_build_words(["early", "title"])


def test_suggest_build_words_where_the_program_is_not_in_memory(session):
    session.machine.platform = "sfc"
    put(session, 0x300, bytes(range(0x21, 0x2D)))                   # a table the game writes as it starts
    put(session, 0x500, b"\x55" * 40)                              # a filled stretch: one value, no use
    for name in ("a", "b", "c"):
        session.run(7)
        session.snap(name)
    out = session.suggest_build_words(["a", "b", "c"])
    assert out["words"][0] == {"address": "0x80000300", "bytes": bytes(range(0x21, 0x2D)).hex()}
    assert out["lua"].startswith('if mem.read(0x80000300, 12) ~= "\\x21\\x22')
    session.machine.memory[:] = bytes(len(session.machine.memory))
    session.machine._step = lambda *_: None
    for name in ("d", "e", "f"):
        session.snap(name)
    out = session.suggest_build_words(["d", "e", "f"])
    assert out["words"] == [] and "reader.game_id" in out["note"]


# ---- snaps kept as what differs from the session's first ----

def test_snapshot_files_whole_and_as_a_difference(tmp_path):
    import gzip
    from yomikata import snapfile
    base = bytes(range(256)) * 400
    later = bytearray(base)
    later[10:14] = b"\x00\x00\x00\x00"
    later[20] = 0xFF                                                 # near the first: one stretch
    later[90000:90003] = b"abc"
    assert snapfile.write(tmp_path / "a.ram.gz", base) is False
    assert snapfile.write(tmp_path / "b.ram.gz", bytes(later), ("a.ram.gz", base)) is True
    assert snapfile.read(tmp_path / "a.ram.gz") == base and snapfile.read(tmp_path / "b.ram.gz") == bytes(later)
    assert (tmp_path / "b.ram.gz").stat().st_size < 200 < (tmp_path / "a.ram.gz").stat().st_size
    body = gzip.decompress((tmp_path / "b.ram.gz").read_bytes())
    assert body.startswith(b"YKDELTA1\n") and body.count(b"abc") == 1
    # The same memory again: a difference of nothing. Mostly different, or of another size: written whole.
    assert snapfile.write(tmp_path / "c.ram.gz", base, ("a.ram.gz", base)) is True and snapfile.read(tmp_path / "c.ram.gz") == base
    assert snapfile.write(tmp_path / "d.ram.gz", bytes(len(base)), ("a.ram.gz", base)) is False
    assert snapfile.write(tmp_path / "e.ram.gz", base[:100], ("a.ram.gz", base)) is False
    # Not gzipped at all, as some tools write memory: read as it is.
    (tmp_path / "plain.bin").write_bytes(base)
    assert snapfile.read(tmp_path / "plain.bin") == base
    # Its base gone, or changed: said, not read wrongly.
    (tmp_path / "a.ram.gz").write_bytes(gzip.compress(bytes(len(base))))
    with pytest.raises(snapfile.SnapError, match="changed since"):
        snapfile.read(tmp_path / "b.ram.gz")
    snapfile.write(tmp_path / "a.ram.gz", base)
    assert snapfile.expand(tmp_path / "b.ram.gz") is True and snapfile.expand(tmp_path / "b.ram.gz") is False
    (tmp_path / "a.ram.gz").unlink()
    assert snapfile.read(tmp_path / "b.ram.gz") == bytes(later)      # whole now: it needs no other
    with pytest.raises(snapfile.SnapError, match="isn't beside it"):
        snapfile.read(tmp_path / "c.ram.gz")


def test_a_sessions_later_snaps_are_small_and_read_the_same(session, tmp_path):
    import gzip
    from yomikata import cli, layout, snapfile
    from yomikata.run import _load, _regions
    session.machine.memory[0x200:0x1000] = os.urandom(0xE00)         # memory that doesn't shrink by itself
    session.snap("s-0")
    session.run(10)
    session.snap("s-1")
    session.run(10)
    session.snap("s-2")
    folder = tmp_path / "scratch/snaps"
    sizes = [(folder / f"s-{i}.ram.gz").stat().st_size for i in range(3)]
    assert sizes[0] > 3000 and sizes[1] < 300 and sizes[2] < 300
    assert gzip.decompress((folder / "s-2.ram.gz").read_bytes()).startswith(b"YKDELTA1")
    assert gzip.decompress((folder / "s-2.vram.gz").read_bytes()).startswith(b"YKDELTA1")
    # Everything that takes snapshots reads them: the run and check loaders, a region, the session's own tools.
    assert _load(folder / "s-2.ram.gz") == bytes(session.machine.memory) == layout.Ram.load(folder / "s-2.ram.gz", BASE).data
    assert _regions(folder / "s-2.ram.gz") == {"vram": bytes(range(256))}
    assert session.changed("s-0", "s-1", unit=2)["places"][0]["address"] == "0x80000012"
    assert session.differs("s-2")["same"] and session.find(text="さようなら", snaps=["s-1"])["found"]["s-1"]
    # A snap written over the first becomes the first; the others, which needed the old one, are told.
    session.run(10)
    session.snap("s-0")
    assert not gzip.decompress((folder / "s-0.ram.gz").read_bytes()).startswith(b"YKDELTA1")
    with pytest.raises(snapfile.SnapError):
        _load(folder / "s-1.ram.gz")
    session.snap("s-3")
    assert _load(folder / "s-3.ram.gz") == bytes(session.machine.memory)
    # Loading a game starts anew: its first snap is whole.
    session.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc")
    session.snap("t-0")
    assert not gzip.decompress((folder / "t-0.ram.gz").read_bytes()).startswith(b"YKDELTA1")
    # And the command that makes them whole for a tool that doesn't know the form.
    assert cli.main(["expand", str(folder / "s-3.ram.gz"), str(folder / "t-0.ram.gz")]) == 0
    assert not gzip.decompress((folder / "s-3.ram.gz").read_bytes()).startswith(b"YKDELTA1")


def test_find_relative_lines_apart_in_one_memory(session):
    # A scene's lines as they lie in its script: none repeats a character, each is somewhere else, and other
    # bytes lie between them. What they share ties them together.
    lines = ["「いんや、まだずらよ", "「すまん", "ところで、ボスはもう"]
    own = {ch: 0x21 + 5 * i for i, ch in enumerate(dict.fromkeys("".join(lines)))}
    cells = lambda text: bytes(own[ch] for ch in text)                                   # noqa: E731
    put(session, 0x300, cells(lines[1]) + b"\xF7\x01" + cells(lines[2]))
    put(session, 0x380, cells(lines[0]))
    put(session, 0x500, bytes(range(1, 200)))                                            # other data
    out = session.find_relative(apart=lines)
    assert out["count"] == 1 and out["sets"][0]["lines"] == ["0x80000380", "0x80000300", "0x80000306"]
    assert out["sets"][0]["codes"] == {ch: f"{code:02X}" for ch, code in own.items()}
    session.snap("apart")
    put(session, 0x380, bytes(16))
    assert session.find_relative(apart=lines)["count"] == 0 and session.find_relative(apart=lines, snap="apart")["count"] == 1
    assert session.find_relative(apart=lines, snap="apart", window=64)["count"] == 0     # the first lies farther off
    for bad in (["「すまん"], ["あいう", "えおか"], ["「すまん", "「"]):
        with pytest.raises(HarnessError):
            session.find_relative(apart=bad)


def test_a_playstation_core_gets_its_video_memory_from_its_savestate(tmp_path):
    """The real machine wrapper, with a made-up core: no video memory given out, but a savestate laid out as
    PCSX ReARMed's, from which the harness takes it as the region `vram`."""
    from yomikata.harness.machine import Machine
    from yomikata import vram

    class Core:
        maps, hw, variables, options = [], None, {}, {}

        def __init__(self):
            self.video = bytes(range(256)) * (vram.VRAM_SIZE // 256)
            self.saves = 0

        def video_ram(self):
            return None

        def save_state(self):
            self.saves += 1
            head = bytes(32 + 4 + 1 + 128 * 96 * 3 + vram.RAM_SIZE + vram.BIOS_SIZE + vram.HW_SIZE)
            control = [0] * 256
            for n in range(6):
                control[0xE1 + n] = (0xE1 + n) << 24
            gpu = struct.pack("<II", 1, 0x14802000) + struct.pack("<256I", *control) + self.video
            return b"ST" + head[2:] + bytes(64) + gpu + bytes(64)

    machine = Machine.__new__(Machine)
    machine.platform, machine.core, machine.frames = "ps1", Core(), 0
    assert machine.regions() == {"vram": machine.core.video}
    machine.regions()
    assert machine.core.saves == 1                                   # once for a frame, however often asked
    machine.frames = 1
    machine.regions()
    assert machine.core.saves == 2
    machine.platform = "gc"                                          # another console: nothing made up
    assert machine.regions() == {}


@needs_reader
def test_compare_with_what_the_script_said_in_play(session):
    """A script that says a line only as it appears reads nothing from a still snap: what it said on the way to
    each snap is kept with the snap, and compare counts that."""
    session.snap("before")                                            # no script yet
    assert session.script_try(SCRIPT)["loaded"]
    session.steps([{"wait": 3, "snap": True}, {"wait": 3, "snap": True}, {"wait": 10, "snap": True}], prefix="p")
    session.snap_lines("p-000", ocr=["こんにちは"])
    session.snap_lines("p-001", no_text=True)                        # nothing new was said on the way to it
    session.snap_lines("p-002", ocr=["さようなら"])
    out = session.compare(snaps=["p-000", "p-001", "p-002"], live=True, show=True)
    assert [r["read"] for r in out["rows"]] == ["こんにちは", "", "さようなら"]
    assert out["script"]["of those, agreeing 80% or more"] == 2 and out["script"]["of those, reading nothing"] == 1
    with pytest.raises(HarnessError, match="no script loaded"):
        session.compare(snaps=["before"], live=True)


def test_on_disc_finds_memory_that_lies_on_the_disc_as_it_is(session, tmp_path):
    line = bytes(range(40, 104))
    put(session, 0x300, line)
    disc = bytearray(2048 * 40)
    disc[2048 * 7 + 2040:2048 * 7 + 2040 + len(line)] = line          # across the edge of two sectors
    (tmp_path / "games/ngc/Made Up (Japan).iso").write_bytes(bytes(disc))
    out = session.on_disc("0x80000300", 48)
    assert out["found"] == [hex(2048 * 7 + 2040).upper().replace("0X", "0x")] and out["sectors"] == 40 and out["bytes"] == 48
    put(session, 0x380, bytes(range(200, 232)))                       # this isn't on it
    assert session.on_disc("0x80000380")["found"] == []
    with pytest.raises(HarnessError, match="too short or too plain"):
        session.on_disc("0x80000800")                                 # zeros



def test_snaps_during_a_wait_and_what_points_where_in_each(session, tmp_path):
    out = session.steps([{"buttons": ["a"], "hold": 2, "wait": 7, "every": 3, "snap": True}], prefix="type")
    assert out["steps"] == [{"step": 0, "frames": 9, "snaps": ["type-000-00", "type-000-01"]}]
    assert [session.snaps[n].frame for n in out["steps"][0]["snaps"]] == [5, 8]
    for bad in ([{"wait": 4, "every": 2}], [{"wait": 4, "every": 0, "snap": True}], [{"every": 2, "snap": True}],
                [{"wait": 4000, "every": 1, "snap": True}]):
        with pytest.raises(HarnessError):
            session.steps(bad)
    # A word that points into the line in one moment only comes first.
    session.machine.memory[0x60:0x64] = struct.pack(">I", BASE + 0x104)
    session.snap("typing")
    session.machine.memory[0x60:0x64] = bytes(4)
    session.snap("after")
    found = session.refs("0x80000100", span=0x10, snaps=["typing", "after"])
    assert found["count"] == 2 and found["snaps"] == 2
    assert found["refs"][0] == {"at": "0x80000060", "in": 1, "points_at": {"typing": "0x80000104"}}
    assert found["refs"][1]["at"] == "0x80000040" and found["refs"][1]["in"] == 2


def test_words_and_the_text_they_point_at(session):
    out = session.pointed("0x80000038", count=4)
    assert out == {"words": 4, "pointers": [{"at": "0x80000040", "points_at": "0x80000100", "encoding": "shift_jis",
                                             "text": "こんにちは"}]}
    session.machine.memory[0x44:0x48] = struct.pack(">I", BASE + 0x800)                # points at nothing readable
    assert session.pointed("0x80000040", count=2)["pointers"][1] == {"at": "0x80000044", "points_at": "0x80000800"}


def test_changed_by_a_pattern_over_snaps(session):
    m = session.machine.memory
    for name, flag, pointer in (("s0", 1, 0), ("s1", 2, 0x111), ("s2", 1, 0), ("s3", 2, 0x222)):
        m[0x300] = flag
        m[0x310:0x314] = struct.pack(">I", pointer)
        session.snap(name)
    names = ["s0", "s1", "s2", "s3"]
    assert session.changed(snaps=names, pattern="abab") == {
        "count": 1, "places": [{"address": "0x80000300", "a": "01", "b": "02"}]}
    loose = session.changed(snaps=names, pattern="aAaA", unit=4)["places"]
    assert [p["address"] for p in loose] == ["0x80000300", "0x80000310"] and loose[1]["a"] == "00000000"
    assert session.changed(snaps=names, pattern="a.aA")["count"] >= 2
    assert session.changed(snaps=names, pattern="aAaA", unit=4, within=["0x80000308", "0x80000400"])["count"] == 1
    assert session.changed("s0", "s1", within=["0x80000300", "0x80000301"])["places"] == [
        {"address": "0x80000300", "a": "01", "b": "02"}]
    with pytest.raises(HarnessError):
        session.changed("s0", "s1", within=["0x80000400"])
    for bad in ({"snaps": names, "pattern": "aba"}, {"snaps": names, "pattern": "aaaa"}, {"snaps": names, "pattern": "aBaB"},
                {"snaps": names, "pattern": "ab1b"}, {"a": "s0", "snaps": names, "pattern": "abab"}, {"a": "s0"}):
        with pytest.raises(HarnessError):
            session.changed(**bad)


def test_a_name_read_off_a_naming_screen(session):
    m = session.machine.memory
    session.snap("n0")
    for k, ch in enumerate("あかさ"):                                   # the game's own codes: the fifty sounds from 0x20
        m[0x400 + k] = 0x20 + "あいうえおかきくけこさ".index(ch)
        m[0x500] = k + 1                                                 # a cursor: changes each time, isn't the name
        session.snap(f"n{k + 1}")
    out = session.entered(["n0", "n1", "n2", "n3"], "あかさ")
    assert out["count"] == 1
    name = out["names"][0]
    assert name["address"] == "0x80000400" and name["unit"] == 1
    assert name["codes"] == {"あ": "20", "か": "25", "さ": "2A"} and name["table"] == ["20=あ", "25=か", "2A=さ"]
    assert name["rule"].startswith("code = place in the fifty sounds") and "+ 0x20 (it holds for these" in name["rule"]
    with pytest.raises(HarnessError):
        session.entered(["n0", "n1"], "あか")


@needs_reader
def test_steps_that_answer_what_the_script_says(session):
    session.script_try(SCRIPT)
    out = session.steps([{"wait": 4, "times": 20, "until_event": "dialogue"}, {"wait": 1}],
                        on={"dialogue": [{"buttons": ["a"], "hold": 2, "snap": True}]}, prefix="go")
    rows = out["steps"]
    stopped = next(i for i, r in enumerate(rows) if r.get("stopped") == "event")
    assert stopped < 19 and rows[stopped + 1] == {"step": rows[stopped]["step"], "on": "dialogue",
                                                  "frames": rows[stopped]["frames"] + 2,
                                                  "snap": f"go-{rows[stopped]['step']:03d}-on01"}
    assert rows[-1]["step"] == 20 and len(rows) == stopped + 3          # the repetition's rest was left out
    for bad in ({"on": {"dialogue": []}}, {"on": {"menu": [{"wait": 1}]}}, {"on": {"text": [{"wait": 1, "times": 2}]}}):
        with pytest.raises(HarnessError):
            session.steps([{"wait": 1}], **bad)
    with pytest.raises(HarnessError):
        session.steps([{"wait": 1, "until_event": "menu"}])


def test_find_relative_tries_the_other_ways_by_itself(session):
    """Codes of two bytes, high byte first, that count DOWN as JIS counts up, at an odd address, with a sign
    from another range in the line: the plain call finds nothing and says which way does."""
    def code(ch):
        jis = ch.encode("euc_jp")
        return 0xA420 - ((jis[0] & 0x7F) << 8 | jis[1] & 0x7F) if ch != "ー" else 0x30FA
    line = "レーザーをもちいる"
    put(session, 0x701, b"".join(struct.pack(">H", code(ch)) for ch in line))
    out = session.find_relative(line)
    assert out["count"] == 0
    way = out["other_ways"][0]
    assert (way["unit"], way["big_endian"], way["order"], way["count"]) == (2, True, "unicode", 1)
    assert way["places"][0]["address"] == "0x80000701" and way["places"][0]["codes"]["レ"] == "7EB4"


def test_code_rule_says_the_arithmetic_kind_by_kind(session):
    rules = {row["kind"]: row for row in session.code_rule({
        "し": "7FE9", "て": "7FDA", "い": "7FFC", "レ": "7EB4", "サ": "7EEB", "Ｌ": "80B4", "９": "80C7", "Ｆ": "80BA",
        "反": "6B96", "動": "6C01", "銃": "6DF4", "っ": "3FDD", "る": "7FB5"})["kinds"]}
    assert "code = 0xA420 - JIS" in rules["katakana"]["rules"]
    assert "(code & 0x3FFF) = 0x6420 - JIS" in rules["hiragana"]["rules"]          # a small kana has a bit cleared
    assert "code = 0x8100 - ASCII" in rules["letters, digits and ASCII signs"]["rules"]
    assert rules["kanji"]["rules"] == [] and "fall with JIS" in rules["kanji"]["note"]
    assert session.code_rule({"あ": "56", "い": "57", "き": "61"})["kinds"][0]["rules"] == []
    assert "code = the fifty sounds + 0x20" in session.code_rule({"あ": "20", "か": "25", "さ": "2A"})["kinds"][0]["rules"]
    with pytest.raises(HarnessError):
        session.code_rule({"あ": "zz", "い": "01"})


def test_a_glyph_cut_out_of_video_memory_is_found_in_main_memory(session):
    glyph = np.array([[0, 0, 3, 3, 0, 0, 0, 0, 2, 1, 0, 0], [0, 3, 0, 0, 3, 0, 0, 2, 0, 0, 1, 0],
                      [3, 3, 3, 3, 3, 3, 0, 2, 2, 2, 1, 1], [3, 0, 0, 0, 0, 3, 0, 1, 0, 0, 0, 2]])
    # The font: 2 bits a pixel, the high bits first, three bytes a row.
    packed = b"".join(bytes(int("".join(f"{v:02b}" for v in row[i:i + 4]), 2) for i in range(0, 12, 4)) for row in glyph)
    put(session, 0x900, packed)
    # The texture the game drew it into: 4 bits a pixel, starting two pixels into a word.
    vram = np.zeros((512, 1024), dtype="<u2")
    for y, row in enumerate(glyph):
        for x, v in enumerate(row):
            vram[100 + y, 640 + (x + 2) // 4] |= int(v) << (4 * ((x + 2) % 4))
    session.machine.regions = lambda: {"vram": vram.tobytes()}
    out = session.find_picture(640, 100, 4, 4, bits=4, skip=2, width=12)
    assert out["pixels"] == [12, 4] and out["values"] == [0, 1, 2, 3]
    hit = out["found"][0]
    assert (hit["at"], hit["bits"], hit["first"], hit["row_bytes"], hit["cut"]) == ("0x80000900", 2, "high", 3, 0)
    kind, png = session.vram_picture(636, 96, 12, 12, bits=4, scale=4)
    from PIL import Image
    assert kind == "image" and Image.open(io.BytesIO(png)).size == (12 * 4 * 4, 12 * 4)
    assert Image.open(io.BytesIO(session.vram_picture()[1])).size == (1024, 512)
    session.machine.regions = lambda: {}
    with pytest.raises(HarnessError, match="video memory"):
        session.vram_picture()


def test_watch_lists_a_place_as_it_changes(session):
    out = session.watch("0x80000010", 3)                               # the frame counter
    assert out["from"] == "0x00000000" and [c["value"] for c in out["changes"]] == ["0x00000001", "0x00000002", "0x00000003"]
    out = session.watch("0x80000020", 6, size=1, buttons=["a"], hold=2)
    assert [(c["frame"], c["value"]) for c in out["changes"]] == [(4, "0x01"), (6, "0x00")]
    with pytest.raises(HarnessError):
        session.watch("0x80000010", 3, size=3)


def test_read_shows_memory_as_text_in_the_games_own_codes(session):
    def code(ch):
        jis = ch.encode("euc_jp")
        return 0xA420 - ((jis[0] & 0x7F) << 8 | jis[1] & 0x7F)
    put(session, 0xA00, struct.pack(">6H", code("し"), code("て") & ~0x4000, 0x8100 - ord("R"), 0x6B96, 0x30FE, 0x1234))
    out = session.read("0x80000A00", 12, table="6B96=反\n70FE=、\n", mask="1FFF",
                       rules=["(code & 0x1FFF) = 0x100 - ASCII", "(code & 0x1FFF) = 0x4420 - JIS"])       # the first that fits
    assert out["text"] == "してR反、<1234>" and out["hex"].startswith("7fe9")
    # A copy the other way round: codes that count up with JIS.
    put(session, 0xA40, struct.pack(">2H", 0x2437 + 0x5CE0, 0x2446 + 0x5CE0))
    assert session.read("0x80000A40", 4, rules=["code = JIS + 0x5CE0"])["text"] == "して"
    assert "text" not in session.read("0x80000A40", 4)
    session.snap("ra")
    put(session, 0xA40, b"\x01\x02")
    session.snap("rb")
    assert session.read("0x80000A40", 2, snaps=["ra", "rb"])["snaps"] == {"ra": "8117", "rb": "0102"}
    for bad in ({"rules": ["code = JIS * 2"]}, {"rules": ["code = 0x10 - Morse"]}, {"table": "zz=あ"}, {"rules": ["code = JIS + 0x1"], "unit": 3}):
        with pytest.raises(HarnessError):
            session.read("0x80000A40", 4, **bad)


def test_a_cursor_is_found_and_moved_to_a_place(session):
    with pytest.raises(HarnessError, match="cursor_find"):
        session.cursor_to(10, 10)
    found = session.cursor_find()
    assert (found["x_address"], found["y_address"], found["size"]) == ("0x80000030", "0x80000032", 2)
    assert (found["x"], found["y"]) == (88, 68) and found["moved"] == {"right": 12, "down": 12, "frames": 6}
    out = session.cursor_to(151, 40)
    assert (out["x"], out["y"], out["reached"]) == (150, 40, True)        # two a frame: as near as 1
    assert session.cursor_to() == {"x": 150, "y": 40, "frames": out["frames"]}
    out = session.cursor_to(400, 40, limit=30)                           # past the edge: as near as it gets
    assert out["reached"] is False and out["ran"] >= 30 and out["x"] == 210
    other = session.cursor_to(0, 0, x_address="0x80000030", y_address="0x80000032", size=2)
    assert (other["x"], other["y"]) == (0, 0)
    session.machine.memory[0x34] = 1                                    # a menu is open: the cursor is held
    assert session.cursor_to(20, 20, limit=60)["reached"] is False
    out = session.cursor_to(20, 20, if_stuck="b")
    assert out["reached"] is True and out["pressed"] == 1


@needs_reader
def test_steps_press_until_the_script_has_been_quiet(session):
    session.script_try(SCRIPT)                                           # it says a line every ten frames
    out = session.steps([{"buttons": ["a"], "hold": 1, "wait": 2, "times": 30, "until_quiet": 2}, {"wait": 1}])
    rows = out["steps"]
    stopped = [i for i, r in enumerate(rows) if r.get("stopped") == "quiet"]
    assert stopped == [len(rows) - 2] and len(rows) < 8 and rows[-1]["step"] == 30
    assert not rows[stopped[0]].get("events") and not rows[stopped[0] - 1].get("events")
    with pytest.raises(HarnessError):
        session.steps([{"wait": 1, "until_quiet": 2}], on={"dialogue": [{"wait": 1, "until_quiet": 1}]})


def test_a_zipped_game_is_unpacked_into_scratch_for_the_core(tmp_path):
    import zipfile
    for name in ("games/snes", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    with zipfile.ZipFile(tmp_path / "games/snes/Made Up (Japan).zip", "w") as z:
        z.writestr("Made Up (Japan).sfc", b"a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    seen = {}

    def make(core_path, game, platform, system_dir, options):
        seen["game"] = game
        return FakeMachine(core_path, game, platform, system_dir, options)
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), make)
    s.load("snes/Made Up (Japan).zip", "fake_libretro.so", "snes")
    assert seen["game"] == tmp_path / "scratch/work/unzipped/Made Up (Japan).sfc" and seen["game"].read_bytes() == b"a game"
    assert s.status()["game"] == "snes/Made Up (Japan).zip"
    s.close()


@needs_reader
def test_visit_goes_round_spots_and_says_what_each_gave(session):
    with pytest.raises(HarnessError, match="script"):
        session.visit([[10, 10]])
    session.script_try(SCRIPT)                                           # it says a line every ten frames
    with pytest.raises(HarnessError, match="cursor_find"):
        session.visit([[10, 10]])
    session.cursor_find()
    out = session.visit([[120, 80], [60, 40]], wait=4, most=6, after=[{"buttons": ["b"], "hold": 1}])
    assert [(s["at"], s["reached"]) for s in out["spots"]] == [([120, 80], True), ([60, 40], True)]
    assert all(2 <= s["presses"] <= 8 for s in out["spots"])
    assert {t["text"] for s in out["spots"] for t in s["said"]} <= {"こんにちは", "さようなら", "またあした"}
    assert out["spots"][0]["said"] and "stopped" not in out
    assert session.cursor_to()["x"] == 60
    session.cursor_at = (0x80000010, 0x80000014, 2)                      # the numbers are elsewhere now
    again = session.visit([[90, 50]], wait=4, most=3)["spots"][0]
    assert again["reached"] is True and again["found_again"] is True and session.cursor_at[0] == 0x80000030
    with pytest.raises(HarnessError):
        session.visit([[1, 2, 3]])


def test_follows_finds_a_place_that_takes_codes_one_after_the_other(session):
    m = session.machine.memory
    names = []
    for k, (code, other) in enumerate(((0x1E, 5), (0x00, 5), (0x1E, 6), (0x12, 6), (0x12, 7), (0x15, 7))):
        m[0x600], m[0x601] = code, other                                 # the character being drawn; a counter
        m[0x610] = 0x1E                                                  # a place that only ever holds the first
        session.snap(f"f{k}")
        names.append(f"f{k}")
    out = session.follows(names, ["1E", "1E", "12", "15"])
    assert out == {"count": 1, "places": [{"address": "0x80000600", "at_snaps": ["f0", "f2", "f3", "f5"]}]}
    assert session.follows(names, ["1E", "15"], within=["0x80000700", "0x80000800"])["count"] == 0
    assert session.follows(names, ["15", "1E"])["count"] == 0            # the other way round: no
    for bad in ({"values": ["1E"]}, {"values": ["zz", "01"]}, {"values": ["1E", "12"], "unit": 4}):
        with pytest.raises(HarnessError):
            session.follows(names, **bad)
    # When the codes aren't known: the characters' pattern. The place rests at 0 between them.
    names = []
    for k, code in enumerate((0, 0x41, 0, 0x52, 0, 0x41, 0, 0x63, 0x77, 0, 0)):
        m[0x700], m[0x701] = code, k                                     # the character; a counter
        session.snap(f"g{k}")
        names.append(f"g{k}")
    out = session.follows(names, pattern="abac")
    assert out["places"] == [{"address": "0x80000700", "rests_at": "00", "values": {"a": "41", "b": "52", "c": "63"}}]
    assert session.follows(names, pattern="ab?c")["places"][0]["values"] == {"a": "41", "b": "52", "c": "63"}
    assert session.follows(names, pattern="abab")["count"] == 0
    with pytest.raises(HarnessError):
        session.follows(names, pattern="ab")


def test_super_famicom_video_memory_as_tiles_and_a_tilemap(session):
    from PIL import Image
    vram = bytearray(0x10000)
    vram[16 * 65:16 * 65 + 2] = bytes([0xFF, 0x00])                      # tile 65: its top row, plane 0 only
    vram[16 * 65 + 2:16 * 65 + 4] = bytes([0x0F, 0xFF])                  # its second row: planes 0 and 1
    struct.pack_into("<3H", vram, 0x5000 + 64 * 2 + 2 * 3, 0x2041, 0x2042, 0x0C10)
    session.machine.platform = "sfc"
    session.machine.regions = lambda: {"vram": bytes(vram)}
    kind, png = session.vram_picture(x=1, y=1, w=1, h=1)
    tile = np.asarray(Image.open(io.BytesIO(png)))
    assert kind == "image" and tile.shape == (8, 8)
    assert list(tile[0]) == [85] * 8 and list(tile[1]) == [170] * 4 + [255] * 4 and not tile[2:].any()
    assert Image.open(io.BytesIO(session.vram_picture()[1])).size == (512, 512)
    assert Image.open(io.BytesIO(session.vram_picture(bits=4)[1])).size == (512, 256)
    out = session.vram_tilemap("0x5000", x=3, y=2, w=3, h=1)
    assert out["tiles"] == ["041 042 010"]
    session.machine.platform = "gc"
    with pytest.raises(HarnessError):
        session.vram_tilemap("0x5000")


def test_glyph_pairs_keeps_the_picture_the_game_draws_for_each_code(session, tmp_path):
    from PIL import Image
    machine = session.machine
    codes = [0x02E6, 0x033D, 0x02E6, 0x02FD, 0x1001, 0x032A]

    def step(buttons, stick, plain=machine._step):
        plain(buttons, stick)
        n = min(machine.frames // 2, len(codes))                         # a character every other frame
        rest = codes[n:] + [0] * 24
        machine.memory[0x800:0x800 + 2 * len(rest)] = b"".join(struct.pack(">H", c) for c in rest)   # the queue
        if n:
            machine.memory[0x7F0:0x7F2] = struct.pack(">H", codes[n - 1])                            # the code drawn
            machine.memory[0x780:0x784] = struct.pack(">HH", codes[n - 1], 0x8001)                   # its picture
    machine._step = step
    kind, out, png = session.glyph_pairs("0x80000780", 2, [{"wait": 14}], queue="0x80000800", name="font")
    assert kind == "both" and Image.open(io.BytesIO(png)).width > 100
    assert (out["drawn"], out["new"], out["codes"]) == (5, 4, 4)         # the wait mark isn't a character
    assert out["in_order"] == ["02E6 02FD 032A 033D"] and out["sheet"] == "work/font.png"
    kept = json.loads((tmp_path / "scratch/work/font.json").read_text())
    assert kept == {"02E6": "02e68001", "033D": "033d8001", "02FD": "02fd8001", "032A": "032a8001"}
    assert (tmp_path / "scratch/work/font.png").is_file() and session.each_frame is None
    # By the code's own place, and added to what was kept.
    machine.frames = 0
    codes[:] = [0x0311, 0x02E6]
    out = session.glyph_pairs("0x80000780", 2, [{"wait": 6}], code="0x800007F0", name="font")[1]
    assert out["new"] == 1 and out["codes"] == 5
    for bad in ({"queue": "0x80000800", "code": "0x800007F0"}, {}, {"queue": "0x80000800", "size": 3}):
        with pytest.raises(HarnessError):
            session.glyph_pairs("0x80000780", 2, [{"wait": 1}], **bad)
