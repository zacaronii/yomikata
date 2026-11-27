"""The harness on a made-up machine: memory that moves with the frames and the buttons, no core and no game."""

import io
import json
import os
import struct

import numpy as np
import pytest
from pathlib import Path

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


def test_every_call_goes_into_the_record_and_is_summed_by_game(session, tmp_path, capsys):
    from yomikata import calls, cli
    def ask(name, arguments):
        return mcp.handle(session, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    ask("run", {"frames": 2})
    ask("run", {"frames": 0})                                           # refused: a call all the same
    ask("find", {"text": "こ" * 80})
    ask("no-such-tool", {})                                             # not a call of a tool
    record = tmp_path / "scratch/record/calls.jsonl"
    raw = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    # Each call twice: as it begins (ok not yet known) and with its answer, under one id.
    assert [(e["tool"], e["ok"], bool(e.get("begun"))) for e in raw] == [
        ("run", None, True), ("run", True, False), ("run", None, True), ("run", False, False),
        ("find", None, True), ("find", True, False)]
    assert [e["call"] for e in raw[::2]] == [e["call"] for e in raw[1::2]] and len({e["call"] for e in raw}) == 3
    from yomikata.harness.record import entries
    lines = entries(record.read_text(encoding="utf-8"))
    assert [(e["tool"], e["ok"]) for e in lines] == [("run", True), ("run", False), ("find", True)]
    assert {e["session"] for e in lines} == {session.session_id} and lines[0]["game"] == "ngc/Made Up (Japan).iso"
    assert lines[0]["arguments"] == {"frames": 2} and lines[2]["arguments"]["text"].endswith("(80 characters)")
    # Another run of the harness, on another game, and a line cut short.
    with record.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": "2026-01-01T00:00:00Z", "session": "other", "game": "sfc/Other.sfc", "tool": "load",
                            "arguments": {}, "ok": True, "seconds": 1.5}) + "\n" + '{"at": "2026')
    rows = calls.summary(record)
    assert rows[0] == {"game": "ngc/Made Up (Japan).iso", "calls": 3, "sessions": 1, "failed": 1, "first": lines[0]["at"],
                       "last": lines[2]["at"], "seconds": rows[0]["seconds"], "tools": {"run": 2, "find": 1}}
    assert (rows[1]["game"], rows[1]["calls"], rows[1]["seconds"]) == ("sfc/Other.sfc", 1, 1.5)
    assert cli.main(["calls", str(tmp_path / "scratch"), "--game", "Other"]) == 0
    assert [json.loads(line)["game"] for line in capsys.readouterr().out.splitlines()] == ["sfc/Other.sfc"]
    assert cli.main(["calls", str(tmp_path / "nowhere")]) == 1



class BigMachine(FakeMachine):
    """FakeMachine with 16 MB of memory, as a Dreamcast's: a 16-bit count at 0x800 goes up 2 a frame while
    `typing`, and stands after; a stretch at 0x1000 changes at random, every frame."""
    typing = True

    def __init__(self, *a):
        super().__init__(*a)
        self.memory.extend(bytes((16 << 20) - len(self.memory)))

    def run(self, frames, buttons, stick):
        import random
        super().run(frames, buttons, stick)
        if self.typing:
            struct.pack_into(">H", self.memory, 0x800, (struct.unpack_from(">H", self.memory, 0x800)[0] + 2 * frames) & 0xFFFF)
        self.memory[0x1000:0x1040] = random.randbytes(0x40)


def test_advancing_over_many_large_snaps_holds_one_at_a_time(tmp_path):
    """Forty snaps of 16 MB through a line's typing and two after: advancing finds the count, and holds about
    what one snap takes, not forty (it read them all at once before, and the harness died of it)."""
    import tracemalloc
    for name in ("games/dc", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/dc/Made Up (Japan).chd").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), BigMachine)
    s.load("dc/Made Up (Japan).chd", "fake_libretro.so", "gc")          # big-endian, as the count is written
    names = []
    for i in range(42):
        s.machine.typing = i < 40
        s.machine.run(1, set(), (0.0, 0.0))
        names.append(s.snap(f"big-{i:02d}")["snap"])
    tracemalloc.start()
    out = s.advancing(names[:40], rest=names[40:], unit=2)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert [p["address"] for p in out["places"]] == [f"0x{BASE + 0x800:08X}"], out
    assert out["places"][0]["moved_in"] == 39
    assert peak < 200 << 20, f"{peak >> 20} MB at most"                 # forty snaps as numbers: over 2.5 GB
    # Too big a stretch is refused, saying what to do, before anything is read; so are too many snaps.
    from yomikata.harness import session as harness_session
    keep = harness_session.ADVANCING_MOST_BYTES
    harness_session.ADVANCING_MOST_BYTES = 1 << 20
    try:
        with pytest.raises(HarnessError, match="give `within` a stretch"):
            s.advancing(names[:40], rest=names[40:], unit=2)
    finally:
        harness_session.ADVANCING_MOST_BYTES = keep
    # within: only that stretch is held, and the count is found in it.
    near = s.advancing(names[:40], rest=names[40:], unit=2, within=[f"0x{BASE:X}", f"0x{BASE + 0x1000:X}"])
    assert [p["address"] for p in near["places"]] == [f"0x{BASE + 0x800:08X}"]
    past = s.advancing(names[:40], rest=names[40:], unit=2, within=[f"0x{BASE + 0x801:X}", f"0x{BASE + 0x1000:X}"])
    assert past["places"] == []                                         # the count starts before the stretch
    with pytest.raises(HarnessError, match="400 at most"):
        s.advancing([f"x{i}" for i in range(401)])
    s.close()


def test_a_call_whose_process_dies_is_still_a_call_in_the_record(session, tmp_path):
    """A call is in the record from its start: one that never comes back (the process killed in it, out of
    memory or given up on) is counted, as one that failed, by the budget and by `yomikata calls`."""
    from yomikata import calls
    from yomikata.harness.record import entries
    original = mcp.tools

    def dies(**_):
        raise SystemExit(137)                                           # as the process killed mid-call
    mcp.tools = lambda s: {**original(s), "dies": ("dies", {}, dies)}
    try:
        mcp.handle(session, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run", "arguments": {"frames": 1}}})
        with pytest.raises(SystemExit):
            mcp.handle(session, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "dies", "arguments": {}}})
    finally:
        mcp.tools = original
    record = tmp_path / "scratch/record/calls.jsonl"
    got = entries(record.read_text(encoding="utf-8"))
    assert [(e["tool"], e["ok"], e.get("died", False)) for e in got] == [("run", True, False), ("dies", False, True)]
    assert session.calls_so_far("ngc/Made Up (Japan).iso") == 2
    row = calls.summary(record)[0]
    assert (row["calls"], row["failed"], row["tools"]) == (2, 1, {"run": 1, "dies": 1})
    # A record from before calls had ids reads as it did: a line a call.
    with record.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": "2026-01-01T00:00:00Z", "session": "old", "game": "ngc/Made Up (Japan).iso",
                            "tool": "run", "arguments": {}, "ok": True, "seconds": 0.1}) + "\n")
    assert session.calls_so_far("ngc/Made Up (Japan).iso") == 3

def test_a_budget_is_counted_from_the_record_and_stops_calls_past_it(session, tmp_path):
    from yomikata import calls
    def ask(name, arguments=None):
        answer = mcp.handle(session, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments or {}}})["result"]
        return answer.get("isError", False), [c["text"] for c in answer["content"] if c["type"] == "text"]
    game, record = "ngc/Made Up (Japan).iso", tmp_path / "scratch/record/calls.jsonl"
    # Every answer ends with the game's calls so far, an error's too; with no budget, only the count.
    assert json.loads(ask("run", {"frames": 2})[1][-1]) == {"calls_so_far": 1}
    wrong, texts = ask("run", {"frames": 0})
    assert wrong and json.loads(texts[-1]) == {"calls_so_far": 2}
    # Calls of an earlier run of the harness count: the number is the record's, not this run's.
    with record.open("a", encoding="utf-8") as f:
        for _ in range(3):
            f.write(json.dumps({"at": "2026-01-01T00:00:00Z", "session": "earlier", "game": game, "tool": "run",
                                "arguments": {}, "ok": True, "seconds": 0.1}) + "\n")
        f.write(json.dumps({"at": "2026-01-01T00:00:00Z", "session": "earlier", "game": "sfc/Other.sfc", "tool": "run",
                            "arguments": {}, "ok": True, "seconds": 0.1}) + "\n")
    _, texts = ask("budget", {"calls": 8})
    # Asking is a call too, and in the record from its start: its own answer counts it.
    assert json.loads(texts[0]) == {"game": game, "calls_so_far": 6, "budget": 8, "left": 2}
    assert json.loads(texts[-1]) == {"calls_so_far": 6, "budget": 8, "left": 2}          # asking is a call too
    assert json.loads(ask("run", {"frames": 1})[1][-1])["left"] == 1
    assert json.loads(ask("status")[1][-1]) == {"calls_so_far": 8, "budget": 8, "left": 0}
    frames = session.machine.frames
    wrong, texts = ask("run", {"frames": 5})
    assert wrong and "budget" in texts[0] and "8 calls in the record, 8 allowed" in texts[0] and len(texts) == 1
    assert session.machine.frames == frames                                              # nothing ran
    assert ask("load", {"game": game, "core": "c.so", "platform": "gc"})[0]              # nor is the game loaded anew
    rows = {row["game"]: row["calls"] for row in calls.summary(record)}
    assert rows == {game: 8, "sfc/Other.sfc": 1}                                         # a refused call isn't in the record
    # Raised, calls go on; the count is still what `yomikata calls` sums.
    assert json.loads(ask("budget", {"calls": 11})[1][0])["left"] == 2              # itself counted
    assert not ask("run", {"frames": 1})[0]
    assert {row["game"]: row["calls"] for row in calls.summary(record)}[game] == 10
    assert json.loads(ask("budget", {"calls": 0})[1][0]) == {"game": game, "calls_so_far": 11, "budget": None}
    assert session.budget_of("sfc/Other.sfc") is None


def test_every_counted_call_ends_in_the_record_under_its_own_game(tmp_path):
    """Each call the record counts has two lines, its start and its end, both under the same game: `budget` and
    `load` before any game is up too (the end line once named no game), and an errored call. A refused call ran
    nothing and has neither."""
    for name in ("games/ngc", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/ngc/Made Up (Japan).iso").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), FakeMachine)
    game = "ngc/Made Up (Japan).iso"

    def ask(name, arguments=None):
        return mcp.handle(s, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments or {}}})["result"].get("isError", False)
    assert not ask("budget", {"calls": 4, "game": game})                               # before any game is up
    assert not ask("load", {"game": game, "core": "fake_libretro.so", "platform": "ngc"})
    assert ask("run", {"frames": 0})                                                   # an error: still a call
    assert not ask("run", {"frames": 1})
    assert ask("run", {"frames": 1})                                                   # refused: the budget is spent
    lines = [json.loads(line) for line in (tmp_path / "scratch/record/calls.jsonl").read_text().splitlines()]
    begun = {e["call"]: e for e in lines if e.get("begun")}
    ended = {e["call"]: e for e in lines if not e.get("begun")}
    assert begun.keys() == ended.keys() and len(begun) == 4 and len(lines) == 8
    assert all(begun[c]["game"] == ended[c]["game"] == game for c in begun)
    assert [ended[c]["tool"] for c in begun] == ["budget", "load", "run", "run"]
    assert s.calls_so_far(game) == 4
    s.close()


def test_load_with_a_state_starts_the_game_there_in_one_call(session, tmp_path):
    """A run of the harness that begins at a kept state is one call, not load, run, state_load, run."""
    session.run(7)
    put(session, 0x400, b"\x5a")
    session.state_save("kept")
    later = Session(session.roots, FakeMachine)
    answer = mcp.handle(later, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "load", "arguments": {
        "game": "ngc/Made Up (Japan).iso", "core": "fake_libretro.so", "platform": "gc", "state": "kept"}}})["result"]
    status = json.loads(answer["content"][0]["text"])
    assert status["state"] == "kept" and status["frames"] == 7 + 2                 # the state's frame, and two to settle
    assert later.machine.read(BASE + 0x400, 1) == b"\x5a"
    from yomikata.harness.record import entries
    record = entries((tmp_path / "scratch/record/calls.jsonl").read_text())
    assert [e["tool"] for e in record if e["session"] == later.session_id] == ["load"]
    later.close()
    exact = Session(session.roots, FakeMachine)
    assert exact.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "gc", state="kept", settle=0)["frames"] == 7
    with pytest.raises(HarnessError, match="no such state"):
        exact.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "gc", state="nowhere")
    exact.close()


def test_a_snap_name_used_twice_leaves_the_snaps_that_differed_from_it_readable(session, tmp_path):
    """The first snap of a run is whole and the rest are what differs from it. Taking a snap under the first's
    name again (a prefix used for two stretches of one run) must not cost the others."""
    from yomikata import snapfile
    folder = tmp_path / "scratch/snaps"
    put(session, 0x500, b"one")
    session.snap("s-1")
    put(session, 0x500, b"two")
    assert "written_over" not in session.snap("s-2")
    two = snapfile.read(folder / "s-2.ram.gz")
    assert snapfile._head(folder / "s-2.ram.gz")["base"] == "s-1.ram.gz"
    put(session, 0x500, b"3rd")
    again = session.snap("s-1")                                        # the name of the one s-2 hangs on
    assert "whole files now" in again["written_over"]
    assert snapfile.read(folder / "s-2.ram.gz") == two and snapfile._head(folder / "s-2.ram.gz") is None
    assert snapfile.read(folder / "s-1.ram.gz")[0x500:0x503] == b"3rd"
    assert snapfile.read(folder / "s-2.vram.gz") == bytes(range(256))
    # Another run of the harness, the same name once more: the first run's snaps are still read.
    later = Session(session.roots, FakeMachine)
    later.load("ngc/Made Up (Japan).iso", "fake_libretro.so", "ngc")
    session.snap("s-3")
    three = snapfile.read(folder / "s-3.ram.gz")
    assert "written_over" in later.snap("s-1") and snapfile.read(folder / "s-3.ram.gz") == three
    assert "written_over" not in later.snap("s-9")
    later.close()


def test_a_game_boy_advances_main_memory_is_the_256_kb_on_the_board():
    """mGBA calls the 32 KB in the CPU its system RAM. The harness takes the board's 256 KB from the core's
    memory map as main memory, and gives the 32 KB beside it as a region."""
    import ctypes as C
    from yomikata.harness.machine import Machine, stretch
    board, cpu = C.create_string_buffer(b"board" + bytes(0x40000 - 5), 0x40000), C.create_string_buffer(b"cpu" + bytes(0x8000 - 3), 0x8000)
    maps = [(0x03000000, 0x8000, C.addressof(cpu)), (0x02000000, 0x40000, C.addressof(board)), (0x08000000, 0, 0)]
    assert stretch(maps, 0x02000000)[1] == 0x40000 and stretch(maps, 0x08000000) is None and stretch(maps, 0x04000000) is None

    class Core:
        def video_ram(self): return b""
        def ram(self): return bytes(cpu)
        def ram_base(self): return None
    machine = Machine.__new__(Machine)
    machine.platform, machine.core = "gba", Core()
    machine.core.maps = maps
    base, ram = machine.ram()
    assert base == 0x02000000 and len(ram) == 0x40000 and ram[:5] == b"board"
    assert machine.regions()["iwram"][:3] == b"cpu" and len(machine.regions()["iwram"]) == 0x8000
    machine.core.maps = []                                             # a core that describes nothing: as before
    assert machine.ram() == (0x02000000, bytes(cpu)) and machine.regions() == {}


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


def test_a_snap_name_another_games_snap_holds_is_refused(session, tmp_path):
    """Every game's snaps share one folder: a name another game's snap holds is refused rather than written over,
    and one from before games were kept beside snaps is told by its console."""
    folder = tmp_path / "scratch/snaps"
    session.snap("mine-000")
    assert json.loads((folder / "mine-000.json").read_text(encoding="utf-8"))["game"] == session.game
    assert "snap" in session.snap("mine-000")                          # this game's own name again: as before
    (tmp_path / "games/ngc/Another Made Up (Japan).iso").write_bytes(b"not a game either")
    other = Session(session.roots, FakeMachine)
    other.load("ngc/Another Made Up (Japan).iso", "fake_libretro.so", "ngc")
    with pytest.raises(HarnessError, match="Made Up .*prefix of their own"):
        other.snap("mine-000")
    (folder / "old-000.json").write_text(json.dumps({"platform": "ps2", "frame": 1}), encoding="utf-8")
    with pytest.raises(HarnessError, match="a ps2 game's"):
        other.snap("old-000")
    (folder / "old-001.json").write_text(json.dumps({"platform": other.machine.platform, "frame": 1}), encoding="utf-8")
    assert "snap" in other.snap("old-001")                             # the same console: it can't be told
    assert "snap" in other.snap("theirs-000")
    other.close()


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
    # Only the part of each picture the text is in, numbered: a route's screens by the dozen.
    kind, png = session.sheet(["walk-000", "walk-002", "walk-003"], columns=3, crop=[8, 30, 40, 18], number=24)
    assert Image.open(io.BytesIO(png)).size == (3 * 40, 18 + 12)
    assert Image.open(io.BytesIO(session.sheet(["walk-000"], crop=[60, 40, 40, 40])[1])).size == (4, 8 + 12)   # cut at the edge
    for bad in ([1, 2, 3], [0, 0, 0, 5], [-1, 0, 5, 5]):
        with pytest.raises(HarnessError, match="crop"):
            session.sheet(["walk-000"], crop=bad)


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



def test_a_core_that_never_ends_is_left_when_the_calls_are_over_and_the_record_is_whole(session, tmp_path, capsys):
    """At the end of the lines the core is ended; one that doesn't come back from that (flycast's) is waited
    for only `closing` seconds, then the harness leaves, saying so, with every call's line in the record."""
    import threading
    import time
    release, left = threading.Event(), []
    session.machine.close = lambda: release.wait(30)                 # a deinit that hangs
    lines = [json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                         "params": {"name": "run", "arguments": {"frames": 5}}}) for i in (1, 2, 3)]
    out = io.StringIO()
    try:
        server = threading.Thread(target=mcp.serve, args=(session, io.StringIO("\n".join(lines) + "\n"), out),
                                  kwargs={"give_up": 0, "leave": left.append, "closing": 0.3}, daemon=True)
        start = time.monotonic()
        server.start()
        while not left and time.monotonic() - start < 5:
            time.sleep(0.02)
        took = time.monotonic() - start
    finally:
        release.set()
    server.join(5)
    assert left == [0] and took < 2, (left, took)
    assert "didn't end within 0.3 seconds" in capsys.readouterr().err
    assert [json.loads(line)["id"] for line in out.getvalue().splitlines()] == [1, 2, 3]
    record = (tmp_path / "scratch/record/calls.jsonl").read_text(encoding="utf-8")
    from yomikata.harness.record import entries
    assert record.endswith("\n") and [(e["tool"], e["ok"]) for e in entries(record)] == [("run", True)] * 3


def test_a_core_that_ends_is_not_left(session):
    """A core that ends in time is ended once, and the harness isn't made to leave."""
    import time
    ended, left = [], []
    machine = session.machine
    machine.close = lambda: ended.append(1)
    assert mcp.finish(session, left.append, 0.2) is True
    time.sleep(0.4)                                                  # the watch has let it be
    assert ended == [1] and left == [] and session.machine is None


def test_a_machine_counts_the_frames_it_runs_and_its_close_ends_the_core():
    """The real Machine (not the tests' fake): run counts frames, close ends its core."""
    from yomikata.harness.machine import Machine
    did = []

    class Core:
        def run(self, frames, buttons, stick): did.append(("run", frames))
        def close(self): did.append("close")
    machine = Machine.__new__(Machine)
    machine.core, machine.frames = Core(), 0
    machine.run(5, set(), (0.0, 0.0))
    machine.run(7, set(), (0.0, 0.0))
    assert machine.frames == 12
    machine.close()
    assert did == [("run", 5), ("run", 7), "close"] and machine.frames == 12


def test_a_game_boys_io_is_a_region_where_the_cores_map_gives_it():
    """Machine.regions on a Game Boy: the 256 bytes at 0xFF00 through the core's map, as `io`; none where the
    map doesn't give them all."""
    from yomikata.harness.machine import Machine, region_starts

    class Core:
        maps = []
        def video_ram(self): return None
    machine = Machine.__new__(Machine)
    machine.platform, machine.core = "gb", Core()
    machine.read = lambda address, size: bytes(range(size)) if address == 0xFF00 else b""
    assert machine.regions()["io"] == bytes(range(256)) and region_starts("gb")["io"] == 0xFF00
    machine.read = lambda address, size: b""                            # a core that doesn't map it
    assert "io" not in machine.regions()
    machine.platform = "gba"
    assert "io" not in machine.regions() and "io" not in region_starts("gba")


def test_a_game_boy_advances_sprite_table_and_video_memory_are_kept_and_read_at_their_addresses():
    """A Game Boy Advance game may draw each character as a sprite: the sprite table (1 KB at 0x07000000, a
    stretch of the core's map) is kept as the region `oam`, and over a snap it and video memory (`vram`) answer at
    the console's addresses, as they do in play; none where the map doesn't give the table whole."""
    import ctypes as C
    from yomikata.harness.machine import Machine, region_starts
    from yomikata.run import with_regions
    video, table = bytes(range(256)) * 384, bytes((7 * k) & 0xFF for k in range(0x400))
    held = C.create_string_buffer(table, 0x400)

    class Core:
        maps = [(0x07000000, 0x400, C.addressof(held))]
        def video_ram(self): return video
    machine = Machine.__new__(Machine)
    machine.platform, machine.core = "gba", Core()
    regions = machine.regions()
    assert regions["oam"] == table and regions["vram"] == video
    assert region_starts("gba")["oam"] == 0x07000000 and region_starts("gba")["vram"] == 0x06000000
    read = with_regions(lambda address, size: b"", "gba", regions)
    assert read(0x07000008, 8) == table[8:16] and read(0x06010000, 4) == video[0x10000:0x10004]
    machine.core.maps = [(0x07000000, 0x200, C.addressof(held))]                         # half given
    assert "oam" not in machine.regions()
    assert "oam" not in region_starts("gb") and "vram" not in region_starts("snes")


def test_a_game_boys_io_is_read_across_the_stretches_of_the_cores_map():
    """mGBA gives the registers and the high RAM as two stretches, and not 0xFFFF: io is read across them, through
    the real Core.read and Machine.read, with what no stretch gives as 0."""
    import ctypes as C
    from yomikata.harness.machine import Machine
    from yomikata.replay.libretro import Core
    registers = (C.c_char * 0x80).from_buffer(bytearray(range(0x80)))
    high = (C.c_char * 0x7F).from_buffer(bytearray(range(0x80, 0xFF)))
    core = Core.__new__(Core)
    core.descriptors = [{"start": 0xFF00, "len": 0x80, "ptr": C.addressof(registers), "select": 0, "disconnect": 0},
                        {"start": 0xFF80, "len": 0x7F, "ptr": C.addressof(high), "select": 0, "disconnect": 0}]
    core.maps = [(d["start"], d["len"], d["ptr"]) for d in core.descriptors]
    core.video_ram = lambda: None

    class Lib:                                                          # no system RAM to fall back on
        def retro_get_memory_data(self, which): return None
        def retro_get_memory_size(self, which): return 0
    core.lib = Lib()
    machine = Machine.__new__(Machine)
    machine.platform, machine.core, machine.cartridge = "gb", core, b""
    assert machine.read(0xFF00, 0x100) == bytes(range(0x80))                 # one stretch at a time
    assert machine.regions()["io"] == bytes(range(0xFF)) + b"\0"
    core.descriptors, core.maps = core.descriptors[1:], core.maps[1:]       # no registers: no region
    assert "io" not in machine.regions()


class Battles(FakeMachine):
    """A made-up role-playing game: a byte at 0x50 is 1 during a fight, which ends after `hits` presses of a;
    a menu (0x51 = 1) that a takes no notice of and b closes; a text pointer at 0x60 that moves when the fight
    is over and a story page begins. The picture shows only the game's state, so it stands still in the menu."""
    hits, menu_at = 6, None

    def __init__(self, *a):
        super().__init__(*a)
        self.memory[0x50], self.presses, self.was = 1, 0, set()

    def run(self, frames, buttons, stick):
        super().run(frames, buttons, stick)
        new = set(buttons) - self.was                                   # a press counts once, as it goes down
        self.was = set(buttons)
        m = self.memory
        if m[0x51]:
            if "b" in new:
                m[0x51] = 0
        elif "a" in new and m[0x50]:
            self.presses += 1
            if self.menu_at is not None and self.presses == self.menu_at:
                m[0x51] = 1
            elif self.presses >= self.hits:
                m[0x50], m[0x60] = 0, m[0x60] + 1                       # the fight is won; the story goes on

    def picture(self):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        frame[:, :, 0] = self.memory[0x50] * 100 + self.memory[0x51] * 50
        frame[:, :, 1] = self.presses % 256 if not self.memory[0x51] else 0
        return frame


def test_advance_through_presses_through_a_fight_and_backs_out_of_a_stuck_menu(tmp_path):
    """advance_through presses a while the fight's flag holds, and stops when it ends or the text pointer moves;
    a menu that takes no notice of a is seen as a still picture, backed out of with b, and the fight goes on.
    One that never lets go is reported as stuck."""
    for name in ("games/sfc", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/sfc/Made Up (Japan).sfc").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), Battles)
    s.load("sfc/Made Up (Japan).sfc", "fake_libretro.so", "sfc")
    flag, pointer = f"0x{BASE + 0x50:X}", f"0x{BASE + 0x60:X}"
    out = s.advance_through(flag, equals="1", wait=10)
    assert (out["stopped"], out["presses"], out["value"]) == ("mode ended", 6, "0x0"), out
    s.machine.memory[0x50], s.machine.presses, s.machine.menu_at = 1, 0, 2
    out = s.advance_through(flag, differs="0", wait=10, watch=pointer, stuck=4)
    assert out["stopped"] == "watched changed" and out["backs"] == 1 and s.machine.presses == 6, out
    s.machine.memory[0x50], s.machine.memory[0x51], s.machine.presses, s.machine.menu_at = 1, 1, 0, None
    out = s.advance_through(flag, equals="1", wait=10, stuck=3, back=["x"])         # x never closes the menu
    assert out["stopped"] == "stuck" and out["backs"] == 3 and out["presses"] == 12, out
    out = s.advance_through(flag, equals="1", wait=10, most=2, stuck=0)
    assert (out["stopped"], out["presses"]) == ("most", 2)
    with pytest.raises(HarnessError, match="one of them"):
        s.advance_through(flag)
    s.close()


class LateBattles(Battles):
    """Battles whose flag (0x50) is set only once the fight's opening has played, at frame `opens`."""
    opens = 0

    def run(self, frames, buttons, stick):
        super().run(frames, buttons, stick)
        if self.opens and self.frames >= self.opens:
            self.memory[0x50], self.opens = 1, 0


def test_advance_through_waits_for_a_mode_to_begin_and_says_when_it_never_did(tmp_path):
    """A fight whose flag is set after its opening: without `begin` nothing is pressed and it never began (not
    "mode ended"); with `begin` it waits, pressing nothing, then fights through. A flag that doesn't come in
    time never began either. The outcome goes into the record of calls, through the MCP call."""
    from yomikata.harness import mcp
    for name in ("games/sfc", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/sfc/Made Up (Japan).sfc").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), LateBattles)
    s.load("sfc/Made Up (Japan).sfc", "fake_libretro.so", "sfc")
    flag, m = f"0x{BASE + 0x50:X}", s.machine.memory
    m[0x50], s.machine.opens, s.machine.presses = 0, s.machine.frames + 90, 0
    out = s.advance_through(flag, equals="1", wait=10)
    assert (out["stopped"], out["presses"]) == ("never began", 0), out
    out = s.advance_through(flag, equals="1", wait=10, begin=40)                       # not yet by then
    assert (out["stopped"], out["presses"]) == ("never began", 0), out
    answer = mcp.call(s, "advance_through", {"address": flag, "equals": "1", "wait": 10, "begin": 200})
    out = json.loads(answer["content"][0]["text"])
    assert (out["stopped"], out["presses"], out["value"]) == ("mode ended", 6, "0x0"), out
    record = [json.loads(line) for line in (tmp_path / "scratch/record/calls.jsonl").read_text().splitlines()]
    done = [e for e in record if e["tool"] == "advance_through" and not e.get("begun")]
    assert done[-1]["outcome"] == {"presses": 6, "backs": 0, "stopped": "mode ended"}
    s.close()


class LoopingBattles(Battles):
    """A fight whose command cursor is on a command that goes round three pictures under a (a list opening, a
    note, the menu again: Chrono Cross's element command) and never on; b does nothing there."""
    hits = 10 ** 6

    def run(self, frames, buttons, stick):
        new = set(buttons) - self.was
        if "a" in new:
            self.memory[0x53] = (self.memory[0x53] + 1) % 3
        super().run(frames, buttons, stick)

    def picture(self):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        frame[:, :, 0] = 40 + 60 * self.memory[0x53]
        return frame


def test_advance_through_takes_a_loop_of_pictures_for_still(tmp_path):
    """A menu going round three pictures under a: with the last six presses' pictures held against, it is seen as
    still, backed out of three times and reported stuck; against two back only, it runs to `most`."""
    for name in ("games/sfc", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/sfc/Made Up (Japan).sfc").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), LoopingBattles)
    s.load("sfc/Made Up (Japan).sfc", "fake_libretro.so", "sfc")
    flag = f"0x{BASE + 0x50:X}"
    out = s.advance_through(flag, equals="1", wait=10, stuck=4, most=100)
    assert (out["stopped"], out["backs"]) == ("stuck", 3) and out["presses"] < 40, out
    out = s.advance_through(flag, equals="1", wait=10, stuck=4, most=100, cycle=2)
    assert (out["stopped"], out["backs"], out["presses"]) == ("most", 0, 100), out
    s.close()


class CursorBattles(Battles):
    """Battles with a command cursor (0x52: 0 to fight, 1 the items): a fights only from the first command, and
    from the second opens an empty list (0x51) whose cursor blinks, 4 pixels of 3072 every 8 frames; b closes
    it; up moves the command cursor, round from the top to the bottom."""
    hits = 4

    def run(self, frames, buttons, stick):
        new = set(buttons) - self.was
        m = self.memory
        if "up" in new and not m[0x51]:
            m[0x52] = (m[0x52] - 1) % 2
        if "a" in new and not m[0x51] and m[0x52] == 1 and m[0x50]:
            m[0x51] = 1                                                 # the empty list: a does nothing in it
            self.was = set(buttons)
            FakeMachine.run(self, frames, buttons, stick)
            return
        super().run(frames, buttons, stick)

    def picture(self):
        frame = super().picture()
        if self.memory[0x51] and (self.frames // 8) % 2:
            frame[10:12, 10:12, 2] = 255                               # the list's cursor, blinking
        return frame


def test_advance_through_takes_a_blinking_cursor_for_still_and_presses_a_sequence(tmp_path):
    """A list whose cursor blinks is still all the same (a two-phase blink, few pixels): it is backed out of, and
    with `back` that can't close it, reported stuck. A `first` sequence sets the command cursor, after which a
    fights; `buttons` as a list of presses goes round in turn."""
    for name in ("games/sfc", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/sfc/Made Up (Japan).sfc").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), CursorBattles)
    s.load("sfc/Made Up (Japan).sfc", "fake_libretro.so", "sfc")
    flag = f"0x{BASE + 0x50:X}"
    m = s.machine.memory
    m[0x52] = 1                                                         # the cursor left on the items
    out = s.advance_through(flag, equals="1", wait=12, stuck=3, back=["x"], most=40)
    assert out["stopped"] == "stuck" and out["backs"] == 3, out          # blinking, and x can't close it
    m[0x51], m[0x52], s.machine.presses = 0, 1, 0
    out = s.advance_through(flag, equals="1", wait=12, stuck=3, most=40)
    assert out["stopped"] == "stuck" and out["backs"] == 3, out          # b closes it, a opens it again: a loop
    m[0x51], m[0x52], s.machine.presses = 0, 1, 0
    out = s.advance_through(flag, equals="1", wait=12, first=[["up"]], stuck=3)
    assert (out["stopped"], out["presses"], out["backs"]) == ("mode ended", 4, 0), out
    m[0x50], m[0x52], s.machine.presses = 1, 1, 0                     # a list of presses: b, up, a, a, ...
    out = s.advance_through(flag, equals="1", wait=12, buttons=[["b"], ["up"], ["a"], ["a"], ["a"], ["a"]], stuck=0)
    assert out["stopped"] == "mode ended" and out["presses"] == 6, out
    with pytest.raises(HarnessError, match="not both"):
        s.advance_through(flag, equals="1", buttons=["a", ["b"]])
    s.close()

class BorderedTyping(FakeMachine):
    """A Game Boy game with a Super Game Boy border: a 256x224 picture whose middle 160x144 is the game's screen.
    A fight (0x50 = 1) whose page types 400 more pixels each 36 frames (one press's hold and wait): 1.7% of the
    game's screen, 0.7% of the whole picture. A menu (0x51 = 1) stops the typing and shows a blinking cursor,
    4 pixels; b doesn't close it. The real Machine.screen finds the game's screen in it."""
    from yomikata.harness.machine import Machine
    screen = Machine.screen

    class core:
        base = (256, 224)                           # mGBA may say the bordered size: the platform's size counts

    def __init__(self, *a):
        super().__init__(*a)
        self.memory[0x50], self.typed = 1, 0

    def run(self, frames, buttons, stick):
        super().run(frames, buttons, stick)
        if not self.memory[0x51]:
            self.typed = (self.frames // 36) * 400 % (160 * 144)

    def picture(self):
        frame = np.full((224, 256, 3), 90, dtype=np.uint8)            # the border
        game = np.zeros((144, 160, 3), dtype=np.uint8)
        game.reshape(-1, 3)[:self.typed] = 255
        if self.memory[0x51] and (self.frames // 8) % 2:
            game[0:2, 0:2] = 128
        frame[40:184, 48:208] = game
        return frame


def test_advance_through_measures_still_over_the_game_screen_inside_a_border(tmp_path):
    """A page typing inside a Super Game Boy border isn't still (it would be, as a share of the whole picture);
    a menu that takes no press, its cursor blinking, still is, and is reported stuck."""
    from yomikata.harness.machine import Machine
    for name in ("games/gb", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/gb/Made Up (Japan).gb").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), BorderedTyping)
    s.load("gb/Made Up (Japan).gb", "fake_libretro.so", "gb")
    flag = f"0x{BASE + 0x50:X}"
    out = s.advance_through(flag, equals="1", wait=30, stuck=4, most=20)
    assert (out["stopped"], out["backs"], out["presses"]) == ("most", 0, 20), out
    s.machine.memory[0x51] = 1                                          # the menu: nothing types
    out = s.advance_through(flag, equals="1", wait=30, stuck=4, most=40)
    assert (out["stopped"], out["backs"]) == ("stuck", 3), out
    s.close()
    # Without the border taken off, the typing is too small a share: what a whole picture would have said.
    whole = BorderedTyping.picture
    BorderedTyping.screen = lambda self: whole(self)
    try:
        s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), BorderedTyping)
        s.load("gb/Made Up (Japan).gb", "fake_libretro.so", "gb")
        out = s.advance_through(flag, equals="1", wait=30, stuck=4, most=20)
        assert out["stopped"] == "stuck", out
        s.close()
    finally:
        BorderedTyping.screen = Machine.screen


def test_a_game_screen_is_the_middle_of_a_bordered_picture_and_the_core_says_its_size():
    """game_screen takes the size's middle from a bigger picture, but not from one drawn a whole multiple bigger;
    the Core keeps its base size from the AV info and from SET_GEOMETRY; Machine.screen goes by the platform's
    size, then the core's."""
    import ctypes as C
    from yomikata.harness.machine import Machine, game_screen
    from yomikata.replay import libretro
    frame = np.arange(224 * 256 * 3, dtype=np.uint32).reshape(224, 256, 3)
    assert (game_screen(frame, (160, 144)) == frame[40:184, 48:208]).all()
    assert game_screen(frame, (256, 224)) is frame and game_screen(frame, None) is frame
    assert game_screen(frame, (128, 112)) is frame                     # twice as big: not a border
    assert game_screen(frame, (320, 240)) is frame                     # smaller than said: all of it
    assert game_screen(None, (160, 144)) is None

    class Lib:
        def retro_get_system_info(self, info): info._obj.need_fullpath = True
        def retro_load_game(self, info): return True
        def retro_get_system_av_info(self, av): av._obj.base_width, av._obj.base_height = 256, 224
        def retro_set_controller_port_device(self, port, device): pass
    core = libretro.Core.__new__(libretro.Core)
    core.lib, core.base = Lib(), None
    core.load_game(Path("made-up.gb"))
    assert core.base == (256, 224)
    geometry = libretro.GameGeometry(160, 144, 256, 224, 0.0)
    assert core._env(libretro.ENV_SET_GEOMETRY, C.cast(C.pointer(geometry), C.c_void_p)) and core.base == (160, 144)

    machine = Machine.__new__(Machine)
    machine.core = core
    core.frame_rgb = lambda: frame
    machine.platform = "gb"
    assert machine.screen().shape == (144, 160, 3)
    machine.platform, core.base = "sfc", (240, 200)
    assert (machine.screen() == frame[12:212, 8:248]).all()
    core.base = None
    assert machine.screen() is frame


def test_a_cores_end_unloads_the_game_then_ends_the_core_once():
    from yomikata.replay.libretro import Core
    did = []

    class Lib:
        def retro_unload_game(self): did.append("unload")
        def retro_deinit(self): did.append("deinit")

    class Gl:
        def close(self): did.append("gl")

    class Hw:
        def context_destroy(self): did.append("destroy")
    core = Core.__new__(Core)
    core.lib, core.hw, core.gl, core._loaded = Lib(), Hw(), Gl(), True
    core.close()
    core.close()
    assert did == ["destroy", "unload", "deinit", "gl"]


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
    # By scene: whether the bar for a checked script is met, and what is short of it.
    out = session.compare(SCRIPT, scenes={"the first room": ["c-000", "c-001"], "the walk": ["c-002"]})
    assert out["bar"]["met"] is False
    assert out["bar"]["scenes"] == {"the first room": {"screens": 2, "agreeing": 2}, "the walk": {"screens": 1, "agreeing": 0}}
    short = " / ".join(out["bar"]["short_of_it"])
    assert "2 screens with text (1 more show none)" in short and "2 scene(s)" in short and "give live" in short \
        and "show no text" in short
    assert (out["bar"]["screens with text"], out["bar"]["screens showing none"]) == (2, 1)
    for bad in (lambda: session.compare(SCRIPT, snaps=["nothing"]), lambda: session.snap_lines("nothing", no_text=True),
                lambda: session.snap_lines("c-000"), lambda: session.compare(SCRIPT, snaps=["c-000"], entry={"id": "x"}),
                lambda: session.compare(SCRIPT, scenes={"empty": []})):
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


def test_find_relative_tries_rows_padded_to_a_windows_width(session):
    """A page kept as Kaeru no Tame ni Kane wa Naru keeps its own: two rows of 18 cells, each a number of the
    page's own in the order its characters first come, 0x54 a blank padding each row to the width. Two rows
    with a break aren't found (the break is as many cells as the row is short); padded to 18, they are."""
    rows = ["やまのうえの　かわのした", "そらのいろ　はなのいろ。"]
    numbers, cells = {}, b""
    for row in rows:
        for ch in row:
            cells += bytes([0x54]) if ch == "　" else bytes([numbers.setdefault(ch, len(numbers))])
        cells += bytes([0x54]) * (18 - len(row))
    put(session, 0xD00, cells)
    out = session.find_relative("\n".join(rows))
    assert out["count"] == 0 and out["padded"][0]["width"] == 18, out
    assert out["padded"][0]["places"][0]["address"] == "0x80000D00"
    assert out["padded"][0]["places"][0]["codes"]["の"] == f"{numbers['の']:02X}"


def test_find_relative_a_line_in_codes_of_the_games_own(session):
    line = "ははのははははがいたい"
    own = {ch: 0x130 + 7 * i for i, ch in enumerate(dict.fromkeys(line))}              # codes in no usual order
    put(session, 0x900, b"".join(struct.pack(">H", own[ch]) for ch in line))
    out = session.find_relative(line, unit=2)
    assert out["count"] == 1 and out["places"][0]["address"] == "0x80000900"
    assert out["places"][0]["codes"] == {ch: f"{code:04X}" for ch, code in own.items()}
    assert session.find_relative(line, unit=1)["count"] == 0
    # Given a snap, the line is looked for in it, not in memory now (which no longer holds it).
    session.snap("held")
    put(session, 0x900, bytes(2 * len(line)))
    assert session.find_relative(line, unit=2)["count"] == 0
    assert session.find_relative(line, unit=2, snap="held")["places"][0]["address"] == "0x80000900"
    put(session, 0x900, b"".join(struct.pack(">H", own[ch]) for ch in line))
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


def test_find_relative_a_row_of_tiles_with_its_spaces(session):
    # A window kept as tiles: a cell a character, blanks too, all one tile. The row has one kana twice.
    row = "なつのひかりを みずうみで あびた"
    assert sorted(ch for ch in set(row) if row.count(ch) > 1) == [" ", "み"]
    own = {ch: 0x39 + 5 * i for i, ch in enumerate(dict.fromkeys(row))}
    put(session, 0x900, b"".join(struct.pack(">H", own[ch]) for ch in row))
    with pytest.raises(HarnessError, match="spaces: true"):
        session.find_relative("あいう えお")                    # still too little, and it says what to give
    out = session.find_relative(row, unit=2, spaces=True)
    assert out["count"] == 1 and out["places"][0]["address"] == "0x80000900"
    assert out["places"][0]["codes"][" "] == f"{own[' ']:04X}"
    # Without the word the spaces are left out and one repeat is too little: it tries them as cells by itself.
    out = session.find_relative(row, unit=2)
    assert out["count"] == 0 and "too little" in out["note"]
    assert {(w["unit"], w.get("spaces"), w["places"][0]["address"]) for w in out["other_ways"]} == {(2, True, "0x80000900")}   # in either byte order
    with pytest.raises(HarnessError, match="too little"):
        session.find_relative(row.replace(" ", ""))             # one repeat alone fits by chance: still refused


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
    cart = bytearray(0x200)                                         # a Game Boy Advance cartridge's header
    cart[0xA0:0xAC], cart[0xAC:0xB0], cart[0xB2] = b"MADE UP GAME", b"AZZJ", 0x96
    (tmp_path / "games/gba").mkdir()
    (tmp_path / "games/gba/Made Up (Japan).gba").write_bytes(bytes(cart))
    out = session.identify("gba/Made Up (Japan).gba")
    assert (out["platform"], out["id"]) == ("gba", "AGB-AZZJ") and len(out["header_crc32"]) == 8
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
    # A snap written over the first becomes the first; the others, which needed the old one, were made whole.
    one = _load(folder / "s-1.ram.gz")
    session.run(10)
    session.snap("s-0")
    assert not gzip.decompress((folder / "s-0.ram.gz").read_bytes()).startswith(b"YKDELTA1")
    assert _load(folder / "s-1.ram.gz") == one
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
    # A window that scrolls keeps the row before on screen: given all its rows, a screen is held to those it
    # gained since the screen before it in its scene. A scene's first screen gained all it shows.
    session.snap_lines("p-002", ocr=["こんにちは", "　さようなら"])
    scenes = {"one": ["p-000", "p-002"], "two": ["p-001"]}
    plain = session.compare(scenes=scenes, live=True, show=True)
    assert [r["agreement"] for r in plain["rows"]][1] < 0.8
    out = session.compare(scenes=scenes, live=True, show=True, scrolls=True)
    assert [r["seen"] for r in out["rows"]] == [["こんにちは"], ["　さようなら"], []]
    assert out["bar"]["scenes"]["one"] == {"screens": 2, "agreeing": 2}
    again = session.compare(scenes={"a": ["p-002"], "b": ["p-000", "p-001"]}, live=True, show=True, scrolls=True)
    assert again["rows"][0]["seen"] == ["こんにちは", "　さようなら"]            # first in its scene: nothing before it
    with pytest.raises(HarnessError, match="scrolls goes with"):
        session.compare(snaps=["p-000"], live=True, scrolls=True)
    with pytest.raises(HarnessError, match="no script loaded"):
        session.compare(snaps=["before"], live=True)


RUBY_SCRIPT = """-- reader: title つくりもの (Made Up)
-- reader: no hooks
if mem.u32be(0x80000040) ~= 0x80000100 then error("this script is for another build of the game", 0) end
reader.on_state_loaded(function() reader.forget() end)
reader.on_frame(function()
    local bytes = mem.cstr(mem.u32be(0x80000040), 32)
    if reader.changed("line", bytes) and #bytes > 0 then
        local b = reader.builder()
        b:add_ruby("今日", "きょう")
        b:add("は")
        reader.emit{ text = b:text(), ruby = b:ruby() }
    end
end)
"""


@needs_reader
def test_compare_scores_furigana_read_off_the_picture(session):
    """A script that gives furigana: what it said in play keeps them with the snap, snap_lines takes those read
    off the picture as [base, reading] pairs, and compare scores each screen's and the bar counts them; a reading
    read wrong, or one the script hasn't, lowers a screen's agreement."""
    assert session.script_try(RUBY_SCRIPT)["loaded"]
    session.steps([{"wait": 3, "snap": True}, {"wait": 3, "snap": True}, {"wait": 10, "snap": True}], prefix="r")
    out = session.snap_lines(screens={"r-000": ["今日は"], "r-002": ["今日は"]},
                             ruby={"r-000": [["今日", "きょう"]], "r-002": [["今日", "こんにち"]]})
    assert out["with_ruby"] == 2
    found = session.compare(scenes={"a": ["r-000"], "b": ["r-002"], "c": ["r-001"]}, live=True, show=True)
    rows = {r["snap"]: r for r in found["rows"]}
    assert rows["r-000"]["ruby_read"] == [["今日", "きょう"]] and rows["r-000"]["ruby_agreement"] == 1.0
    assert rows["r-002"]["ruby_agreement"] == 0.0 and "ruby_agreement" not in rows["r-001"]
    assert found["script"]["with furigana read off the screen"] == 2
    assert found["bar"]["furigana"] == {"screens": 2, "agreeing": 1}
    assert any("furigana" in why for why in found["bar"]["short_of_it"])
    with pytest.raises(HarnessError, match="pairs"):
        session.snap_lines("r-000", ocr=["今日は"], ruby=[["今日"]])
    with pytest.raises(HarnessError, match="one of the screens"):
        session.snap_lines(screens={"r-000": ["今日は"]}, ruby={"r-009": []})
    from yomikata.harness.session import ruby_agreement
    assert ruby_agreement([["今日", "きょう"], ["日", "ひ"]], [["今日", "きょう"]]) == 0.5      # one extra
    assert ruby_agreement([], []) == 1.0


@needs_reader
def test_compare_reread_runs_the_script_over_snaps_taken_in_play_again(session):
    """After a fix to a script or its table, the snaps already taken in play are counted again by running the
    script as it now is over each one's memory: every snap the same way, those taken with no script loaded too;
    how many were re-read is in the answer, the bar and the record. The bar is never met so: it counts what was
    said in play, and a script can read a still snap right and still fail there."""
    session.snap("before")                                            # no script yet
    broken = SCRIPT.replace("text = text.sjis(bytes)", 'text = "?"')
    assert session.script_try(broken)["loaded"]
    session.steps([{"wait": 3, "snap": True}, {"wait": 10, "snap": True}], prefix="p")
    session.snap_lines("p-000", ocr=["こんにちは"])
    session.snap_lines("p-001", ocr=["さようなら"])
    scenes = {"one": ["p-000"], "two": ["p-001"]}
    said = session.compare(scenes=scenes, live=True, show=True)       # what the broken script said in play
    assert [r["read"] for r in said["rows"]] == ["?", "?"] and said["script"]["re-read offline"] == 0
    again = session.compare(source=SCRIPT, scenes=scenes, live=True, reread=True, show=True)
    assert [r["read"] for r in again["rows"]] == ["こんにちは", "さようなら"]
    assert all(r["reread"] for r in again["rows"])
    assert again["script"]["re-read offline"] == 2 and again["bar"]["re-read offline"] == 2
    assert again["bar"]["scenes"]["one"] == {"screens": 1, "agreeing": 1}
    assert not again["bar"]["met"]
    assert "re-read offline: the bar counts what was said in play; run the stretch again" in again["bar"]["short_of_it"]
    assert not any("re-read" in line for line in said["bar"]["short_of_it"])
    assert session.compare(source=SCRIPT, snaps=["before"], live=True, reread=True)["script"]["re-read offline"] == 1
    with pytest.raises(HarnessError, match="give reread"):
        session.compare(source=SCRIPT, snaps=["before"], live=True)
    with pytest.raises(HarnessError, match="whole window"):
        session.compare(source=SCRIPT, scenes=scenes, live=True, reread=True, scrolls=True)
    with pytest.raises(HarnessError, match="reread goes with live"):
        session.compare(source=SCRIPT, snaps=["p-000"], reread=True)


def made_up_sfc(hirom: bool, title: bytes = b"MADE UP") -> bytes:
    """A 64 KB Super Famicom image with a header that holds: its title at bus 0xFFC0, its checksum and complement,
    its map byte; each byte elsewhere its offset's low byte, and a mark at the start of its second 32 KB."""
    image = bytearray(i & 0xFF for i in range(0x10000))
    image[0x8000:0x8004] = b"MARK"
    at = 0xFFB0 if hirom else 0x7FB0
    image[at + 0x10:at + 0x10 + 21] = title.ljust(21)
    image[at + 0x25] = 0x21 if hirom else 0x20
    image[at + 0x2C:at + 0x30] = struct.pack("<HH", 0xEDCB, 0x1234)
    return bytes(image)


def test_rom_reader_gives_the_cartridge_at_the_consoles_own_addresses():
    """Snaps keep memory, not the cartridge: rom_reader gives a script the cartridge where it reads it in play,
    and no more than docs/script-api.md promises: a Super Famicom HiROM cartridge at 0xC00000 on, byte for byte
    (a copier's header left out), not its mirrors, and no LoROM one; a Game Boy's at CARTRIDGE."""
    from yomikata.harness.machine import CARTRIDGE, rom_reader
    hi = made_up_sfc(True)
    read = rom_reader("sfc", hi)
    assert read(0xC0FFC0, 7) == b"MADE UP" and read(0xC08000, 4) == b"MARK" and read(0xC00123, 1) == b"\x23"
    assert read(0xC0FFFE, 4) == hi[0xFFFE:]                                           # where the file ends
    assert read(0x400123, 1) == b"" and read(0x008000, 4) == b"" and read(0x7E0000, 1) == b""     # mirrors, RAM
    assert rom_reader("sfc", bytes(512) + hi)(0xC0FFC0, 7) == b"MADE UP"              # a copier's header
    assert rom_reader("sfc", made_up_sfc(False)) is None                              # LoROM: not dependable yet
    assert rom_reader("gb", b"GB" * 8)(CARTRIDGE["gb"] + 2, 2) == b"GB"
    assert rom_reader("gba", b"AGB!" * 4)(0x08000004, 4) == b"AGB!"                   # where the core gives it
    assert rom_reader("gba", b"AGB!")(0x02000000, 4) == b""
    assert rom_reader("ps1", b"x") is None and rom_reader("sfc", b"short") is None


class Cartridge(FakeMachine):
    """FakeMachine with the game's file as its cartridge, as Machine keeps it for reading over snaps; read in play
    too, as a Super Famicom core's map gives it."""

    def __init__(self, core_path, game, platform, system_dir, options):
        from yomikata.harness.machine import rom_reader
        super().__init__(core_path, game, platform, system_dir, options)
        self.rom = rom_reader(platform, Path(game).read_bytes())

    def read(self, address, size):
        return super().read(address, size) or self.rom(address, size)


SFC_SCRIPT = SCRIPT.replace('if mem.u32be(0x80000040) ~= 0x80000100 then',
                            'if mem.read(0xC0FFC0, 7) ~= "MADE UP" or mem.u32be(0x80000040) ~= 0x80000100 then')


@needs_reader
def test_compare_gives_a_super_famicom_script_its_cartridge_over_snaps(tmp_path):
    """A script whose build check reads the cartridge's header refused every snap: a snap keeps memory only.
    compare now gives it the loaded game's cartridge, over snaps of that game (not another's), and so does
    `run` with the game's file; reread too, which still never meets the bar."""
    from yomikata.run import run
    for name in ("games/sfc", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/sfc/Made Up (Japan).sfc").write_bytes(made_up_sfc(True))
    (tmp_path / "games/sfc/Other (Japan).sfc").write_bytes(made_up_sfc(True, b"SOMETHING ELSE"))
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), Cartridge)
    s.load("sfc/Made Up (Japan).sfc", "fake_libretro.so", "sfc")
    assert s.script_try(SFC_SCRIPT, "SFC-MADEUP.lua")["loaded"]
    s.steps([{"wait": 3, "snap": True}, {"wait": 10, "snap": True}], prefix="p")
    s.snap_lines("p-000", ocr=["こんにちは"])
    s.snap_lines("p-001", ocr=["さようなら"])
    out = s.compare(source=SFC_SCRIPT, name="SFC-MADEUP.lua", snaps=["p-000", "p-001"], show=True)
    assert [(r["read"], r["error"]) for r in out["rows"]] == [("こんにちは", None), ("さようなら", None)]
    again = s.compare(source=SFC_SCRIPT, name="SFC-MADEUP.lua", scenes={"a": ["p-000"], "b": ["p-001"]},
                      live=True, reread=True, show=True)
    assert [r["read"] for r in again["rows"]] == ["こんにちは", "さようなら"] and not again["bar"]["met"]
    script = tmp_path / "SFC-MADEUP.lua"
    script.write_text(SFC_SCRIPT, encoding="utf-8")
    snap = tmp_path / "scratch/snaps/p-000.ram.gz"
    assert [e[1]["text"] for e in run(script, [snap], rom=made_up_sfc(True))[0].events if e[0] == "text"] == ["こんにちは"]
    assert "another build" in (run(script, [snap])[0].error or "")                    # no file: no cartridge
    s.close()
    # Snaps of another game get no cartridge from the one loaded now.
    t = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), Cartridge)
    t.load("sfc/Other (Japan).sfc", "fake_libretro.so", "sfc")
    out = t.compare(source=SFC_SCRIPT, name="SFC-MADEUP.lua", snaps=["p-000"], show=True)
    assert "another build" in (out["rows"][0]["error"] or "")
    t.close()


def unpack(packed: bytes) -> bytes:
    """A made-up decompressor: 0x00 n and n bytes as they are; 0x01 back length, that many bytes copied from back."""
    out, i = bytearray(), 0
    while i < len(packed):
        if packed[i] == 0:
            n = packed[i + 1]
            out += packed[i + 2:i + 2 + n]
            i += 2 + n
        else:
            back, n = packed[i + 1], packed[i + 2]
            for _ in range(n):
                out.append(out[-back])
            i += 3
    return bytes(out)


class Unpacker(FakeMachine):
    """A game whose script is packed: a press of a unpacks the next page into BUFFER (or, kept twice, the pages
    alternating between two places), 2 frames after the press. Beside it, as a real game has: the frame counter
    (0x10), the line that changes every 10 frames (0x100), a stack rewritten every frame, and a tile copy of the
    page whose first half holds while the page stands and whose second half moves every frame."""
    BUFFER, OTHER, TILES, STACK, SIZE = 0x400, 0x500, 0x800, 0x900, 64
    PACKED = [b"\x00\x0a" + bytes(range(0x21, 0x2B)) + b"\x01\x0a\x1e" + b"\x00\x04\x50\x51\x52\x53",
              b"\x00\x14" + bytes(range(0x40, 0x54)) + b"\x01\x05\x14",
              b"\x00\x08" + bytes(range(0x60, 0x68)) + b"\x01\x08\x20" + b"\x00\x02\x30\x31",
              None]                                                  # the fourth is the first again
    twice = False
    write = True

    def __init__(self, *args):
        super().__init__(*args)
        self.page, self.held, self.due = -1, False, None
        self.memory[self.BUFFER:self.BUFFER + self.SIZE] = b"\xff" * self.SIZE
        self.memory[self.OTHER:self.OTHER + self.SIZE] = b"\xff" * self.SIZE

    def text(self):
        packed = self.PACKED[self.page % len(self.PACKED)] or self.PACKED[0]
        return unpack(packed).ljust(self.SIZE, b"\xff")

    def run(self, frames, buttons, stick):
        for _ in range(frames):
            super().run(1, buttons, stick)
            if "a" in buttons and not self.held:
                self.page, self.due = self.page + 1, self.frames + 2
            self.held = "a" in buttons
            if self.due == self.frames and self.write:
                at = self.OTHER if self.twice and self.page % 2 else self.BUFFER
                self.memory[at:at + self.SIZE] = self.text()
            if self.page >= 0:
                half = self.text()[:self.SIZE // 2]
                moving = bytes((b + self.frames) & 0xFF for b in self.text()[self.SIZE // 2:])
                self.memory[self.TILES:self.TILES + self.SIZE] = half + moving
            self.memory[self.STACK:self.STACK + 32] = bytes((self.frames * 37 + i * 11) & 0xFF for i in range(32))


def unpacker_session(tmp_path, machine):
    for name in ("games/sfc", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "games/sfc/Made Up (Japan).sfc").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), machine)
    s.load("sfc/Made Up (Japan).sfc", "fake_libretro.so", "sfc")
    steps = []
    for k in range(4):                                  # a snap before each page opens, three while it stands
        steps += [{"wait": 1, "snap": f"b{k}"}, {"buttons": ["a"], "hold": 1, "wait": 5, "snap": f"p{k}a"},
                  {"wait": 9, "snap": f"p{k}b"}, {"wait": 9, "snap": f"p{k}c"}]
    s.steps(steps)
    return s


PAGES = [[f"p{k}a", f"p{k}b", f"p{k}c"] for k in range(4)]


def test_page_buffer_finds_where_a_packed_page_is_written_whole(tmp_path):
    """The buffer comes first, whole, with each page written by the first snap after its press (not before it),
    and the repeated page the same; the tile copy that moves while a page stands is ranked below it, the stack
    and the counter left out."""
    s = unpacker_session(tmp_path, Unpacker)
    out = s.page_buffer(PAGES, before=[f"b{k}" for k in range(4)], repeats=[[0, 3]])
    top = out["stretches"][0]
    assert top["address"] == f"0x{BASE + Unpacker.BUFFER:08X}" and top["stood"] == 1.0 and top["repeats"]
    assert top["length"] >= 30 and top["written"] == [0, 0, 0, 0]
    places = {r["address"]: r for r in out["stretches"]}
    tiles = places.get(f"0x{BASE + Unpacker.TILES:08X}")
    assert tiles and tiles["stood"] < 1.0 and tiles["score"] < top["score"]            # ranked down, not out
    assert f"0x{BASE + Unpacker.STACK:08X}" not in places                               # never stands: left out
    assert all(not (BASE + 0x10 <= int(a, 16) < BASE + 0x14) for a in places)            # the counter
    assert s.page_buffer(PAGES, within=[f"0x{BASE + 0x700:X}", f"0x{BASE + 0x1000:X}"])["stretches"][0]["address"] \
        == f"0x{BASE + Unpacker.TILES:08X}"
    with pytest.raises(HarnessError, match="two pages or more"):
        s.page_buffer([["p0a"]])
    with pytest.raises(HarnessError, match="pairs of page indices"):
        s.page_buffer(PAGES, repeats=[[0, 9]])
    s.close()


class Scenery(Unpacker):
    """Unpacker with what made Tales of Phantasia's first pages go wrong: two long stretches of a scene's
    graphics, unpacked anew for each page and standing while it does (each page's own bytes, all of them
    different), as long as a whole block of tiles."""
    GRAPHICS = ((0x1000, 6000), (0x3000, 6500))

    def __init__(self, *args):
        super().__init__(*args)
        self.memory += bytearray(0x8000 - len(self.memory))

    def run(self, frames, buttons, stick):
        super().run(frames, buttons, stick)
        for at, length in self.GRAPHICS:
            seed = (self.page + 1) * 7919 + at
            self.memory[at:at + length] = bytes((seed + i * 131 + (i >> 3) * 17) & 0xFF for i in range(length))


def test_page_buffer_ranks_against_the_size_a_page_takes(tmp_path):
    """Long stretches of graphics stand and differ per page as a buffer does, and by length alone they came first.
    Held to the size a page takes, given or estimated from the stretches that stood (those longer than cap left
    out of it), the buffer comes first."""
    s = unpacker_session(tmp_path, Scenery)
    buffer = f"0x{BASE + Unpacker.BUFFER:08X}"
    graphics = {f"0x{BASE + at:08X}" for at, _ in Scenery.GRAPHICS}
    out = s.page_buffer(PAGES)
    assert out["stretches"][0]["address"] == buffer and out["expected"]["from"] == "estimated"
    assert out["expected"]["size"] == out["stretches"][0]["length"]
    lengths = {r["address"]: r["length"] for r in out["stretches"]}
    assert graphics <= set(lengths) and all(lengths[g] > 40 * lengths[buffer] for g in graphics)   # by length, they'd win
    given = s.page_buffer(PAGES, size=48)
    assert given["stretches"][0]["address"] == buffer and given["expected"] == {"size": 48, "from": "given"}
    loose = s.page_buffer(PAGES, cap=10000)                  # graphics counted in the estimate: it goes wrong
    assert loose["expected"]["size"] > 1000
    s.close()


def test_page_buffer_gives_both_halves_of_a_buffer_kept_twice(tmp_path):
    """Pages that alternate between two buffers: both are given, side by side at the top, each said to alternate
    with the other."""
    class Twice(Unpacker):
        twice = True
    s = unpacker_session(tmp_path, Twice)
    out = s.page_buffer(PAGES)
    first, second = out["stretches"][:2]
    addresses = {f"0x{BASE + Unpacker.BUFFER:08X}", f"0x{BASE + Unpacker.OTHER:08X}"}
    assert {first["address"], second["address"]} == addresses
    assert first["alternates_with"] == second["address"] and second["alternates_with"] == first["address"]
    s.close()


def test_page_buffer_gives_no_stood_stretch_where_no_page_is_written(tmp_path):
    """With no buffer, nothing stands through the pages: the tile copy is still given, ranked down as one that
    moves, and nothing is passed off as a page's buffer."""
    class Nowhere(Unpacker):
        write = False
    s = unpacker_session(tmp_path, Nowhere)
    out = s.page_buffer(PAGES)
    assert out["stretches"] and all(r["stood"] < 1.0 for r in out["stretches"])
    s.close()


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


def test_grouped_finds_what_is_the_same_on_a_map_and_differs_between_maps(session):
    m = session.machine.memory
    for name, where, room in (("a1", 1, 5), ("a2", 1, 6), ("b1", 2, 5), ("c1", 3, 7), ("c2", 3, 7), ("d1", 2, 9)):
        m[0x300], m[0x301] = where, room                    # which map; something that changes within one
        session.run(1)
        session.state_save(name)
        session.snap(name)
    session.run(4)
    at = session.status()["frames"]
    groups = {"ship": ["a1", "a2"], "deck": ["b1"], "hold": ["c1", "c2"]}
    out = session.grouped(groups, within=["0x80000100", "0x80000400"])
    assert out == {"count": 1, "groups": 3, "places": [
        {"address": "0x80000300", "values": {"ship": "01", "deck": "02", "hold": "03"}}]}
    assert session.status()["frames"] == at                 # the game is back where it was
    assert session.grouped(groups, snaps=True, within=["0x80000100", "0x80000400"]) == out
    # Two groups that are one map after all: found when some may share a value, and said.
    groups["cabin"] = ["d1"]
    assert session.grouped(groups, within=["0x80000300", "0x80000302"])["count"] == 0
    out = session.grouped(groups, least=3, within=["0x80000300", "0x80000302"])
    assert out["places"] == [{"address": "0x80000300", "values": {"ship": "01", "deck": "02", "hold": "03", "cabin": "02"},
                              "alike": [["cabin", "deck"]]}]
    for bad in ({"ship": ["a1"]}, {"ship": ["a1"], "deck": []}, {"ship": ["a1"], "deck": ["never"]}):
        with pytest.raises(HarnessError):
            session.grouped(bad)


def test_grouped_keeps_only_places_whose_values_fit_and_says_when_it_cut_the_list(session):
    """A flag that is 0 on the field and 0x80 in a fight, among many places that differ (a fight loads other code
    over the field's): `values` "flag" or each group's value keeps it alone; a list past the most says it was cut
    (Chrono Cross: 230,000 places, the first 200 given, the flag not among them)."""
    m = session.machine.memory
    for name, flag, noise in (("f1", 0, 1), ("f2", 0, 1), ("b1", 0x80, 2), ("b2", 0x80, 2)):
        m[0x900], m[0x710:0x810] = flag, bytes([noise]) * 0x100      # the flag, above 256 places that differ too
        session.run(1)
        session.snap(name)
    groups = {"field": ["f1", "f2"], "fight": ["b1", "b2"]}
    every = session.grouped(groups, snaps=True)
    assert every["count"] > 200 and "cut" in every and "0x80000900" not in [p["address"] for p in every["places"]]
    for values in ("flag", {"field": "0", "fight": "!0"}, {"fight": "0x80"}):
        out = session.grouped(groups, snaps=True, values=values)
        assert [p["address"] for p in out["places"]] == ["0x80000900"] and "cut" not in out, (values, out)
    assert session.grouped(groups, snaps=True, values={"fight": "!0x80"}, within=["0x80000900", "0x80000901"])["count"] == 0
    for bad in ({"nowhere": "0"}, {"field": "zero"}, "flags"):
        with pytest.raises(HarnessError):
            session.grouped(groups, snaps=True, values=bad)


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
    out = session.watch("0x80000020", 4, size=1, buttons=["a"], hold=2, more=["0x80000013"])     # with the counter's low byte
    assert out["places"] == ["0x80000020", "0x80000013"] and [c["values"] for c in out["changes"]][:2] == ["01 0A", "01 0B"]
    out = session.watch("0x80000012", 2, size=1, stretch=2)
    assert out["places"] == ["0x80000012", "0x80000013"] and len(out["changes"]) == 2
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


def test_walk_to_goes_one_way_at_a_time_and_round_what_is_in_the_way(session):
    session.cursor_find()
    start = session.cursor_to()
    out = session.walk_to(start["x"] + 20, start["y"] - 10)
    assert out["reached"] is True and (out["x"], out["y"]) == (start["x"] + 20, start["y"] - 10) and "turned" not in out
    # A wall across the way right, at this height: it goes down first, then across.
    machine, plain = session.machine, session.machine._step

    def walled(buttons, stick):
        x, y = struct.unpack_from(">hh", machine.memory, 0x30)
        if "right" in buttons and x >= 120 and y < 100:
            buttons = set(buttons) - {"right"}
        plain(buttons, stick)
    machine._step = walled
    out = session.walk_to(140, 110, patience=8)
    assert out["reached"] is True and out["turned"] >= 1
    assert session.walk_to(100, 60, patience=8)["reached"] is True       # back round it
    out = session.walk_to(140, 60, patience=8, limit=400)                # straight at the wall: as near as it gets
    assert out["reached"] is False and out["x"] <= 120 and out["turned"] > 6


def test_walk_to_takes_near_for_near_enough(session):
    session.cursor_find()
    struct.pack_into(">hh", session.machine.memory, 0x30, 100, 80)
    out = session.walk_to(121, 71, limit=200, patience=8, linger=0)           # two a step: never the very place
    assert out["reached"] is False
    struct.pack_into(">hh", session.machine.memory, 0x30, 100, 80)
    out = session.walk_to(121, 71, near=1, linger=0)
    assert out["reached"] is True and abs(out["x"] - 121) <= 1 and abs(out["y"] - 71) <= 1


def test_walk_to_stops_where_the_map_changes(session):
    session.cursor_find()
    machine, plain = session.machine, session.machine._step

    def stairs(buttons, stick):
        plain(buttons, stick)
        if struct.unpack_from(">hh", machine.memory, 0x30) == (110, 80):   # stairs: another map, its own numbers
            struct.pack_into(">hh", machine.memory, 0x30, 30, 200)
            machine.memory[0x38] += 1
    machine._step = stairs
    struct.pack_into(">hh", machine.memory, 0x30, 100, 80)
    out = session.walk_to(140, 80)
    assert out["map_changed"] is True and out["reached"] is False and out["from"] == [108, 80]
    assert (out["x"], out["y"]) == (30, 200) and "map" not in out        # it did not walk on towards 140 there
    # By the place that says which map it is, where the numbers happen to lie close.
    struct.pack_into(">hh", machine.memory, 0x30, 108, 82)
    out = session.walk_to(140, 82, jump=0)
    assert out["reached"] is True
    struct.pack_into(">hh", machine.memory, 0x30, 100, 80)
    out = session.walk_to(140, 80, jump=0, map_address=f"0x{BASE + 0x38:08X}")
    assert out["map_changed"] is True and out["map"] == 2 and (out["x"], out["y"]) == (30, 200)
    # Stairs that act a moment after they are stood on.
    def slow(buttons, stick, count=[0]):
        plain(buttons, stick)
        count[0] = count[0] + 1 if struct.unpack_from(">hh", machine.memory, 0x30) == (120, 80) else 0
        if count[0] == 30:
            struct.pack_into(">hh", machine.memory, 0x30, 30, 200)
    machine._step = slow
    struct.pack_into(">hh", machine.memory, 0x30, 100, 80)
    assert session.walk_to(120, 80, linger=0)["reached"] is True
    struct.pack_into(">hh", machine.memory, 0x30, 100, 80)
    out = session.walk_to(120, 80)
    assert out["map_changed"] is True and out["from"] == [120, 80] and (out["x"], out["y"]) == (30, 200)


def test_map_picture_puts_the_games_numbers_over_the_screen(session):
    from PIL import Image
    session.cursor_find()
    kind, out, png = session.map_picture(tile=8, per=2, scale=2)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    assert kind == "both" and image.size == (64 * 2 + 22, 48 * 2 + 12)
    at = session.cursor_to()
    assert (out["x"], out["y"], out["per_tile"]) == (at["x"], at["y"], 2)
    # The character's tile is the middle one, 28 pixels from the left: three tiles and a part lie before it.
    assert out["columns"] == [at["x"] - 8, at["x"] + 8] and out["rows"] == [at["y"] - 6, at["y"] + 6]
    assert image.getpixel((22 + 28 * 2, 12 + 20 * 2)) == (255, 0, 0)     # its mark
    out = session.map_picture(tile=8, at_x=0, at_y=0)[1]
    assert out["columns"][0] == at["x"] and out["rows"][0] == at["y"]
    # A picture that doesn't keep the character in the middle: where the game says it draws it.
    struct.pack_into(">hh", session.machine.memory, 0x60, 24, 32)
    out = session.map_picture(tile=8, screen_x_address=f"0x{BASE + 0x60:08X}", screen_y_address=f"0x{BASE + 0x62:08X}")[1]
    assert out["columns"][0] == at["x"] - 2 and out["rows"][0] == at["y"] - 2      # the mark 16 across, 16 down


@needs_reader
def test_pictures_a_script_logs_for_want_of_a_reading_are_kept_in_scratch(session, tmp_path):
    """lib/glyphs.lua logs a picture its dictionary hasn't got, and the row it stood in: the harness keeps
    those lines for `yomikata unread`, and they aren't among a call's events; another log line is."""
    script = SCRIPT.replace("reader.on_frame(function()", "reader.on_frame(function(frame)\n"
                            "    if frame == 3 then reader.log('glyph 00AA000000000001 4x2x2 1be4') "
                            "reader.log('glyph-line 00AA000000000001 _ 00AA000000000001') reader.log('other') end")
    assert session.script_try(script, "GXXJ01.lua")["loaded"]
    out = session.run(6)
    assert [e["message"] for e in out["events"] if e["kind"] == "log"] == ["other"]
    assert (tmp_path / "scratch/glyphs/GXXJ01.log").read_text(encoding="utf-8").splitlines() == [
        "glyph 00AA000000000001 4x2x2 1be4", "glyph-line 00AA000000000001 _ 00AA000000000001"]


def test_a_scripts_info_logs_reach_a_calls_events_capped_below_its_text(session):
    """reader.log lines (info) come back among a call's events, as warnings do, but at most MAX_INFO of them, so a
    script that logs every frame doesn't crowd out what it says; warnings and text aren't held by that cap."""
    from yomikata.harness.session import MAX_INFO
    script = SCRIPT.replace("reader.on_frame(function()", "reader.on_frame(function(frame)\n"
                            "    reader.log('info ' .. frame) if frame % 10 == 0 then reader.warn('warn ' .. frame) end")
    assert session.script_try(script, "GXXJ01.lua")["loaded"]
    out = session.run(120)
    infos = [e for e in out["events"] if e["kind"] == "log" and e.get("level") == "info"]
    warns = [e for e in out["events"] if e["kind"] == "log" and e.get("level") != "info"]
    assert len(infos) == MAX_INFO and infos[0]["message"] == "info 1"
    assert len(warns) == 12 and [e for e in out["events"] if e["kind"] == "text"]


def test_the_second_shoulder_buttons_and_the_sticks_pressed_in_are_buttons_too(session):
    """A PlayStation 2 game's menus may want L2 and R2 (a naming screen's fields): they are taken as any
    button, with the ids a core knows them by, and a name that is no button is still refused."""
    from yomikata.replay.libretro import JOYPAD
    assert [JOYPAD[b] for b in ("l2", "r2", "l3", "r3")] == [12, 13, 14, 15]
    assert session.run(2, buttons=["a", "r2", "l3"])["frames"] > 0
    with pytest.raises(HarnessError, match="buttons are"):
        session.run(1, buttons=["r4"])


def test_explore_finds_where_one_can_walk_by_trying_and_walks_the_way_it_found(session):
    """A room walled off but for a gap: every place the pushes reach is listed, the game is left as it was,
    and a place behind the wall is walked to through the gap."""
    session.cursor_find()
    machine, plain = session.machine, session.machine._step

    def walled(buttons, stick):
        x, y = struct.unpack_from(">hh", machine.memory, 0x30)
        buttons = set(buttons)
        if x == 108 and "right" in buttons and y != 88:                  # a wall right of 108, open at 88 only
            buttons.discard("right")
        if x == 110 and "left" in buttons and y != 88:
            buttons.discard("left")
        plain(buttons, stick)
        if struct.unpack_from(">hh", machine.memory, 0x30) == (116, 76):   # stairs behind the wall
            struct.pack_into(">hh", machine.memory, 0x30, 30, 200)
    machine._step = walled
    struct.pack_into(">hh", machine.memory, 0x30, 100, 80)
    before, frames, way = machine.save_state(), machine.frames, json.dumps(session.route)
    out = session.explore(step=1, within=[96, 76, 116, 92])
    assert (out["x"], out["y"]) == (100, 80) and out["columns"] == [96, 116] and out["rows"] == [76, 92]
    assert out["map"][4] == "#.#.@.#.#.#.#.#.#.#.#" and out["map"][1] == "." * 21      # two a push: every other place
    assert out["places"] == 11 * 9 - 1 and out["doors"] == [{"at": [114, 76], "push": "right"}, {"at": [116, 78], "push": "up"}]
    assert (machine.save_state(), machine.frames, json.dumps(session.route)) == (before, frames, way)   # nothing happened
    out = session.explore(step=1, within=[96, 76, 116, 92], to=[112, 80], near=0)
    assert out["reached"] is True and (out["x"], out["y"]) == (112, 80) and out["pushes"] == 14 and "map" not in out
    assert struct.unpack_from(">hh", machine.memory, 0x30) == (112, 80) and machine.frames > frames
    # Not reached, it walks as far as it got, to the place nearest the one asked for, and says where it stopped.
    out = session.explore(step=1, within=[96, 76, 116, 92], to=[200, 80])
    assert out["reached"] is False and out["stopped_at"] == [116, 80] and "didn't reach [200, 80]" in out["why"]
    assert struct.unpack_from(">hh", machine.memory, 0x30) == (116, 80)
    assert session.explore(step=1, within=[96, 76, 116, 92], to=[200, 80])["why"].startswith("no place it got to")
    out = session.explore(step=1, most=5)
    assert out["places"] == 5 and "more" in out
    with pytest.raises(HarnessError, match="within"):
        session.explore(within=[1, 2, 3])


def test_explore_hints_at_a_place_kept_in_pixels_and_walks_it_in_cells(session):
    """A push that moves the place several of its numbers looks like pixels: explore says to give grid and
    whole. With them a push is one place, and to and jump are in cells."""
    session.cursor_find()
    struct.pack_into(">hh", session.machine.memory, 0x30, 100, 80)
    out = session.explore(step=4, within=[84, 64, 116, 96])            # 8 of the numbers a push
    assert "grid 8 and whole" in out["hint"]
    struct.pack_into(">hh", session.machine.memory, 0x30, 96, 80)      # on the grid of pushes: cell (12, 10)
    cells = session.explore(step=4, grid=8, whole=True, within=[10, 8, 14, 12])
    assert "hint" not in cells and (cells["x"], cells["y"]) == (12, 10) and cells["places"] == 25
    out = session.explore(step=4, grid=8, whole=True, within=[10, 8, 14, 12], to=[14, 12], near=0)
    assert out["reached"] is True and struct.unpack_from(">hh", session.machine.memory, 0x30) == (112, 96)


@needs_reader
def test_steps_snaps_the_moment_the_script_says_text(session):
    """said: a snap `after` frames from each saying, mid-wait, where a snap at the step's end would come after
    the line had given way to the next (the line here changes every 10 frames)."""
    assert session.script_try(SCRIPT)["loaded"]
    out = session.steps([{"wait": 35}], prefix="w", said={"after": 2, "shot": True})
    texts = [e for e in out[1]["events"] if e["kind"] == "text"]
    taken = out[1]["said"]
    assert len(taken) == len(texts) >= 3
    assert [t["frame"] for t in taken] == [e["frame"] + 2 for e in texts]
    assert [t["snap"] for t in taken][:2] == ["w-said-00", "w-said-01"]
    lines = [json.loads((session.roots.scratch / f"snaps/{t['snap']}.json").read_text())["said"][-1]["text"]
             for t in taken]
    assert lines == [e["text"] for e in texts]                              # each snap holds the line just said
    assert session.each_frame is None                                        # the hook is put back
    with pytest.raises(HarnessError, match="said is an object"):
        session.steps([{"wait": 1}], said={"when": 1})


def test_explore_says_when_a_window_takes_the_pad_and_closes_it_with_close(session):
    """A message open takes the pad (Dragon Quest I & II's, after a search): explore without `close` gives one
    place and says why it may be; with `close` it presses the button, keeps the press in the record of the
    way, and maps the room from there."""
    session.cursor_find()
    machine, plain = session.machine, session.machine._step

    def windowed(buttons, stick):
        buttons = set(buttons)
        if machine.memory[0x7F0]:                     # a message open: the pad moves nobody, A closes it
            if "a" in buttons:
                machine.memory[0x7F0] = 0
            buttons = set()
        plain(buttons, stick)
    machine._step = windowed
    struct.pack_into(">hh", machine.memory, 0x30, 100, 80)
    machine.memory[0x7F0] = 1
    before, frames, way = machine.save_state(), machine.frames, json.dumps(session.route)
    out = session.explore(step=1, within=[96, 76, 104, 84])
    assert out["places"] == 1 and "close" in out["still"] and "closed" not in out
    assert (machine.save_state(), machine.frames, json.dumps(session.route)) == (before, frames, way)
    out = session.explore(step=1, within=[96, 76, 104, 84], close="a")
    assert out["places"] > 1 and out["closed"] == 1 and "still" not in out
    assert machine.memory[0x7F0] == 0 and machine.frames > frames and json.dumps(session.route) != way
    assert ["a"] in [m[1] for m in session.route["moves"] if isinstance(m, list)]
    again = session.explore(step=1, within=[96, 76, 104, 84], close="a")      # nothing to close: no press
    assert "closed" not in again and again["places"] == out["places"]


def test_explore_tells_a_step_counted_late_from_a_window_and_presses_nothing(session):
    """A game that counts a step only once it is over, 26 frames after the press (Pocket Monsters Gin), and has
    no window open: pushes of 16 frames see nothing move, a longer trial push does, so `close` presses nothing
    and `still` says which step to give; with that step the map is found."""
    machine, plain = session.machine, session.machine._step
    ways = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    def walking(buttons, stick):
        m = machine.memory
        if m[0x700]:                                  # a step under way: the place changes when it is over
            m[0x700] -= 1
            if not m[0x700]:
                x, y = struct.unpack_from(">hh", m, 0x30)
                dx, dy = list(ways.values())[m[0x701]]
                struct.pack_into(">hh", m, 0x30, x + dx, y + dy)
        else:
            for k, way in enumerate(ways):
                if way in buttons:
                    m[0x700], m[0x701] = 26, k
                    break
        plain(set(buttons) & {"a", "b"}, stick)
    machine._step = walking
    struct.pack_into(">hh", machine.memory, 0x30, 10, 10)
    at = dict(x_address=f"0x{BASE + 0x30:X}", y_address=f"0x{BASE + 0x32:X}", size=2)
    before, frames, way = machine.save_state(), machine.frames, json.dumps(session.route)
    out = session.explore(step=16, close="a", within=[8, 8, 12, 12], **at)
    assert out["places"] == 1 and "closed" not in out and "give step 32" in out["still"], out
    assert (machine.save_state(), machine.frames, json.dumps(session.route)) == (before, frames, way)   # nothing pressed
    out = session.explore(step=32, within=[8, 8, 12, 12], **at)
    assert out["places"] > 1 and "still" not in out, out


def test_explore_presses_through_the_fights_its_steps_bring(session):
    """Grass at x 104 starts a fight (a flag at 0x7F2) that holds the pad until a is pressed three times: explore
    without `fight` can't get past it; with it, each fight is pressed through and the map goes on behind the grass,
    the fights counted; a walk to a place behind it keeps the fights' presses in the record of the way."""
    session.cursor_find()
    machine, plain = session.machine, session.machine._step

    def grassy(buttons, stick):
        m = machine.memory
        pressed = "a" in buttons and not m[0x7F4]
        m[0x7F4] = 1 if "a" in buttons else 0
        if m[0x7F2]:                                   # in a fight: the pad walks nobody
            if pressed:
                m[0x7F3] += 1
                if m[0x7F3] >= 3:
                    m[0x7F2], m[0x7F3] = 0, 0
            plain(set(), stick)
            return
        before = struct.unpack_from(">hh", m, 0x30)
        plain(buttons, stick)
        x, y = struct.unpack_from(">hh", m, 0x30)
        if x == 104 and (x, y) != before:              # a step into the grass
            m[0x7F2] = 1
    machine._step = grassy
    struct.pack_into(">hh", machine.memory, 0x30, 100, 80)
    box = [96, 78, 112, 82]
    out = session.explore(step=1, within=box)
    assert out["columns"] == [96, 104] and "fights" not in out, out                  # held in the grass
    mode = {"address": f"0x{BASE + 0x7F2:X}", "equals": "1", "wait": 4}
    out = session.explore(step=1, within=box, fight=mode)
    assert out["columns"] == [96, 112] and out["fights"] >= 1, out
    assert all((f["presses"], f["stopped"]) == (3, "mode ended") and f["at"][0] == 104 for f in out["fought"])
    way = len(session.route["moves"])
    out = session.explore(step=1, within=box, fight=mode, to=[110, 80], near=0)
    assert out["reached"] is True and (out["x"], out["y"]) == (110, 80) and machine.memory[0x7F2] == 0, out
    assert sum(1 for m in session.route["moves"][way:] if isinstance(m, list) and m[1] == ["a"]) == 3
    with pytest.raises(HarnessError, match="fight"):
        session.explore(step=1, fight={"address": "0x10"})


def test_explore_counts_a_push_that_changes_the_room_as_a_door_however_little_the_place_moved(session):
    """A doorway at x 106: stepping through it changes the room (a byte at 0x7F0) and moves the place no more
    than any step, so at the default jump it is a place like any other and the next room is mapped as if it were
    this one; given `room`, the push through is a door, and the map stops at it."""
    session.cursor_find()
    machine, plain = session.machine, session.machine._step

    def rooms(buttons, stick):
        plain(buttons, stick)
        x = struct.unpack_from(">h", machine.memory, 0x30)[0]
        machine.memory[0x7F0] = 2 if x > 106 else 1
    machine._step = rooms
    struct.pack_into(">hh", machine.memory, 0x30, 100, 80)
    machine.memory[0x7F0] = 1
    box = [96, 78, 112, 82]
    out = session.explore(step=1, within=box)
    assert out["columns"] == [96, 112] and out["doors"] == [], out
    out = session.explore(step=1, within=box, room=f"0x{BASE + 0x7F0:X}")
    assert out["columns"] == [96, 106] and {"at": [106, 80], "push": "right"} in out["doors"], out
    with pytest.raises(HarnessError, match="room_size"):
        session.explore(step=1, room=f"0x{BASE + 0x7F0:X}", room_size=3)


def test_explore_tells_a_fade_from_a_fight_by_how_long_the_mode_holds_or_by_a_second_mode(session):
    """Stepping onto x 100 fades the screen: the fight's flag (0x7F2) is set for two frames and drops by itself.
    Grass at x 104 starts a fight that holds the flag, and a second flag (0x7F5), until a is pressed three times.
    Without `holds` every fade is pressed through as a fight; with it, or with `also`, only the grass's are."""
    session.cursor_find()
    machine, plain = session.machine, session.machine._step

    def fades(buttons, stick):
        m = machine.memory
        pressed = "a" in buttons and not m[0x7F4]
        m[0x7F4] = 1 if "a" in buttons else 0
        if m[0x7F6]:                                   # a fade: the flag stands for two frames
            m[0x7F6] -= 1
            if not m[0x7F6]:
                m[0x7F2] = 0
        if m[0x7F2] and m[0x7F5]:                      # in a fight: the pad walks nobody
            if pressed:
                m[0x7F3] += 1
                if m[0x7F3] >= 3:
                    m[0x7F2], m[0x7F3], m[0x7F5] = 0, 0, 0
            plain(set(), stick)
            return
        before = struct.unpack_from(">hh", m, 0x30)
        plain(buttons, stick)
        x, y = struct.unpack_from(">hh", m, 0x30)
        if (x, y) != before and x == 100:
            m[0x7F2], m[0x7F6] = 1, 2
        if (x, y) != before and x == 104:
            m[0x7F2], m[0x7F5] = 1, 1
    machine._step = fades
    struct.pack_into(">hh", machine.memory, 0x30, 102, 80)
    box = [96, 78, 112, 82]
    mode = {"address": f"0x{BASE + 0x7F2:X}", "equals": "1", "wait": 4, "begin": 1}
    out = session.explore(step=1, within=box, fight=mode)
    assert any(f["at"][0] == 100 for f in out["fought"]), out                      # the fades counted
    for watch in ({"holds": 6}, {"also": {"address": f"0x{BASE + 0x7F5:X}", "equals": "1"}}):
        out = session.explore(step=1, within=box, fight={**mode, **watch})
        assert out["columns"] == [96, 112] and out["fights"] >= 1, out
        assert all(f["at"][0] == 104 and (f["presses"], f["stopped"]) == (3, "mode ended") for f in out["fought"]), out
    with pytest.raises(HarnessError, match="also"):
        session.explore(step=1, fight={**mode, "also": {"address": "0x10"}})
    # `first`: pressed once as a fight begins, before the presses that end it (a menu's cursor set first).
    way = len(session.route["moves"])
    out = session.explore(step=1, within=box, fight={**mode, "holds": 6, "first": [["b"]]}, to=[110, 80], near=0)
    assert out["reached"] is True and any(m[1] == ["b"] for m in session.route["moves"][way:] if isinstance(m, list))


@needs_reader
def test_things_find_finds_the_people_a_map_keeps_and_interact_tries_each(session):
    """A made-up map: the party's place at 0x30, and its people after it in the same list, 0x60 bytes a record, each
    with its place at the same offsets (x, then y). things_find finds that list from the party's place;
    interact walks next to each, presses a, and says who spoke (two of the three do, the script reading what
    they say), and leaves the game where it was."""
    session.cursor_find()
    machine, plain = session.machine, session.machine._step
    people = [(110, 80, "おはよう"), (100, 100, ""), (130, 90, "こんばんは")]
    for k, (x, y, _) in enumerate(people):
        struct.pack_into(">hh", machine.memory, 0x90 + 0x60 * k, x, y)

    def map_(buttons, stick):
        m = machine.memory
        pressed = "a" in buttons and not m[0x3F0]
        m[0x3F0] = 1 if "a" in buttons else 0
        plain(buttons, stick)
        x, y = struct.unpack_from(">hh", m, 0x30)
        if pressed:
            m[0x3F1] = 0
            for k, (px, py, line) in enumerate(people):
                if line and abs(px - x) <= 4 and abs(py - y) <= 4:
                    m[0x3F1] = k + 1
        line = people[m[0x3F1] - 1][2].encode("cp932") if m[0x3F1] else b""
        m[0x100:0x120] = line + bytes(0x20 - len(line))
    machine._step = map_
    struct.pack_into(">hh", machine.memory, 0x30, 100, 80)
    machine.memory[0x100:0x120] = bytes(0x20)
    assert session.script_try(SCRIPT, "GXXJ01.lua")["loaded"]
    found = session.things_find(near=64)
    assert found["stride"] == 0x60 and [(t["x"], t["y"]) for t in found["things"]] == [(110, 80), (100, 100), (130, 90)]
    assert found["holds_party"] and found["things"][0]["address"] == f"0x{BASE + 0x90:X}"
    frames, place = machine.frames, struct.unpack_from(">hh", machine.memory, 0x30)
    out = session.interact(step=1, wait=10, near=64)
    spoke = {tuple(t["thing"]): t["said"] for t in out["tried"]}
    assert all(t["reached"] for t in out["tried"]), out
    assert spoke == {(110, 80): 1, (100, 100): 0, (130, 90): 1}, out
    assert machine.frames == frames and struct.unpack_from(">hh", machine.memory, 0x30) == place   # put back
    kept = session.interact(things=[[130, 90]], step=1, wait=10, keep=0)
    assert kept["kept"] == 0 and machine.frames > frames                 # left after the try that was kept


@needs_reader
def test_expedition_walks_room_to_room_until_something_is_said_and_its_legs_walk_again(session):
    """Four made-up rooms (a byte at 0x7F0 says which): from the first, a door west leads to a dead end and a door
    east to a second room, whose door south leads to a third where something is said on arriving. expedition maps
    each room, goes through each door, and stops at the saying with the legs that led there; the legs, walked
    again from the start, reach the same room and the same saying without mapping."""
    session.cursor_find()
    machine, plain = session.machine, session.machine._step
    walls = {1: (90, 112, 78, 82), 2: (20, 40, 60, 100), 3: (200, 220, 50, 60), 4: (150, 160, 10, 20)}

    def rooms(buttons, stick):
        m = machine.memory
        plain(buttons, stick)
        x, y = struct.unpack_from(">hh", m, 0x30)
        here = m[0x7F0]
        x0, x1, y0, y1 = walls[here]
        if here == 1 and x > x1 and y <= 80:
            m[0x7F0], x, y = 2, 20, 80
        elif here == 1 and x < x0:
            m[0x7F0], x, y = 4, 155, 15
        elif here == 2 and y > y1:
            m[0x7F0], x, y = 3, 210, 55
        else:
            x, y = max(x0, min(x, x1)), max(y0, min(y, y1))
        struct.pack_into(">hh", m, 0x30, x, y)
        line = "とうちゃく".encode("cp932") if m[0x7F0] == 3 else b""
        m[0x100:0x120] = line + bytes(0x20 - len(line))
    machine._step = rooms
    struct.pack_into(">hh", machine.memory, 0x30, 100, 80)
    machine.memory[0x7F0] = 1
    machine.memory[0x100:0x120] = bytes(0x20)
    assert session.script_try(SCRIPT, "GXXJ01.lua")["loaded"]
    room = f"0x{BASE + 0x7F0:X}"
    start = machine.save_state()
    out = session.expedition(room=room, step=1, rooms=6)
    assert out["stopped"] == "said" and out["room"] == "3" and out["said"] == ["とうちゃく"], out
    assert [leg["push"] for leg in out["legs"]][-1] == "down" and len(out["legs"]) == 2, out
    assert machine.memory[0x7F0] == 3
    doors = {d["to"] for d in out["rooms"]["1"]["doors"]}
    assert "2" in doors, out
    machine.load_state(start)
    session.reader.state_loaded()
    again = session.expedition(room=room, step=1, legs=out["legs"])
    assert again["legs_walked"] == 2 and again["room"] == 3 and again["said"] == ["とうちゃく"], again
    # Heading right then down (as far as each room goes that way) finds the same doors without mapping all.
    machine.load_state(start)
    session.reader.state_loaded()
    headed = session.expedition(room=room, step=1, heading="right", rooms=6)
    assert headed["stopped"] in ("said", "every door tried") and "1" in headed["rooms"], headed


@needs_reader
def test_events_says_where_an_event_waits_and_expedition_walks_there_first(session):
    """A made-up room with a door east and, out of the way, a place where a scene plays when stepped on while
    its flag (a byte at 0x7F1) is clear. The script's `events` function gives that place while the flag is
    clear; the `events` tool says it, and expedition with `events` walks there before any door and stops at
    the scene's saying. Once the flag is set, no place is given and expedition goes on to the door."""
    session.cursor_find()
    machine, plain = session.machine, session.machine._step

    def room(buttons, stick):
        m = machine.memory
        plain(buttons, stick)
        x, y = struct.unpack_from(">hh", m, 0x30)
        x, y = max(20, min(x, 35)), max(20, min(y, 32))
        if m[0x7F0] == 1 and x >= 35 and y <= 21:
            m[0x7F0], x, y = 2, 20, 20
        struct.pack_into(">hh", m, 0x30, x, y)
        if m[0x7F0] == 1 and (x, y) == (22, 31):
            m[0x7F1] = 1
        line = "おまちかね".encode("cp932") if m[0x7F0] == 1 and (x, y) == (22, 31) else b""
        m[0x100:0x120] = line + bytes(0x20 - len(line))
    machine._step = room
    struct.pack_into(">hh", machine.memory, 0x30, 30, 25)
    machine.memory[0x7F0], machine.memory[0x7F1] = 1, 0
    machine.memory[0x100:0x120] = bytes(0x20)
    events = SCRIPT + """
function events()
    if mem.u8(0x800007F1) == 0 and mem.u8(0x800007F0) == 1 then return { { 22, 31 } } end
    return {}
end
"""
    assert session.script_try(events, "GXXJ01.lua")["loaded"]
    assert session.events()["places"] == [[22, 31]]
    start = machine.save_state()
    out = session.expedition(room=f"0x{BASE + 0x7F0:X}", step=1, rooms=3, events=True, keep="xp-said")
    assert out["stopped"] == "said" and out["event_at"] == [22, 31] and out["said"] == ["おまちかね"], out
    assert out["kept"] == "xp-said" and (session.roots.scratch / "states" / "xp-said.route.json").is_file()
    assert machine.memory[0x7F1] == 1 and out["legs"] == []
    assert session.events()["places"] == []
    # Done already: nothing waits, and the walk goes through the door as before.
    machine.load_state(start)
    machine.memory[0x7F1] = 1
    session.reader.state_loaded()
    on = session.expedition(room=f"0x{BASE + 0x7F0:X}", step=1, rooms=3, events=True)
    assert on["rooms"]["1"]["events"] == [] and any(d["to"] == "2" for d in on["rooms"]["1"]["doors"]), on
    # Time running out on the walk to the event's place: with keep, where the walk got to is kept, not the start.
    machine.load_state(start)
    session.reader.state_loaded()
    ticks, late = [0], session._late
    def soon():
        ticks[0] += 1
        return ticks[0] > 12
    session._late = soon
    cut = session.expedition(room=f"0x{BASE + 0x7F0:X}", step=1, rooms=3, events=True, keep="xp-k")
    session._late = late
    assert cut["stopped"] == "time" and cut["kept"] == "xp-k", cut
    walked = struct.unpack_from(">hh", machine.memory, 0x30)
    assert walked != (30, 25)
    session.state_load("xp-k")
    assert struct.unpack_from(">hh", machine.memory, 0x30) == walked


@needs_reader
def test_replay_plays_the_way_to_a_saved_state_and_goes_on_from_what_it_kept(session):
    """A made-up game says a line at each press of a (a counter at 0x7F2 picks it). From a state, three presses
    are made and a second state saved; back at the first, replay plays the same moves with the script on and
    snaps each saying. A replay whose time runs out mid-way keeps where it got to: loaded, a replay to the same
    state goes on from there and ends on that state's way exactly."""
    machine, plain = session.machine, session.machine._step
    lines = ["いち", "にい", "さん"]

    def game(buttons, stick):
        m = machine.memory
        plain(buttons, stick)
        if "a" in buttons and m[0x7F3] == 0:
            m[0x7F2] += 1
        m[0x7F3] = 1 if "a" in buttons else 0
        line = lines[m[0x7F2] - 1].encode("cp932") if m[0x7F2] else b""
        m[0x100:0x120] = line + bytes(0x20 - len(line))
    machine._step = game
    machine.memory[0x7F2] = machine.memory[0x7F3] = 0
    machine.memory[0x100:0x120] = bytes(0x20)
    session.state_save("rp-a")
    session.steps([{"buttons": ["a"], "hold": 4, "wait": 40} for _ in range(3)])
    session.state_save("rp-b")
    assert machine.memory[0x7F2] == 3
    session.state_load("rp-a")
    assert session.script_try(SCRIPT, "GXXJ01.lua")["loaded"]
    out = session.replay("rp-b", said={"after": 2})
    assert out["reached"] == "rp-b" and len(out["said"]) == 3, out
    assert [e["text"] for e in out["events"] if e["kind"] == "text"] == lines
    assert machine.memory[0x7F2] == 3
    # The call's time runs out after a few frames: where it got to is kept, and a replay from there ends the way.
    session.state_load("rp-a")
    ticks, late = [0], session._late
    def soon():
        ticks[0] += 1
        return ticks[0] > 50
    session._late = soon
    cut = session.replay("rp-b", keep="rp-k")
    session._late = late
    assert cut["stopped"] == "time" and cut["kept"] == "rp-k" and 0 < cut["left"], cut
    session.state_load("rp-k")
    on = session.replay("rp-b")
    assert on["reached"] == "rp-b" and machine.memory[0x7F2] == 3
    b = json.loads((session.roots.scratch / "states" / "rp-b.route.json").read_text(encoding="utf-8"))
    assert session.route["moves"] == b["moves"]
    # A state off the way is refused.
    session.state_load("rp-b")
    session.steps([{"buttons": ["b"], "hold": 4}])
    with pytest.raises(HarnessError):
        session.replay("rp-a")


def test_explore_keeps_the_nearest_place_when_its_time_runs_out(session):
    """explore towards a place, its time running out on the way: with keep, the nearest place it got to is saved
    as a state, from which a second explore goes on."""
    session.cursor_find()
    ticks, late = [0], session._late
    def soon():
        ticks[0] += 1
        return ticks[0] > 40
    session._late = soon
    out = session.explore(to=[160, 40], near=0, step=1, keep="ex-k")
    session._late = late
    assert out["reached"] is False and out["kept"] == "ex-k", out
    stood = out["stopped_at"]
    session.state_load("ex-k")
    here = session.explore(most=1)
    assert [here["x"], here["y"]] == stood


def _made_up_fight(session, enemy=100, party=50):
    """A made-up boss fight: 0x7F5 is 1 during it, 0x7F6 1 while the command menu is up, 0x7F7 the boss's HP and
    0x7F8 the party's, 0x7F9 the cursor (down: magic), 0x7F0 the room (0 once the game is over: the title). The
    command a attacks the shell (the boss hits back for 40), down then a casts (50 to the boss), b heals (the
    party back to 50); after each command the boss hits for 10, and the menu comes back 10 frames on."""
    machine, plain = session.machine, session.machine._step
    m = machine.memory
    m[0x7F0], m[0x7F5], m[0x7F6], m[0x7F7], m[0x7F8], m[0x7F9], m[0x7FA], m[0x7FB] = 1, 1, 1, enemy, party, 0, 0, 0
    m[0x7FE] = 0

    def game(buttons, stick):
        plain(buttons, stick)
        m = machine.memory                          # a loaded state brings memory of its own
        pressed = {b for b in buttons} - ({"a"} if m[0x7FA] & 1 else set()) - ({"down"} if m[0x7FA] & 2 else set()) \
            - ({"b"} if m[0x7FA] & 4 else set())
        m[0x7FA] = (1 if "a" in buttons else 0) | (2 if "down" in buttons else 0) | (4 if "b" in buttons else 0)
        if m[0x7F5] != 1 and not m[0x7FE]:
            return
        if m[0x7FE]:
            m[0x7FE] += 1
            if m[0x7FE] > 200:
                m[0x7F0], m[0x7FE] = 0, 0
            return
        if m[0x7F6] == 0:
            m[0x7FB] += 1
            if m[0x7FB] >= 10:
                m[0x7F6], m[0x7FB] = 1, 0
            return
        if "down" in pressed:
            m[0x7F9] = 1
        acted = False
        if "a" in pressed:
            if m[0x7F9] == 1:
                m[0x7F7] = max(0, m[0x7F7] - 50)
            else:
                m[0x7F8] = max(0, m[0x7F8] - 40)
            acted = True
        elif "b" in pressed:
            m[0x7F8], acted = 50, True
        if m[0x7FE]:
            m[0x7FE] += 1
            if m[0x7FE] > 200:
                m[0x7F0], m[0x7FE] = 0, 0          # lost: back to the title, a while after the fight ends
            return
        if acted:
            m[0x7F9], m[0x7F6] = 0, 0
            m[0x7F8] = max(0, m[0x7F8] - 10)
            if m[0x7F7] == 0:
                m[0x7F5] = 0                       # won
            elif m[0x7F8] == 0:
                m[0x7F5], m[0x7FE] = 2, 1          # lost: the fight's mode ends, the title comes later
    machine._step = game
    at = lambda a: f"0x{BASE + a:X}"
    return {"address": at(0x7F5), "equals": "1", "menu": {"address": at(0x7F6), "equals": "1"},
            "room": {"address": at(0x7F0)}, "after": 5, "hold": 2, "wait": 2}


def test_fight_stops_at_a_game_over_and_wins_by_the_next_plan_from_the_kept_state(session):
    """Attacking the shell loses: the room goes back to the title's, the game over is seen at once and nothing
    more is pressed; the next plan (magic: down, then a) is tried from the state kept as the fight began, and
    wins. With a plain fight watch (no plan) a loss is said as a game over too."""
    fight = _made_up_fight(session)
    attack, magic = [[{"buttons": ["a"]}]], [[{"buttons": ["down"]}, {"buttons": ["a"]}]]
    out = session.fight({**fight, "plan": attack}, plans=[magic], keep="boss")[1]
    assert out["fought"] and out["won"], out
    assert out["tries"][0]["stopped"] == "game over" and "room" in out["tries"][0]["game_over"], out
    assert out["tries"][1]["stopped"] == "mode ended" and out["tries"][1]["turns"] == 2, out
    assert session.machine.memory[0x7F0] == 1 and session.machine.memory[0x7F7] == 0
    assert (session.roots.scratch / "states" / "boss.state.gz").is_file() and out["kept_after"] == "boss-won"
    # Lost every way: the game stands at the kept state, before the fight.
    session.state_load("boss")
    lost = session.fight({**fight, "plan": attack}, keep="boss2")[1]
    assert lost["won"] is False and session.machine.memory[0x7F5] == 1 and session.machine.memory[0x7F8] == 50, lost
    # A game_over mode stops the plain watch too.
    session.state_load("boss")
    plain = session.fight({k: v for k, v in fight.items() if k not in ("menu", "room")}
                          | {"game_over": [{"address": f"0x{BASE + 0x7F0:X}", "equals": "0"}]}, keep="boss3")[1]
    assert plain["tries"][0]["game_over"] == "game_over mode 0", plain


def test_fight_presses_idle_through_a_talk_that_holds_the_menu(session):
    """After the first command a talk in the fight holds the menu until a is pressed (0x7FC set): without idle
    the menu never comes and the try ends; with idle (a every 30 frames) the talk is pressed through and the
    fight is won, and the outcome comes with a picture of how it ended."""
    fight = _made_up_fight(session)
    machine, step = session.machine, session.machine._step
    machine.memory[0x7FC] = machine.memory[0x7FD] = 0

    def talky(buttons, stick):
        m = machine.memory
        before = m[0x7F6]
        held = m[0x7FC]
        step(buttons, stick)
        m = machine.memory
        if held == 1:
            m[0x7F6] = 0                            # the talk holds the menu back
            if "a" in buttons and not m[0x7FD]:
                m[0x7FC] = 2
        elif held == 0 and before == 1 and m[0x7F6] == 0:
            m[0x7FC] = 1                            # the first command brings the talk
        m[0x7FD] = 1 if "a" in buttons else 0
    machine._step = talky
    session.state_save("talk-start")
    magic = [[{"buttons": ["down"]}, {"buttons": ["a"]}]]
    stuck = session.fight({**fight, "plan": magic, "most": 20}, keep="talk-k")
    assert stuck[1]["tries"][0]["stopped"] == "no menu for a minute" and stuck[2].startswith(b"\x89PNG"), stuck[1]
    session.state_load("talk-start")
    won = session.fight({**fight, "plan": magic, "idle": {"buttons": ["a"], "every": 30}}, keep="talk-k2")
    assert won[1]["won"], won


def test_fight_heals_when_the_hp_it_reads_is_low(session):
    """A boss of 250 HP takes five casts; the party (50 HP, 10 lost a turn) heals with b whenever it is below 25."""
    fight = _made_up_fight(session, enemy=250)
    magic = [[{"buttons": ["down"]}, {"buttons": ["a"]}]]
    heal = {"hp": {"address": f"0x{BASE + 0x7F8:X}"}, "below": 25, "turn": [{"buttons": ["b"]}]}
    out = session.fight({**fight, "plan": magic, "heal": heal}, keep="boss-h")[1]
    assert out["won"] and out["tries"][0]["turns"] == 5 and out["tries"][0]["healed"] >= 1, out
    with pytest.raises(HarnessError):
        session.fight({**fight, "plan": magic, "menu": None})


def test_the_record_gives_each_games_cost_up_to_its_bar(tmp_path, capsys):
    """`yomikata calls --trend`: for each game, the calls, runs and minutes from its first call to the first
    comparison by scenes that met the bar; a comparison's outcome is kept with its call (counts only)."""
    from yomikata import calls, cli
    def line(at, session, game, tool, arguments=None, outcome=None, seconds=1.0):
        return json.dumps({"at": f"2026-01-01T{at}Z", "session": session, "game": game, "tool": tool,
                           "arguments": arguments or {}, "ok": True, "seconds": seconds,
                           **({"outcome": outcome} if outcome else {})})
    by_scenes = {"scenes": "3 keys", "live": True}
    record = tmp_path / "calls.jsonl"
    record.write_text("\n".join([
        line("10:00:00", "a", "ps1/First (Japan).chd", "load"), line("10:01:00", "a", "ps1/First (Japan).chd", "steps"),
        line("10:30:00", "b", "ps1/First (Japan).chd", "compare", by_scenes, {"bar": False, "screens": 12, "scenes": 2}),
        line("10:45:00", "c", "ps1/First (Japan).chd", "compare", by_scenes, {"bar": True, "screens": 31, "scenes": 3}),
        line("10:50:00", "c", "ps1/First (Japan).chd", "screenshot"),
        line("11:00:00", "d", "sfc/Old.sfc", "load"), line("11:10:00", "d", "sfc/Old.sfc", "compare", by_scenes),
        line("11:20:00", "d", "sfc/Old.sfc", "run"),                       # a record from before outcomes were kept
        line("12:00:00", "e", "ps2/Begun.chd", "load"), line("12:02:30", "e", "ps2/Begun.chd", "compare", {"snaps": "4 items"}),
    ]) + "\n", encoding="utf-8")
    rows = calls.trend(record)
    assert [(r["game"], r["calls"], r["runs"], r["minutes"], r["met"], r["screens"], r["calls_after"]) for r in rows] == [
        ("First (Japan)", 4, 3, 45.0, True, 31, 1), ("Old", 2, 1, 10.0, None, None, 1), ("Begun", 2, 1, 2.5, None, None, 0)]
    assert rows[2]["compared"] is False
    assert cli.main(["calls", str(record), "--trend"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("| Game | To the bar: calls | runs | minutes")
    assert out[2] == "| First (Japan) | 4 | 3 | 45.0 | 4.0 | 31, 3 | 1 |"
    assert out[3] == "| Old | 2 | 1 | 10.0 | 2.0 | not in the record | 1 |"
    assert out[4].startswith("| Begun (no comparison by scenes yet) | 2 | 1 | 2.5 |")


def test_a_comparisons_outcome_is_kept_with_its_call():
    assert mcp._outcome("compare", {"content": [{"type": "text", "text": json.dumps(
        {"script": {}, "bar": {"met": True, "screens with text": 40, "scenes": {"a": {}, "b": {}, "c": {}}}})}]}) == \
        {"bar": True, "screens": 40, "scenes": 3}
    assert mcp._outcome("compare", {"content": [{"type": "text", "text": json.dumps(
        {"script": {}, "bar": {"met": False, "screens with text": 10, "scenes": {"a": {}}, "re-read offline": 10}})}]}) == \
        {"bar": False, "screens": 10, "scenes": 1, "reread": 10}
    assert mcp._outcome("compare", {"content": [{"type": "text", "text": json.dumps({"script": {}})}]}) is None
    assert mcp._outcome("run", {"content": [{"type": "text", "text": "{}"}]}) is None
    assert mcp._outcome("compare", {"isError": True, "content": [{"type": "text", "text": "no"}]}) is None


def test_refs_gives_the_words_that_move_along_a_stretch_over_snaps(session):
    """`moving`: a word that points into a script in every snap and at another place from one to the next is
    what follows the game's reading; one that always points at the same place, or only sometimes there, isn't."""
    memory = session.machine.memory
    for i, at in enumerate((0x104, 0x10A, 0x10A, 0x118)):
        struct.pack_into(">I", memory, 0x60, BASE + at)                # goes along the line
        struct.pack_into(">I", memory, 0x68, BASE + 0x100)             # stands at its start
        struct.pack_into(">I", memory, 0x70, BASE + 0x110 if i != 2 else 0)   # there in some moments only
        session.snap(f"mv-{i}")
    names = [f"mv-{i}" for i in range(4)]
    found = session.refs("0x80000100", span=0x20, snaps=names, moving=True)
    assert found["count"] == 1 and found["refs"][0]["at"] == "0x80000060" and found["refs"][0]["places"] == 3
    assert found["refs"][0]["points_at"]["mv-3"] == "0x80000118"
    assert session.refs("0x80000100", span=0x20, snaps=names)["count"] >= 3          # without it: all of them


def test_read_shows_a_script_with_text_among_its_commands():
    from yomikata.harness import search
    data = b"lb01\x00\x22" + b"\x02" + "そらは".encode("cp932") + b"\x03\x04\x02" + "あおい。".encode("cp932") + b"\x03\x0b\x85"
    assert search.mixed(data) == 'lb01<00>"<02>そらは<03><04><02>あおい。<03><0B><85>'
    assert search.mixed(data, marks={7: "start", 9: "AT"}) == 'lb01<00>"<02>[start]そ[AT]らは<03><04><02>あおい。<03><0B><85>'
    assert search.mixed(data, marks={8: "AT"}).startswith('lb01<00>"<02><82>[AT]<BB>らは')     # a mark inside a character
    for name in ("utf8", "utf_8", "UTF-8"):                               # however the encoding is spelt
        assert search.mixed("あ".encode("utf-8") + b"\x01", name) == "あ<01>"
    assert search.mixed(data, "Shift-JIS") == search.mixed(data) == search.mixed(data, "sjis")
    assert search.mixed("あ".encode("euc_jp") + b"\x01", "EUC-JP") == "あ<01>"
    with pytest.raises(LookupError):
        search.mixed(data, "no-such-encoding")


def test_read_takes_mixed_and_marks(session):
    session.machine.memory[0x200:0x20A] = b"\x02" + "ねこ".encode("cp932") + b"\x03\x04ab\x00"
    out = session.read("0x80000200", 10, mixed="shift_jis", marks={"AT": "0x80000203"})
    assert out["mixed"] == "<02>ね[AT]こ<03><04>ab<00>"
    with pytest.raises(HarnessError, match="mixed"):
        session.read("0x80000200", 10, mixed="shift_jis", marks={"AT": "nowhere"})


def test_place_find_and_explore_by_numbers_with_a_point(session):
    """A place kept as three floats, the camera turned so that a push moves two of them: place_find gives the
    three, and explore counts them in cells of `grid` and finds the way round a wall."""
    machine, plain = session.machine, session.machine._step
    struct.pack_into(">3f", machine.memory, 0x300, 10.0, 2.5, -40.0)

    def walked(buttons, stick):
        plain(buttons, stick)
        x, height, z = struct.unpack_from(">3f", machine.memory, 0x300)
        dx = 1.5 * (("right" in buttons) - ("left" in buttons)) - 0.5 * (("down" in buttons) - ("up" in buttons))
        dz = 0.5 * (("right" in buttons) - ("left" in buttons)) + 1.5 * (("down" in buttons) - ("up" in buttons))
        if not (x + dx > 30.0 and z + dz < 0.0):                           # a wall at x = 30, open from z = 0 on
            x, z = x + dx, z + dz
        struct.pack_into(">3f", machine.memory, 0x300, x, height, z)
        struct.pack_into(">f", machine.memory, 0x320, machine.frames * 0.25)   # a clock: moves whatever is pushed
    machine._step = walked
    found = session.place_find(frames=6)
    assert found["groups"][0]["places"][0]["address"] == f"0x{BASE + 0x300:08X}" and len(found["groups"]) == 1
    places = found["groups"][0]["places"]
    assert (places[0]["right"], places[1]["right"], places[2]["right"]) == (9.0, 0.0, 3.0) and places[2]["down"] == 9.0
    struct.pack_into(">3f", machine.memory, 0x300, 10.0, 2.5, -40.0)
    cells = {"x_address": f"0x{BASE + 0x300:08X}", "y_address": f"0x{BASE + 0x308:08X}", "grid": 1.0, "step": 4, "most": 300}
    out = session.explore(**cells, within=[0, -60, 60, 20])
    assert (out["x"], out["y"]) == (10, -40) and out["places"] > 50
    assert not any(row[31 - out["columns"][0]:].strip(".") for row, y in zip(out["map"], range(out["rows"][0], 0)))   # nothing past the wall above z = 0
    out = session.explore(**cells, within=[0, -60, 60, 20], to=[40, 6], near=4)
    assert out["reached"] is True and out["x"] > 30 and out["y"] >= 0     # round the wall's end
    with pytest.raises(HarnessError, match="grid"):
        session.explore(**{**cells, "grid": -1})
    # A passage aslant and narrower than a push: only the slanting pushes get along it.
    def aslant(buttons, stick):
        plain(buttons, stick)
        x, height, z = struct.unpack_from(">3f", machine.memory, 0x300)
        dx, dz = 2.0 * (("right" in buttons) - ("left" in buttons)), 2.0 * (("down" in buttons) - ("up" in buttons))
        if abs((x + dx) - (z + dz)) < 1.0:                                  # the line x = z, and no wider
            struct.pack_into(">3f", machine.memory, 0x300, x + dx, height, z + dz)
    machine._step = aslant
    struct.pack_into(">3f", machine.memory, 0x300, 0.0, 2.5, 0.0)
    assert session.explore(**{**cells, "step": 1}, within=[-10, -10, 10, 10])["places"] == 1
    assert session.explore(**{**cells, "step": 1}, within=[-10, -10, 10, 10], diagonals=True)["places"] == 11


@needs_reader
def test_meet_walks_to_the_nearest_people_and_finds_who_answers(session):
    """A made-up game in three dimensions (no place at exactly 0, as in a real one): an actors' list from 0x400, records of 0x40 bytes (x at +0, height at
    +4, z at +8 as floats), the player's the first; the camera turned so that the stick moves both x and z. Of
    the two people, the nearer says nothing; the other answers when the player stands within 3 of them and
    presses a. meet lists them nearest first, walks to the first, hears nothing more than without the press, then
    to the second, which answers: the game is left there."""
    machine, plain = session.machine, session.machine._step
    mem = machine.memory
    places = [(3.0, -4.0), (15.0, 1.0), (-17.0, 26.0)]
    for k, (x, z) in enumerate(places):
        struct.pack_into(">3f", mem, 0x400 + 0x40 * k, x, 1.0, z)
    mem[0x100:0x120] = bytes(0x20)

    def walked(buttons, stick):
        plain(buttons, stick)
        m = machine.memory
        x, h, z = struct.unpack_from(">3f", m, 0x400)
        sx, sy = stick
        x, z = x + 0.8 * sx - 0.3 * sy, z + 0.3 * sx + 0.8 * sy
        struct.pack_into(">3f", m, 0x400, x, h, z)
        far = ((x + 17.0) ** 2 + (z - 26.0) ** 2) ** 0.5
        if "a" in buttons and far <= 3.0:
            line = "おはなし".encode("cp932")
            m[0x100:0x120] = line + bytes(0x20 - len(line))
    machine._step = walked
    player = {"x_address": f"0x{BASE + 0x400:X}", "z_address": f"0x{BASE + 0x408:X}"}
    listed = session.meet(**player, near=100, look=True)
    assert listed["stride"] == 0x40 and [(t["x"], t["z"]) for t in listed["things"]] == [(15.0, 1.0), (-17.0, 26.0)]
    assert session.script_try(SCRIPT, "GXXJ01.lua")["loaded"]
    out = session.meet(**player, near=100, reach=2.0, keep="met-k")
    assert out["met"]["x"] == -17.0 and out["said"] == ["おはなし"] and out["kept"] == "met-k", out
    assert out["tried"][0]["said"] == 0 and out["tried"][0]["reached"]
    x, _, z = struct.unpack_from(">3f", machine.memory, 0x400)
    assert ((x + 17) ** 2 + (z - 26) ** 2) ** 0.5 <= 3.0                          # left where it was answered
    with pytest.raises(HarnessError, match="hex"):
        session.meet(x_address="nowhere", z_address="0x0")
    # Actors in no list (here, one moved out of it): loose finds every place with the player's spacing; least
    # leaves out what stands nearer than it (a sword the player carries).
    struct.pack_into(">3f", machine.memory, 0x400, 3.0, 1.0, -4.0)
    struct.pack_into(">3f", machine.memory, 0x9A4, 40.0, 1.0, 7.0)
    struct.pack_into(">3f", machine.memory, 0x700, 3.5, 1.0, -4.5)          # the sword
    loose = session.meet(**player, near=100, look=True, loose=True, least=2.0)
    assert (40.0, 7.0) in [(t["x"], t["z"]) for t in loose["things"]] and loose["stride"] is None
    assert (3.5, -4.5) not in [(t["x"], t["z"]) for t in loose["things"]]


def test_explore_with_the_stick_in_three_dimensions(session):
    """A game that walks by the stick alone (the pad does nothing), the camera turned: explore with stick maps by
    pushing the stick eight ways, finds the way round a wall, and a door (the place leaps) found so is listed by
    the stick's way that went through it."""
    machine, plain = session.machine, session.machine._step
    struct.pack_into(">3f", machine.memory, 0x300, 10.0, 2.5, -40.0)

    def walked(buttons, stick):
        plain(buttons, stick)
        x, h, z = struct.unpack_from(">3f", machine.memory, 0x300)
        sx, sy = stick
        nx, nz = x + 1.5 * sx - 0.5 * sy, z + 0.5 * sx + 1.5 * sy
        if nx > 30.0 and nz < 0.0:                       # a wall at x = 30, open from z = 0 on
            nx, nz = x, z
        if nx < 2.0 and nz < -38.0:                      # a door at the far left: the place leaps to another
            nx, nz = 500.0, 500.0
        struct.pack_into(">3f", machine.memory, 0x300, nx, h, nz)
    machine._step = walked
    cells = {"x_address": f"0x{BASE + 0x300:08X}", "y_address": f"0x{BASE + 0x308:08X}", "grid": 2.0, "step": 4,
             "most": 400, "stick": True}
    out = session.explore(**cells, within=[-5, -40, 40, 10])
    assert out["places"] > 50 and out["doors"], out.get("doors")
    assert all(d["push"] in ("n", "ne", "e", "se", "s", "sw", "w", "nw") for d in out["doors"])
    struct.pack_into(">3f", machine.memory, 0x300, 10.0, 2.5, -40.0)
    there = session.explore(**cells, within=[-5, -40, 40, 10], to=[19, 3], near=1)
    assert there["reached"] is True                       # round the wall's end, by the stick


def test_place_find_and_explore_by_whole_numbers(session):
    """A console without floats: the place is three whole numbers, many units a frame, the camera turned.
    place_find with a kind gives them and leaves out a number that leaps; explore counts them in cells of
    `grid` with `whole`, and with `to` stops trying once it is there."""
    machine, plain = session.machine, session.machine._step
    struct.pack_into(">3i", machine.memory, 0x300, 160, 0, -640)

    def walked(buttons, stick):
        plain(buttons, stick)
        x, height, z = struct.unpack_from(">3i", machine.memory, 0x300)
        dx = 6 * (("right" in buttons) - ("left" in buttons)) - 6 * (("down" in buttons) - ("up" in buttons))
        dz = 6 * (("right" in buttons) - ("left" in buttons)) + 6 * (("down" in buttons) - ("up" in buttons))
        if not (x + dx > 480 and z + dz < 0):                              # a wall at x = 480, open from z = 0 on
            x, z = x + dx, z + dz
        struct.pack_into(">3i", machine.memory, 0x300, x, height, z)
        struct.pack_into(">i", machine.memory, 0x340, 40000 * (("right" in buttons) - ("left" in buttons)))   # leaps
    machine._step = walked
    found = session.place_find(frames=6, kind="s32")
    assert [g["places"][0]["address"] for g in found["groups"]] == [f"0x{BASE + 0x300:08X}"]
    assert [p["right"] for p in found["groups"][0]["places"]] == [36.0, 0.0, 36.0] and "numbers_that_followed" in found
    with pytest.raises(HarnessError, match="kind"):
        session.place_find(kind="u8")
    struct.pack_into(">3i", machine.memory, 0x300, 160, 0, -640)
    cells = {"x_address": f"0x{BASE + 0x300:08X}", "y_address": f"0x{BASE + 0x308:08X}", "size": 4, "grid": 16,
             "whole": True, "step": 2, "most": 400}
    out = session.explore(**cells, within=[0, -60, 60, 20])
    assert (out["x"], out["y"]) == (10, -40) and out["places"] > 50
    near = session.explore(**cells, within=[0, -60, 60, 20], to=[12, -38], near=1)
    assert near["reached"] is True and near["places"] < out["places"] // 2     # it stopped once it was there


def test_explore_with_grid_on_a_console_of_whole_numbers_asks_for_whole(session):
    """A Famicom game keeps its place as whole numbers (Mother: x at 0x18, y at 0x1A, 64 a push): grid without
    whole would read them as numbers with a point, so explore says to give whole; where the place can't be read
    as such numbers, it says why."""
    machine = session.machine
    struct.pack_into(">3i", machine.memory, 0x300, 160, 0, -640)
    cells = {"x_address": f"0x{BASE + 0x300:08X}", "y_address": f"0x{BASE + 0x308:08X}", "size": 4, "grid": 16}
    machine.platform = "fc"
    try:
        with pytest.raises(HarnessError, match="give whole as well"):
            session.explore(**cells)
    finally:
        machine.platform = "ngc"
    struct.pack_into("<I", machine.memory, 0x300, 0x7FC00000)                  # not a number, read with a point
    with pytest.raises(HarnessError, match="for whole numbers give whole"):
        session.explore(**cells)


def test_explore_gives_the_same_map_each_time_and_a_ruler_for_its_columns(session):
    """Two runs from one state agree, place for place. And the map comes with a ruler: a mark at every fifth
    column with its number, so that a place isn't read a column's offset out."""
    machine, plain = session.machine, session.machine._step
    struct.pack_into(">2H", machine.memory, 0x300, 103, 100)

    def walked(buttons, stick):
        plain(buttons, stick)
        x, y = struct.unpack_from(">2H", machine.memory, 0x300)
        nx = x + ("right" in buttons) - ("left" in buttons)
        ny = y + ("down" in buttons) - ("up" in buttons)
        if 97 <= nx <= 112 and 98 <= ny <= 101 and not (nx == 106 and ny < 101):
            struct.pack_into(">2H", machine.memory, 0x300, nx, ny)
    machine._step = walked
    cells = {"x_address": f"0x{BASE + 0x300:08X}", "y_address": f"0x{BASE + 0x302:08X}", "size": 2, "step": 1}
    first, again = session.explore(**cells), session.explore(**cells)
    assert first == again and first["columns"] == [97, 112] and first["rows"] == [98, 101]
    assert first["ruler"] == "   |100 |105 |110"
    assert first["map"][0] == "#########.######" and first["map"][2][103 - 97] == "@"
    at = first["ruler"].index("|105")                                      # the wall's column, by the ruler
    assert first["map"][0][at + 1] == "." and 97 + at + 1 == 106


def test_explore_goes_towards_the_place_it_is_asked_to_walk_to(session):
    """An open field and a place 40 cells off: trying evenly outwards would know some thousands of places
    before it got there, more than `most` allows; trying the nearest known place first gets there in a
    few dozen, and round a wall that stands in the straight way."""
    machine, plain = session.machine, session.machine._step
    struct.pack_into(">2H", machine.memory, 0x300, 100, 100)

    def walked(buttons, stick):
        plain(buttons, stick)
        x, y = struct.unpack_from(">2H", machine.memory, 0x300)
        nx = x + ("right" in buttons) - ("left" in buttons)
        ny = y + ("down" in buttons) - ("up" in buttons)
        if not (nx == 120 and 90 <= ny <= 110) and 20 <= nx <= 200 and 20 <= ny <= 200:     # a wall across the way
            struct.pack_into(">2H", machine.memory, 0x300, nx, ny)
    machine._step = walked
    cells = {"x_address": f"0x{BASE + 0x300:08X}", "y_address": f"0x{BASE + 0x302:08X}", "size": 2, "step": 1}
    out = session.explore(**cells, most=400, to=[140, 100], near=0)
    assert out["reached"] is True and (out["x"], out["y"]) == (140, 100)
    assert out["places"] < 400 and out["pushes"] >= 40 + 2 * 11                # round the wall's end, not through it
    struct.pack_into(">2H", machine.memory, 0x300, 100, 100)
    assert session.explore(**cells, most=400)["places"] == 400                 # evenly, the field is more than that


def test_press_at_presses_when_a_number_comes_to_a_value_and_tries_find_which(session):
    """A hand that goes round, and a mark from 120 to 150: a press with the hand over it scores, any other
    stops the hand. `tries` finds the mark by what the game does after each press; `at` then presses for good."""
    machine, plain = session.machine, session.machine._step
    struct.pack_into(">ffI", machine.memory, 0x340, 0.0, 1.0, 0)            # the hand, whether it turns, the score

    def ring(buttons, stick):
        plain(buttons, stick)
        hand, turning, score = struct.unpack_from(">ffI", machine.memory, 0x340)
        if turning:
            if "a" in buttons and not machine.memory[0x34F]:
                if 120.0 <= hand <= 150.0:
                    score += 1
                else:
                    turning = 0.0
            hand += 7.5 * turning
        machine.memory[0x34F] = 1 if "a" in buttons else 0                 # a press counts once, however long it is held
        struct.pack_into(">ffI", machine.memory, 0x340, hand, turning, score)
        machine.memory[0x34F] = 1 if "a" in buttons else 0
    machine._step = ring
    hand, score = f"0x{BASE + 0x340:08X}", f"0x{BASE + 0x348:08X}"
    before, frames = machine.save_state(), machine.frames
    out = session.press_at(hand, tries=[[60], [100], [125], [140], [170]], after=10)
    assert [row["pressed"] for row in out["rows"]] == [1] * 5
    turning = [row["value"] > row["at"][0] + 60 for row in out["rows"]]     # the hand went on only after a press in time
    assert turning == [False, False, True, True, False]
    assert (machine.save_state(), machine.frames) == (before, frames)       # the game was put back
    out = session.press_at(hand, at=[130], after=4, watch=[score], kind="float")
    assert out["presses"][0]["pressed"] and 130 <= out["presses"][0]["value"] < 137.5 and machine.frames > frames
    assert struct.unpack_from(">I", machine.memory, 0x348)[0] == 1
    out = session.press_at(score, at=[5], kind="u32", limit=20)             # a value it never comes to
    assert out["presses"] == [{"at": 5.0, "pressed": False, "waited": 20}]
    for bad in ({"at": [1], "tries": [[1]]}, {}, {"at": [1], "kind": "double"}, {"tries": [[]]}):
        with pytest.raises(HarnessError):
            session.press_at(hand, **bad)
    # A hand that goes round and starts past its mark: with `wrap` it is waited for the next time round.
    def round_and_round(buttons, stick):
        plain(buttons, stick)
        hand, _, score = struct.unpack_from(">ffI", machine.memory, 0x340)
        if "a" in buttons and 120.0 <= hand <= 150.0:
            score += 1
        struct.pack_into(">ffI", machine.memory, 0x340, (hand + 7.5) % 360.0, 1.0, score)
    machine._step = round_and_round
    struct.pack_into(">ffI", machine.memory, 0x340, 300.0, 1.0, 0)
    out = session.press_at(hand, at=[130], wrap=360, hold=1, after=0)
    assert out["presses"][0]["pressed"] and 130 <= out["presses"][0]["value"] < 137.5
    assert struct.unpack_from(">I", machine.memory, 0x348)[0] == 1
    struct.pack_into(">ffI", machine.memory, 0x340, 300.0, 1.0, 0)
    out = session.press_at(hand, at=[130], hold=1, after=0)                 # without it: taken as falling, pressed at once
    assert out["presses"][0]["value"] >= 300 or out["presses"][0]["value"] < 120


def test_steady_finds_what_goes_by_the_same_amount_from_snap_to_snap(session):
    machine, plain = session.machine, session.machine._step

    def turning(buttons, stick):
        plain(buttons, stick)
        struct.pack_into(">f", machine.memory, 0x360, machine.frames * 4.5)            # a hand
        struct.pack_into(">f", machine.memory, 0x364, float(machine.frames % 7))        # something that jumps about
        struct.pack_into(">H", machine.memory, 0x368, 1000 - 3 * machine.frames)        # a timer counting down
    machine._step = turning
    for i in range(4):
        session.run(5)
        session.snap(f"st-{i}")
    names = [f"st-{i}" for i in range(4)]
    found = session.steady(names, within=[f"0x{BASE + 0x300:08X}", f"0x{BASE + 0x400:08X}"])
    assert [p["address"] for p in found["places"]] == [f"0x{BASE + 0x360:08X}"] and found["places"][0]["step"] == 22.5
    found = session.steady(names, kind="u16", within=[f"0x{BASE + 0x300:08X}", f"0x{BASE + 0x400:08X}"])
    assert [(p["address"], p["step"]) for p in found["places"]] == [(f"0x{BASE + 0x368:08X}", -15.0)]
    with pytest.raises(HarnessError):
        session.steady(names[:2])


def test_find_says_when_what_it_found_is_the_middle_of_a_line(session):
    """A search for words from inside a line finds them, and says how much text stands before and where it
    begins: where a line was found isn't where its buffer starts."""
    from yomikata.harness import search
    line = "そらはあおく、うみはひろい。".encode("cp932")
    session.machine.memory[0x400:0x400 + len(line) + 2] = b"\x07" + line + b"\x00"
    start = BASE + 0x401
    whole = session.find(text="そらはあおく")["found"]
    assert whole["shift_jis"] == [f"0x{start:08X}"] and "mid_line" not in whole
    inside = session.find(text="うみはひろい")["found"]
    assert inside["shift_jis"] == [f"0x{start + 14:08X}"]
    assert inside["mid_line"] == {f"0x{start + 14:08X}": {"characters_before": 7, "text_from": f"0x{start:08X}"}}
    data = bytes(session.machine.memory)
    assert search.text_before(data, 0x401) == 0 and search.text_before(data, 0x401 + 14) == 7
    assert search.text_before(data, 0x401 + 14, "utf8") == 0
    assert "mid_line" not in session.find(text="らはあおく")["found"]       # one character before: it may be a mark's last byte


def test_snap_lines_takes_a_routes_screens_in_one_call(session, tmp_path):
    """`screens`: each snap's name to its lines, or to false (or no lines) for a screen without text. One call
    writes them all, as a call for each would; a name that is no snap, and nothing is written."""
    for name in ("r-0", "r-1", "r-2"):
        session.run(3)
        session.snap(name)
    meta = lambda name: json.loads((tmp_path / f"scratch/snaps/{name}.json").read_text(encoding="utf-8"))      # noqa: E731
    out = session.snap_lines(screens={"r-0": ["こんにちは", "さようなら"], "r-1": False, "r-2": []})
    assert out == {"snaps": 3, "with_text": 1, "no_text": 2}
    assert meta("r-0")["ocr"] == ["こんにちは", "さようなら"] and "no_text" not in meta("r-0")
    assert meta("r-1")["no_text"] is True and meta("r-1")["ocr"] == [] and meta("r-2")["no_text"] is True
    one = session.snap_lines("r-1", ocr=["またあした"])                       # the old way gives the same file
    assert one == {"snap": "r-1", "lines": 1, "no_text": False} and "no_text" not in meta("r-1")
    with pytest.raises(HarnessError, match="no snap called nowhere"):
        session.snap_lines(screens={"r-0": ["べつの"], "nowhere": ["x"]})
    assert meta("r-0")["ocr"] == ["こんにちは", "さようなら"]                   # nothing of that call was written
    for bad in ({"screens": {}}, {"screens": {"r-0": "こんにちは"}}, {"screens": {"r-0": ["x"]}, "name": "r-0"},
                {"screens": {"r-0": ["x"]}, "no_text": True}, {}):
        with pytest.raises(HarnessError):
            session.snap_lines(**bad)


def test_a_cores_memory_map_is_read_with_its_select_and_disconnect():
    """libretro's rule for where an address is: a LoROM cartridge (bank n's upper half is the file's n-th
    32 KB, and banks from 0x80 on are the same again), a HiROM one, and plain RAM."""
    from yomikata.replay.libretro import locate
    size = 0x180000                                                       # a 1.5 MB cartridge
    lorom = [{"start": 0x7E0000, "len": 0x20000}, {"start": 0x008000, "len": size, "select": 0x408000, "disconnect": 0x8000}]
    assert locate(lorom, 0x7E1234) == (0, 0x1234)
    for bank in (0, 2, 5, 0x21):
        assert locate(lorom, bank * 0x10000 + 0x8000) == (1, bank * 0x8000)
        assert locate(lorom, (bank + 0x80) * 0x10000 + 0x8000) == (1, bank * 0x8000)      # the mirror from 0x80 on
        assert locate(lorom, bank * 0x10000 + 0xFFFF) == (1, bank * 0x8000 + 0x7FFF)
    assert locate(lorom, 0x020000) is None                                # a bank's lower half isn't cartridge
    assert locate(lorom, 0x308000) == (1, 0x80000)                        # past the cartridge's end: its highest bit is dropped
    hirom = [{"start": 0xC00000, "len": 0x300000, "select": 0xC00000}]
    assert locate(hirom, 0xC00000) == (0, 0) and locate(hirom, 0xD0B355) == (0, 0x10B355)
    assert locate(hirom, 0xF00000) == (0, 0x100000)                       # 3 MB wraps past its end
    assert locate([{"start": 0x100, "len": 0x10}], 0x110) is None


def test_advancing_finds_the_pointer_that_walks_while_a_line_types(session):
    """A pointer that goes up a code or two a frame while a line types and stands afterwards; a frame counter
    that never stands; a flag; a number that goes down. Only the first is given."""
    machine, plain = session.machine, session.machine._step
    state = {"at": 0xB355, "typed": 0}

    def typing(buttons, stick):
        plain(buttons, stick)
        if state["typed"] < 9:
            state["at"] += 2 if state["typed"] in (3, 6) else 1         # a kanji is two codes
            state["typed"] += 1
        struct.pack_into(">H", machine.memory, 0x380, 0x02E1 if state["typed"] == 5 and not state.get("been") else state["at"])
        state["been"] = state.get("been") or state["typed"] == 5      # away for one frame, at a word kept elsewhere
        struct.pack_into(">H", machine.memory, 0x384, 0x4000 - machine.frames)   # goes down
        struct.pack_into(">H", machine.memory, 0x388, machine.frames & 1)        # a flag
    machine._step = typing
    names = []
    for i in range(14):
        session.run(1)
        session.snap(f"ty-{i:02d}")
        names.append(f"ty-{i:02d}")
    span = [f"0x{BASE + 0x300:08X}", f"0x{BASE + 0x400:08X}"]
    found = session.advancing(names[:9], rest=names[9:], within=span)
    assert [(p["address"], p["from"], p["to"]) for p in found["places"]] == [(f"0x{BASE + 0x380:08X}", "0xB356", "0xB360")]
    assert session.advancing(names[:9], within=span)["count"] == 1                    # without the rest: still only it
    assert "nothing_advances" not in found
    none = session.advancing(names[:9], rest=names[9:], most=1, within=span)          # a step of two is too much
    assert none["count"] == 0 and "spaces: true" in none["nothing_advances"]          # and it says where to look instead
    with pytest.raises(HarnessError):
        session.advancing(names[:3])


def test_advancing_finds_a_pointer_at_an_odd_address_with_unit_2(session):
    """A Game Boy keeps words at odd addresses (Dragon Quest I & II's text pointer is at 0xC80F): with unit 2
    the odd offsets are looked at as well, and the pointer is given at its own address."""
    machine, plain = session.machine, session.machine._step
    state = {"at": 0x4210, "typed": 0}

    def typing(buttons, stick):
        plain(buttons, stick)
        if state["typed"] < 7:
            state["at"] += 1
            state["typed"] += 1
        struct.pack_into(">H", machine.memory, 0x381, state["at"])
    machine._step = typing
    names = []
    for i in range(12):
        session.run(1)
        session.snap(f"od-{i:02d}")
        names.append(f"od-{i:02d}")
    span = [f"0x{BASE + 0x300:08X}", f"0x{BASE + 0x400:08X}"]
    found = session.advancing(names[:8], rest=names[8:], within=span)
    assert [p["address"] for p in found["places"]] == [f"0x{BASE + 0x381:08X}"]


def test_advancing_looks_in_a_consoles_other_memory_and_takes_slow_typing(session, monkeypatch):
    """A pointer kept outside main memory (a Game Boy Advance's 32 KB in the CPU, a region of each snap) that
    moves once in four snaps: found there with `least`, and not without it."""
    from yomikata.harness import machine as machines
    monkeypatch.setitem(machines.OTHER_STRETCHES, session.machine.platform, {"iwram": 0x03000000})
    other = bytearray(0x100)
    monkeypatch.setattr(session.machine, "regions", lambda: {"iwram": bytes(other)})
    names = []
    for i in range(16):
        struct.pack_into(">I", other, 0x74, 0x02012400 + 2 * (min(i, 11) // 4))   # a word of the script every fourth snap, then it stands
        struct.pack_into(">I", other, 0x80, 1000 + 3 * i)                     # a clock
        session.run(1)
        session.snap(f"sl-{i:02d}")
        names.append(f"sl-{i:02d}")
    assert session.advancing(names[:12], rest=names[12:], unit=4)["count"] == 0
    found = session.advancing(names[:12], rest=names[12:], unit=4, least=2)
    assert [(p["address"], p["from"], p["to"]) for p in found["places"]] == [("0x03000074", "0x02012400", "0x02012404")]
    assert "least" in session.advancing(names[:12], rest=names[12:], unit=4)["nothing_advances"]


def test_read_says_when_it_cut_a_size_past_the_most(session):
    out = session.read(f"0x{BASE:X}", 5000)
    assert len(bytes.fromhex(out["hex"])) == 4096 and "5000 bytes asked, 4096 read" in out["cut"]
    assert "cut" not in session.read(f"0x{BASE:X}", 16)


def test_find_says_what_to_try_when_a_text_is_nowhere(session):
    out = session.find(text="どこにもないことば")
    assert out["found"] == {} and "advancing" in out["not_found"] and "find_relative" in out["not_found"]
    assert "not_found" not in session.find(text="こんにちは")


def test_a_game_boy_cartridge_is_read_whole_at_its_own_address(tmp_path):
    """A core shows a Game Boy script only the banks switched in now; the harness gives the cartridge's file whole
    at 0x01000000, and main memory as before."""
    from yomikata.harness.machine import CARTRIDGE, Machine

    class Core:
        maps = []

        class lib:
            @staticmethod
            def retro_get_memory_data(kind):
                return None

            @staticmethod
            def retro_get_memory_size(kind):
                return 0

    rom = bytes(range(256)) * 0x100                                     # 64 KB: four banks
    machine = Machine.__new__(Machine)
    machine.platform, machine.core, machine.cartridge = "gbc", Core(), rom
    assert CARTRIDGE["gbc"] == 0x01000000
    assert machine.read(0x01000000 + 3 * 0x4000 + 0x10, 4) == bytes([0x10, 0x11, 0x12, 0x13])
    assert machine.read(0x01000000 + len(rom) - 2, 8) == bytes([0xFE, 0xFF])               # fewer where it ends
    assert machine.read(0x01000000 + len(rom), 4) == b""
    machine.platform, machine.cartridge = "gba", b""
    assert machine.read(0x01000000, 4) == b""


def test_a_pc_engine_hucard_machine_reads_the_consoles_ram_at_its_own_address():
    """beetle pce_fast maps a HuCard game's 8 KB at 0x1F0000 (and its backup RAM at 0x1EE000) and gives the same
    8 KB as system RAM (seen with a made-up HuCard): main memory is read from 0x1F0000."""
    import ctypes as C
    from yomikata.harness.machine import Machine
    work = (C.c_char * 0x2000)(*([7] * 0x2000))
    work[0x10] = b"\x42"

    class Lib:
        def retro_get_memory_data(self, which):
            return C.addressof(work) if which == 2 else None

        def retro_get_memory_size(self, which):
            return 0x2000 if which == 2 else 0

    class Core:
        lib = Lib()
        maps = [(0x1F0000, 0x2000, C.addressof(work))]

        def read(self, address, size):
            for start, length, ptr in self.maps:
                if start <= address < start + length:
                    return C.string_at(ptr + address - start, min(size, start + length - address))
            return b""

        def ram(self):
            return C.string_at(C.addressof(work), 0x2000)

        def ram_base(self):
            return None
    machine = Machine.__new__(Machine)
    machine.platform, machine.core, machine.cartridge = "pce", Core(), b""
    base, ram = machine.ram()
    assert (base, len(ram), ram[0x10]) == (0x1F0000, 0x2000, 0x42)
    assert machine.read(0x1F0010, 2) == bytes([0x42, 7])


def test_a_famicom_cartridge_and_a_disk_are_read_whole_after_their_headers(tmp_path):
    """fceumm's map has no program ROM: the harness gives a Famicom image after its iNES header (and trainer) at
    0x01000000, the program then the CHR; a Disk System image after its fwNES header; a file without one as is."""
    from yomikata.harness.machine import CARTRIDGE, Machine, cartridge_image

    class Core:
        maps = []

        class lib:
            @staticmethod
            def retro_get_memory_data(kind):
                return None

            @staticmethod
            def retro_get_memory_size(kind):
                return 0

    prg, chr_ = bytes(range(256)) * 0x80, bytes([0xC5]) * 0x2000          # 32 KB program, 8 KB CHR
    header = b"NES\x1a" + bytes([2, 1, 0x40, 0]) + bytes(8)
    trained = b"NES\x1a" + bytes([2, 1, 0x44, 0]) + bytes(8) + bytes([0xEE]) * 512
    assert cartridge_image("fc", header + prg + chr_) == prg + chr_
    assert cartridge_image("fc", trained + prg + chr_) == prg + chr_
    assert cartridge_image("fc", prg + chr_) == prg + chr_                 # no header: as it is
    side = b"\x01*NINTENDO-HVC*" + bytes(65500 - 15)
    assert cartridge_image("fds", b"FDS\x1a\x01" + bytes(11) + side) == side
    assert cartridge_image("fds", side) == side
    assert cartridge_image("gb", header + prg) == header + prg           # a Game Boy's file as it is
    machine = Machine.__new__(Machine)
    machine.platform, machine.core = "fc", Core()
    machine.cartridge = cartridge_image("fc", header + prg + chr_)
    assert CARTRIDGE["fc"] == CARTRIDGE["fds"] == 0x01000000
    assert machine.read(0x01000000 + 3 * 0x2000 + 0x10, 4) == bytes([0x10, 0x11, 0x12, 0x13])     # bank 3
    assert machine.read(0x01000000 + len(prg) - 2, 4) == bytes([0xFE, 0xFF, 0xC5, 0xC5])       # then the CHR
    assert machine.read(0x01000000 + len(prg) + len(chr_), 4) == b""
    machine.platform, machine.cartridge = "fds", cartridge_image("fds", b"FDS\x1a\x01" + bytes(11) + side)
    assert machine.read(0x01000001, 14) == b"*NINTENDO-HVC*"


def test_identify_a_game_boy_cartridge(tmp_path):
    """Game Boy cartridges by their header: a Color one with a game code takes it, an older one its header's CRC-32."""
    import zlib
    from yomikata.identify import identify
    def cart(color, code, old_maker):
        rom = bytearray(0x8000)
        rom[0x104:0x108] = bytes.fromhex("ceed6666")
        rom[0x134:0x13F] = b"MADE UP\0\0\0\0"
        rom[0x13F:0x143] = code
        rom[0x143] = 0xC0 if color else 0x00
        rom[0x14B] = old_maker
        return bytes(rom)
    (tmp_path / "a.gbc").write_bytes(cart(True, b"ABCJ", 0x33))
    (tmp_path / "b.gb").write_bytes(cart(False, b"\0\0\0\0", 0x01))
    a, b = identify(tmp_path / "a.gbc"), identify(tmp_path / "b.gb")
    assert (a.platform, a.id) == ("gbc", "CGB-ABCJ")
    assert a.header_crc32 == f"{zlib.crc32(cart(True, b'ABCJ', 0x33)[0x100:0x150]):08x}"
    assert (b.platform, b.id) == ("gb", f"GB-{b.header_crc32.upper()}")


def test_a_pc_engine_cd_is_read_from_its_data_track_and_named_by_its_boot_block(tmp_path):
    """A PC Engine CD keeps an audio track first, all tracks in one file: the data track is read from the cue's
    INDEX 01 on, and the disc is named by its boot block (the data track's second sector) when it has no file
    system. Its main memory in the harness is the card's RAM and the CD's, one after the other."""
    import zlib
    from yomikata.disc import open_data_track
    from yomikata.identify import identify
    from yomikata.harness.machine import Machine
    sync = b"\x00" + b"\xff" * 10 + b"\x00"
    audio = bytes(2352 * 150)
    boot = bytearray(2048)
    boot[0x20:0x20 + 24] = b"PC Engine CD-ROM SYSTEM\x00"
    data = [b"\x82\xa0" * 1024, bytes(boot), bytes(2048)]
    track = b"".join(sync + bytes([0, 2, n, 1]) + sector + bytes(288) for n, sector in enumerate(data))
    (tmp_path / "disc.bin").write_bytes(audio + track)
    (tmp_path / "disc.cue").write_text('FILE "disc.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
                                       '  TRACK 02 MODE1/2352\n    INDEX 00 00:01:00\n    INDEX 01 00:02:00\n')
    with open_data_track(tmp_path / "disc.cue") as img:
        assert img.sector(0) == data[0] and img.sector(1) == bytes(boot)
    release = identify(tmp_path / "disc.cue")
    assert (release.platform, release.header_crc32) == ("pce-cd", f"{zlib.crc32(bytes(boot)):08x}")
    assert release.id == f"PCECD-{release.header_crc32.upper()}"
    import ctypes as C
    card, cd = (C.c_char * 0x30000)(*([7] * 0x30000)), (C.c_char * 0x10000)(*([9] * 0x10000))
    class Core:
        maps = [(0x1F0000, 0x2000, C.addressof((C.c_char * 0x2000)())), (0xD0000, 0x30000, C.addressof(card)),
                (0x100000, 0x10000, C.addressof(cd))]
    machine = Machine.__new__(Machine)
    machine.platform, machine.core = "pce-cd", Core()
    base, ram = machine.ram()
    assert base == 0xD0000 and len(ram) == 0x40000 and ram[0x2FFFF] == 7 and ram[0x30000] == 9
    # The console's own 8 KB is a region beside it, under a name that isn't the main memory's file in a snap
    # (<snap>.ram.gz): named "ram", it was written over the 256 KB.
    Core.video_ram = lambda self: b""
    assert set(machine.regions()) == {"wram"} and len(machine.regions()["wram"]) == 0x2000
    from yomikata.harness.machine import OTHER_STRETCHES
    assert all("ram" not in names for names in OTHER_STRETCHES.values())


def test_identify_famicom_cartridges_by_their_rom_and_disks_by_their_info_block(tmp_path):
    """A Famicom cartridge is named by its PRG and CHR ROM's CRC-32, whatever header the dump has (iNES, NES
    2.0, a trainer, none, in a zip); a Disk System disk by its maker and name codes, with the disk's number for
    the second of a set, and its revision as the build, with or without the fwNES header."""
    import os, zlib, zipfile
    from yomikata.identify import identify
    rom = os.urandom(0x4000 + 0x2000)
    want = f"FC-{zlib.crc32(rom):08X}"
    variants = {"ines.nes": b"NES\x1a\x01\x01\x00\x00" + bytes(8) + rom,
                "nes2.nes": b"NES\x1a\x01\x01\x10\x08\x00\x00\x07\x00\x00\x00\x00\x01" + rom,
                "trainer.nes": b"NES\x1a\x01\x01\x04\x00" + bytes(8) + os.urandom(512) + rom,
                "bare.nes": rom}
    for name, data in variants.items():
        (tmp_path / name).write_bytes(data)
        assert identify(tmp_path / name).id == want, name
    with zipfile.ZipFile(tmp_path / "game.zip", "w") as z:
        z.writestr("Made Up (Japan).nes", variants["ines.nes"])
    release = identify(tmp_path / "game.zip")
    assert (release.platform, release.id, release.header_crc32) == ("fc", want, want[3:].lower())

    def side(maker, name, revision, number):
        block = (b"\x01*NINTENDO-HVC*" + bytes([maker]) + name + bytes([0x20, revision, 0, number])).ljust(0x38, b"\x00")
        return block + bytes(65500 - 0x38)
    disk = side(0xA4, b"ZQ1 ", 3, 0)
    for name, data in (("plain.fds", disk + disk), ("fwnes.fds", b"FDS\x1a\x02" + bytes(11) + disk + disk)):
        (tmp_path / name).write_bytes(data)
        release = identify(tmp_path / name)
        assert (release.platform, release.id, release.build) == ("fds", "FDS-A4ZQ1", "FDS-A4ZQ1-03"), name
        assert release.header_crc32 == f"{zlib.crc32(disk[:0x38]):08x}"
    (tmp_path / "second.fds").write_bytes(side(0x01, b"A?B ", 0, 1))
    assert identify(tmp_path / "second.fds").id == "FDS-01A-B-D2"
    (tmp_path / "junk.fds").write_bytes(bytes(65500))
    with pytest.raises(ValueError, match="disk-info block"):
        identify(tmp_path / "junk.fds")


def test_identify_mega_drive_cartridges_by_their_product_code_or_crc(tmp_path):
    """A Mega Drive cartridge is named by the serial in its product code (the build adds the revision), in any
    image: plain, an SMD dump (interleaved blocks behind a 512-byte header), a zip, a .bin with the header; one
    whose code holds no serial by "MD-" and its ROM's CRC-32. A .bin without the header is still a disc's."""
    import os, zlib, zipfile
    from yomikata.identify import identify, md_unpack
    rom = bytearray(os.urandom(0x8000))
    rom[0x100:0x110] = b"SEGA MEGA DRIVE "
    rom[0x180:0x18E] = b"GM G-9999   01"
    rom = bytes(rom)
    smd = bytearray(b"\x02\x00\x03" + bytes(5) + b"\xAA\xBB\x06" + bytes(512 - 11))
    for at in range(0, len(rom), 0x4000):
        block = rom[at:at + 0x4000]
        smd += block[1::2] + block[0::2]                                   # odd bytes, then even
    assert md_unpack(bytes(smd)) == rom and md_unpack(rom) == rom
    crc = f"{zlib.crc32(rom):08x}"
    with zipfile.ZipFile(tmp_path / "game.zip", "w") as z:
        z.writestr("Made Up (Japan).md", rom)
    for name, data in (("plain.md", rom), ("plain.gen", rom), ("dump.smd", bytes(smd)), ("game.zip", None),
                       ("cart.bin", rom)):
        if data is not None:
            (tmp_path / name).write_bytes(data)
        release = identify(tmp_path / name)
        assert (release.platform, release.id, release.build, release.header_crc32) == ("md", "G-9999", "G-9999-01", crc), name
    genesis = bytearray(rom)
    genesis[0x100:0x110], genesis[0x180:0x18E] = b"SEGA GENESIS    ", b"GM 00000000-00"
    genesis[0x183:0x18B] = b"        "                                 # no serial
    (tmp_path / "nocode.md").write_bytes(bytes(genesis))
    release = identify(tmp_path / "nocode.md")
    want = f"MD-{zlib.crc32(bytes(genesis)):08X}"
    assert (release.platform, release.id, release.build) == ("md", want, want)
    (tmp_path / "junk.md").write_bytes(bytes(0x400))
    with pytest.raises(ValueError, match="SEGA header"):
        identify(tmp_path / "junk.md")


def test_a_mega_drive_is_read_at_its_own_addresses_in_its_own_byte_order_and_its_cartridge_whole():
    """genesis_plus_gx gives the 64 KB of work RAM as 16-bit words in the host's order and no map (seen with a
    made-up cartridge that wrote 11 22 33 44 at 0xFF0000): the harness reads it at 0xFF0000, big-endian, writes
    it back the same way, and gives the cartridge, an SMD dump put back in order, at 0x01000000."""
    import ctypes as C
    from yomikata.harness.machine import CARTRIDGE, Machine, cartridge_image
    raw = (C.c_char * 0x10000)()
    C.memmove(raw, bytes.fromhex("2211443366558877"), 8)
    C.memmove(C.addressof(raw) + 0xFFFE, bytes.fromhex("b2a1"), 2)

    class Lib:
        def retro_get_memory_data(self, which):
            return C.addressof(raw) if which == 2 else None

        def retro_get_memory_size(self, which):
            return 0x10000 if which == 2 else 0

    class Core:
        lib, maps = Lib(), []

        def video_ram(self):
            return None
    machine = Machine.__new__(Machine)
    machine.platform, machine.core = "md", Core()
    rom = bytearray(0x4000)
    rom[0x100:0x104] = b"SEGA"
    rom = bytes(rom)
    smd = bytes(512) + rom[1::2] + rom[0::2]
    machine.cartridge = cartridge_image("md", smd)
    base, ram = machine.ram()
    assert (base, len(ram)) == (0xFF0000, 0x10000) and ram[:8] == bytes.fromhex("1122334455667788")
    assert machine.read(0xFF0002, 4) == bytes.fromhex("33445566") and machine.read(0xFFFFFE, 2) == bytes.fromhex("a1b2")
    assert machine.write(0xFF0001, b"\x99") == 1 and bytes(raw[0:2]) == bytes.fromhex("9911")
    assert CARTRIDGE["md"] == 0x01000000 and machine.read(0x01000100, 4) == b"SEGA" and machine.cartridge == rom


def test_a_mega_drives_video_memory_from_the_patched_core_is_kept_in_snaps_in_the_consoles_order(session, tmp_path):
    """genesis_plus_gx built with tools/cores/genesis_plus_gx-video-ram.patch gives the VDP's 64 KB as
    RETRO_MEMORY_VIDEO_RAM, written a 16-bit word at a time in the host's order: the harness puts each word's bytes
    back (a tile's first row reads as the console has it), and a snap keeps the 64 KB as its `vram` part, read
    back the same. The official build gives none, and a snap then has no `vram` part."""
    import ctypes as C
    from yomikata.harness.machine import Machine
    from yomikata.run import _regions
    console = bytes(range(256)) * 256                           # the VDP's VRAM as the console has it
    host = bytes(b for k in range(0, len(console), 2) for b in (console[k + 1], console[k]))
    work = (C.c_char * 0x10000)()

    class Lib:
        def retro_get_memory_data(self, which):
            return C.addressof(work) if which == 2 else None

        def retro_get_memory_size(self, which):
            return 0x10000 if which == 2 else 0

    def machine_with(video):
        class Core:
            lib, maps = Lib(), []

            def video_ram(self):
                return video
        m = Machine.__new__(Machine)
        m.platform, m.core = "md", Core()
        return m
    regions = machine_with(host).regions()
    assert len(regions["vram"]) == 0x10000 and regions["vram"] == console
    assert machine_with(None).regions() == {}
    session.machine.regions = machine_with(host).regions
    assert "vram" in session.snap("md-0")["regions"]
    assert _regions(tmp_path / "scratch/snaps/md-0.ram.gz") == {"vram": console}
    session.machine.regions = machine_with(None).regions
    assert session.snap("md-1")["regions"] == [] and _regions(tmp_path / "scratch/snaps/md-1.ram.gz") == {}


def test_a_core_built_into_the_image_is_found_where_the_cores_folder_hasnt_it(tmp_path):
    """The harness image builds patched cores (tools/Dockerfile.harness) into a folder of its own: a core is
    taken from the cores folder first, from the built ones where the folder hasn't its name, and a name that
    climbs out of either is refused."""
    from yomikata.harness.session import Roots
    cores, built = tmp_path / "cores", tmp_path / "built"
    cores.mkdir()
    built.mkdir()
    (cores / "genesis_plus_gx_libretro.so").write_bytes(b"official")
    (built / "genesis_plus_gx_vram_libretro.so").write_bytes(b"patched")
    roots = Roots(tmp_path / "games", cores, tmp_path / "inbox", tmp_path / "scratch", built=built)
    assert roots.core("genesis_plus_gx_libretro.so").read_bytes() == b"official"
    assert roots.core("genesis_plus_gx_vram_libretro.so").read_bytes() == b"patched"
    assert not roots.core("missing_libretro.so").is_file()
    (built / "genesis_plus_gx_libretro.so").write_bytes(b"patched too")
    assert roots.core("genesis_plus_gx_libretro.so").read_bytes() == b"official"      # the folder's comes first
    assert not Roots(tmp_path / "games", cores, tmp_path / "inbox", tmp_path / "scratch").core(
        "genesis_plus_gx_vram_libretro.so").is_file()
    with pytest.raises(HarnessError, match="outside"):
        roots.core("../built/genesis_plus_gx_vram_libretro.so")


def test_a_famicom_machine_reads_its_work_ram_through_pages_and_its_nametables(tmp_path):
    """fceumm maps a Famicom's 2 KB at 0x0000 in two pages, 0x6000-0x7FFF in eight pages of 1 KB, and the two
    nametables at 0x80002000, and gives out no video memory as such. The harness reads main memory and its
    mirrors to 0x1FFF, the work RAM as the region `wram` (8 KB, across its pages) and the nametables as `vram`."""
    import ctypes as C
    from yomikata.harness.machine import Machine, region_starts
    work = (C.c_char * 0x800)(*([3] * 0x800))
    pages = [(C.c_char * 0x400)(*([0x60 + k] * 0x400)) for k in range(8)]
    tables = (C.c_char * 0x800)(*([5] * 0x800))
    keep = (work, pages, tables)

    class Lib:
        def retro_get_memory_data(self, which):
            return C.addressof(work) if which == 2 else None

        def retro_get_memory_size(self, which):
            return 0x800 if which == 2 else 0

    class Core:
        lib = Lib()
        maps = ([(0x0000, 0x400, C.addressof(work)), (0x0400, 0x400, C.addressof(work) + 0x400)]
                + [(0x6000 + 0x400 * k, 0x400, C.addressof(p)) for k, p in enumerate(pages)]
                + [(0x80002000, 0x800, C.addressof(tables))])

        def read(self, address, size):
            for start, length, ptr in self.maps:
                if start <= address < start + length:
                    return C.string_at(ptr + address - start, min(size, start + length - address))
            return b""

        def video_ram(self):
            return None

        def ram(self):
            return C.string_at(C.addressof(work), 0x800)

        def ram_base(self):
            return None
    for platform in ("fc", "fds"):
        machine = Machine.__new__(Machine)
        machine.platform, machine.core, machine.cartridge = platform, Core(), b""
        base, ram = machine.ram()
        assert (base, len(ram)) == (0, 0x800) and machine.read(0x1810, 2) == b"\x03\x03"
        regions = machine.regions()
        assert set(regions) == {"wram", "vram"} and region_starts(platform) == {"wram": 0x6000}
        assert regions["wram"] == b"".join(bytes([0x60 + k]) * 0x400 for k in range(8))
        assert regions["vram"] == b"\x05" * 0x800
    assert keep


def test_identify_a_gamecube_disc_plain_and_compressed(tmp_path):
    """A GameCube disc by its header's first 0x80 bytes, as an ISO and as an RVZ (which keeps them at 0x58)."""
    import zlib
    from yomikata.identify import identify
    head = bytearray(0x80)
    head[:6], head[7], head[0x1C:0x20], head[0x20:0x27] = b"GABJ01", 2, bytes.fromhex("c2339f3d"), b"MADE UP"
    (tmp_path / "a.iso").write_bytes(bytes(head) + bytes(0x1000))
    (tmp_path / "a.rvz").write_bytes(b"RVZ\x01" + bytes(0x54) + bytes(head) + bytes(0x100))
    for name in ("a.iso", "a.rvz"):
        release = identify(tmp_path / name)
        assert (release.platform, release.id, release.build) == ("gc", "GABJ01", "GABJ01-02")
        assert release.header_crc32 == f"{zlib.crc32(bytes(head)):08x}"


def test_identify_saturn_dreamcast_and_hucard_and_a_gamecube_id_of_letters_and_digits_only(tmp_path):
    """Saturn and Dreamcast discs by the header at their first data sector (a disc of a set gets -D and its number);
    a HuCard by its ROM's CRC-32 without a copier header; a GameCube header whose id isn't letters and digits gives
    no id."""
    import zlib
    from yomikata.identify import identify, identify_nintendo_disc
    sync = b"\x00" + b"\xff" * 10 + b"\x00"
    def raw_track(sectors):
        return b"".join(sync + bytes([0, 2, n, 1]) + s.ljust(2048, b"\0") + bytes(288) for n, s in enumerate(sectors))
    saturn = bytearray(0x100)
    saturn[:16], saturn[0x20:0x30], saturn[0x38:0x40] = b"SEGA SEGASATURN ", b"GS-9037   V1.005", b"CD-2/2  "
    (tmp_path / "s.bin").write_bytes(raw_track([bytes(saturn), bytes(2048)]))
    (tmp_path / "s.cue").write_text('FILE "s.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
    r = identify(tmp_path / "s.cue")
    assert (r.platform, r.id, r.build, r.header_crc32) == ("saturn", "GS-9037-D2", "GS-9037-D2-V1.005", f"{zlib.crc32(bytes(saturn)):08x}")
    saturn[0x38:0x40] = b"CD-1/1  "
    (tmp_path / "s.bin").write_bytes(raw_track([bytes(saturn), bytes(2048)]))
    assert identify(tmp_path / "s.cue").id == "GS-9037"
    dc = bytearray(0x100)
    dc[:16], dc[0x20:0x30], dc[0x40:0x50] = b"SEGA SEGAKATANA ", b"055A GD-ROM1/3  ", b"HDR-0152  V1.003"
    (tmp_path / "track01.bin").write_bytes(raw_track([bytes(dc), bytes(2048)]))
    (tmp_path / "d.gdi").write_text("1\n1 0 4 2352 track01.bin 0\n")
    r = identify(tmp_path / "d.gdi")
    assert (r.platform, r.id, r.build) == ("dc", "HDR-0152-D1", "HDR-0152-D1-V1.003")
    rom = bytes(range(256)) * 32                                         # 8 KB
    (tmp_path / "a.pce").write_bytes(rom)
    (tmp_path / "b.pce").write_bytes(bytes(512) + rom)
    for name in ("a.pce", "b.pce"):
        r = identify(tmp_path / name)
        assert (r.platform, r.id) == ("pce", f"PCE-{zlib.crc32(rom):08X}")
    head = bytearray(0x80)
    head[:6], head[0x1C:0x20] = b"GAB J1", bytes.fromhex("c2339f3d")
    assert identify_nintendo_disc(bytes(head)) is None
    head[:6] = b"gabj01"
    assert identify_nintendo_disc(bytes(head)) is None


def test_a_plan_played_outside_the_harness_is_its_calls_in_the_record_and_keeps_to_the_budget(tmp_path):
    """`yomikata play`: each call of a plan is a line in the record (offline_play, with the tool it was and the
    frames), counted against the game's budget like the same call in the harness, and a run is one run; a plan
    whose calls don't fit in what is left of the budget isn't run at all; a plan can't set a budget or measure
    a script."""
    from yomikata.harness import play
    from yomikata.calls import summary
    for name in ("games/ngc", "cores", "inbox", "scratch/record"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/ngc/Made Up (Japan).iso").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    game = "ngc/Made Up (Japan).iso"
    load = ["load", {"game": game, "core": "fake_libretro.so", "platform": "ngc"}]
    plan = [load, ["steps", {"steps": [{"buttons": ["a"], "hold": 4, "wait": 30, "times": 2, "snap": True}]}]]
    (tmp_path / "scratch/record/budgets.json").write_text(json.dumps({game: 5}))
    record_file = tmp_path / "scratch/record/calls.jsonl"
    roots = lambda: Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch")  # noqa: E731
    said = []
    s = Session(roots(), FakeMachine)
    lines = play.run(s, plan, tmp_path / "out", say=said.append)
    s.close()
    assert [e["arguments"] for e in lines] == [{"tool": "load", "call": 0}, {"tool": "steps", "call": 1}]
    assert all(e["tool"] == "offline_play" and e["ok"] for e in lines) and lines[-1]["outcome"]["frames"] == 68
    from yomikata.harness.record import entries
    assert len(said) == 2 and len(entries(record_file.read_text())) == 2                # each call begun and answered
    s = Session(roots(), FakeMachine)
    for bad in ([["budget", {"calls": 9}]], [["compare", {"source": "-- x"}]]):     # refused before anything runs
        with pytest.raises(HarnessError, match="isn't for a plan"):
            play.run(s, [load] + bad, say=said.append)
    with pytest.raises(HarnessError, match="begins with a load"):
        play.run(s, plan[1:], say=said.append)
    assert s.machine is None and len(entries(record_file.read_text())) == 2
    with pytest.raises(HarnessError, match="don't fit"):                               # 2 used + 4 > 5
        play.run(s, plan + plan[1:] * 2, say=said.append)
    assert s.machine is None and len(entries(record_file.read_text())) == 2
    assert len(play.run(s, plan + plan[1:], say=said.append)) == 3                       # 2 + 3 = 5: fits
    s.close()
    rows = summary(record_file)
    assert rows[0]["calls"] == 5 and rows[0]["tools"] == {"offline_play": 5} and rows[0]["sessions"] == 2
    s = Session(roots(), FakeMachine)                                                     # spent: nothing runs
    with pytest.raises(HarnessError, match="don't fit"):
        play.run(s, plan[:1], say=said.append)
    s.close()


def test_the_record_takes_lines_from_two_writers_at_once_whole():
    """Two processes appending to the record at the same time: every line comes out whole."""
    import multiprocessing
    import tempfile
    from yomikata.harness import record
    with tempfile.TemporaryDirectory() as scratch:
        writers = [multiprocessing.Process(target=_append_many, args=(scratch, tag)) for tag in "ab"]
        for w in writers:
            w.start()
        for w in writers:
            w.join()
        lines = record.path(Path(scratch)).read_text(encoding="utf-8").splitlines()
        entries = [json.loads(line) for line in lines]
        assert len(entries) == 400 and all(e["pad"] == e["tool"] * 3000 for e in entries)
        assert record.counts(Path(scratch))[1] == {"g": 400}


def _append_many(scratch, tag):
    from yomikata.harness import record
    for _ in range(200):
        record.append(Path(scratch), {"game": "g", "tool": tag, "pad": tag * 3000})


def test_a_saturn_is_read_at_its_own_addresses_and_in_its_own_byte_order():
    """mednafen_saturn gives the work RAM as one 2 MB block of 16-bit words in the host's order, the low 1 MB
    and then the high 1 MB. Read here at the console's addresses (and past the cache, 0x20000000 on), big-endian;
    the high 1 MB is main memory and the low the region lwram; a write lands in its words."""
    import ctypes as C
    from yomikata.harness.machine import Machine, region_starts, BIG_ENDIAN
    console = bytearray(0x200000)
    console[0x100C00:0x100C10] = b"SEGA SEGASATURN "                       # high RAM 0x06000C00
    console[0x000010:0x000014] = b"\x12\x34\x56\x78"                       # low RAM 0x00200010
    host = bytearray(len(console))
    host[0::2], host[1::2] = console[1::2], console[0::2]                  # words in the host's order
    block = (C.c_char * len(host)).from_buffer(host)

    class Lib:
        def retro_get_memory_data(self, which):
            return C.addressof(block) if which == 2 else None

        def retro_get_memory_size(self, which):
            return len(host) if which == 2 else 0

    class Core:
        maps, lib = [], Lib()

        def video_ram(self):
            return None
    machine = Machine.__new__(Machine)
    machine.platform, machine.core, machine.cartridge = "saturn", Core(), b""
    assert machine.read(0x06000C00, 16) == b"SEGA SEGASATURN " == machine.read(0x26000C00, 16)
    assert machine.read(0x06000C01, 4) == b"EGA " and machine.read(0x00200010, 4) == b"\x12\x34\x56\x78"
    assert machine.read(0x060FFFFE, 8) == b"\0\0" and machine.read(0x00300000, 4) == b""
    base, ram = machine.ram()
    assert base == 0x06000000 and len(ram) == 0x100000 and ram[0xC00:0xC04] == b"SEGA"
    regions = machine.regions()
    assert set(regions) == {"lwram"} and regions["lwram"][0x10:0x14] == b"\x12\x34\x56\x78"
    assert region_starts("saturn") == {"lwram": 0x00200000} and "saturn" in BIG_ENDIAN
    assert machine.write(0x06000C03, b"!?") == 2                           # across two words
    assert machine.read(0x06000C00, 6) == b"SEG!?S" and host[0x100C02:0x100C06] == b"!GS?"


def test_identify_an_n64_cartridge_in_each_byte_order_and_zipped(tmp_path):
    """An N64 cartridge by its header in its own order, whether the dump is .z64, .v64 (16-bit words swapped) or
    .n64 (32-bit words reversed), and inside a zip; a header without a game code is named by its CRC-32."""
    import zipfile
    import zlib
    from yomikata.identify import identify
    head = bytearray(0x1000)
    head[:4], head[0x20:0x34] = bytes.fromhex("80371240"), b"MADE UP GAME        "
    head[0x3B:0x3F], head[0x3F] = b"NXXJ", 1
    crc = f"{zlib.crc32(bytes(head[:0x40])):08x}"
    v64, n64 = bytearray(len(head)), bytearray(len(head))
    v64[0::2], v64[1::2] = head[1::2], head[0::2]
    for k in range(4):
        n64[k::4] = head[3 - k::4]
    (tmp_path / "a.z64").write_bytes(bytes(head))
    (tmp_path / "a.v64").write_bytes(bytes(v64))
    (tmp_path / "a.n64").write_bytes(bytes(n64))
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as z:
        z.writestr("Made Up (Japan).z64", bytes(head))
    for name in ("a.z64", "a.v64", "a.n64", "a.zip"):
        r = identify(tmp_path / name)
        assert (r.platform, r.id, r.build, r.header_crc32) == ("n64", "NUS-NXXJ", "NUS-NXXJ-01", crc), name
    head[0x3B:0x3F] = b"\0\0\0\0"
    (tmp_path / "b.z64").write_bytes(bytes(head))
    r = identify(tmp_path / "b.z64")
    assert r.id == f"N64-{zlib.crc32(bytes(head[:0x40])):08X}" and r.build == r.id + "-01"


def test_an_n64_is_read_at_its_own_addresses_and_in_its_own_byte_order():
    """mupen64plus_next gives the N64's RDRAM as 32-bit words in the host's order. Read here at 0x80000000 on
    (and past the cache, 0xA0000000 on), big-endian; it is main memory; a write lands in its words."""
    import ctypes as C
    from yomikata.harness.machine import Machine, BIG_ENDIAN
    console = bytearray(0x1000)
    console[0x300:0x320] = bytes.fromhex("00000001" "00000000" "b0000000" "00000000" "00000000" "00000000" "00400000" "00000000")
    host = bytearray(len(console))
    for k in range(4):
        host[k::4] = console[3 - k::4]                                     # each word's bytes reversed
    block = (C.c_char * len(host)).from_buffer(host)

    class Lib:
        def retro_get_memory_data(self, which):
            return C.addressof(block) if which == 2 else None

        def retro_get_memory_size(self, which):
            return len(host) if which == 2 else 0

    class Core:
        maps, lib = [], Lib()

        def video_ram(self):
            return None
    machine = Machine.__new__(Machine)
    machine.platform, machine.core, machine.cartridge = "n64", Core(), b""
    assert machine.read(0x80000308, 4) == bytes.fromhex("b0000000") == machine.read(0xA0000308, 4)
    assert machine.read(0x80000318, 4) == bytes.fromhex("00400000") and machine.read(0x80000303, 3) == b"\x01\0\0"
    assert machine.read(0x80000FFE, 8) == b"\0\0" and machine.read(0x00000300, 4) == b""
    base, ram = machine.ram()
    assert base == 0x80000000 and len(ram) == 0x1000 and ram[0x308] == 0xB0 and machine.regions() == {}
    assert machine.write(0x80000302, b"AB") == 2 and machine.write(0x80000307, b"CD") == 2     # across two words
    assert machine.read(0x80000300, 10) == b"\0\0AB\0\0\0C" + b"D\0" and "n64" in BIG_ENDIAN
    assert host[0x300:0x304] == b"BA\0\0" and host[0x304:0x30C] == b"C\0\0\0\0\0\0D"


def test_mupen64plus_is_loaded_with_its_renderer_on_the_processor_unless_told_otherwise(tmp_path):
    """mupen64plus_next is given angrylion (GLideN64 gives black pictures here after a framebuffer effect); an
    option the call gives wins; another core gets nothing it wasn't given."""
    from yomikata.harness.session import CORE_OPTIONS
    for name in ("games/n64", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/n64/Made Up (Japan).z64").write_bytes(b"not a game")
    for core in ("mupen64plus_next_libretro.so", "fake_libretro.so"):
        (tmp_path / "cores" / core).write_bytes(b"not a core")
    seen = []

    def make(core_path, game, platform, system_dir, options):
        seen.append(dict(options))
        return FakeMachine(core_path, game, platform, system_dir, options)
    both = {"mupen64plus-rdp-plugin": "angrylion", "mupen64plus-angrylion-sync": "High"}
    for core, given, wanted in (("mupen64plus_next_libretro.so", None, both),
                                ("mupen64plus_next_libretro.so", {"mupen64plus-rdp-plugin": "gliden64"},
                                 {**both, "mupen64plus-rdp-plugin": "gliden64"}),
                                ("fake_libretro.so", None, {})):
        s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), make)
        s.load("n64/Made Up (Japan).z64", core, "n64", options=given)
        assert seen[-1] == wanted and s.loaded[3] == wanted
        s.close()
    assert CORE_OPTIONS["mupen64plus_next_libretro.so"] == both


class Walker(FakeMachine):
    """A character on the ground at x, z (big-endian floats at 0x1000 and 0x1008 past BASE): the stick moves it
    through a camera turned by `angle`, which turns a little each frame the character walks; x stops at `wall`."""
    angle, turn, speed, wall = 0.6, 0.002, 3.0, None

    def __init__(self, *a):
        super().__init__(*a)
        self.x, self.z = 100.0, -50.0
        self.memory.extend(bytes(0x2000))

    def run(self, frames, buttons, stick):
        import math
        for _ in range(frames):
            sx, sy = stick
            if sx or sy:
                c, s = math.cos(self.angle), math.sin(self.angle)
                self.x += self.speed * (c * sx - s * sy)
                self.z += self.speed * (s * sx + c * sy)
                if self.wall is not None:
                    self.x = min(self.x, self.wall)
                self.angle += self.turn
        super().run(frames, buttons, stick)
        struct.pack_into(">ff", self.memory, 0x1000, self.x, 0.0)
        struct.pack_into(">f", self.memory, 0x1008, self.z)


def test_walk_3d_learns_the_camera_follows_it_turning_and_stops_at_a_wall(tmp_path):
    """walk_3d learns which way the stick moves the place (the camera turned), steers there, follows the camera
    as it turns, and stops, saying so, when a push no longer brings it nearer."""
    for name in ("games/wii", "cores", "inbox", "scratch"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "games/wii/Made Up (Japan).iso").write_bytes(b"not a game")
    (tmp_path / "cores/fake_libretro.so").write_bytes(b"not a core")
    s = Session(Roots(tmp_path / "games", tmp_path / "cores", tmp_path / "inbox", tmp_path / "scratch"), Walker)
    s.load("wii/Made Up (Japan).iso", "fake_libretro.so", "wii")
    xa, za = f"0x{BASE + 0x1000:X}", f"0x{BASE + 0x1008:X}"
    s.machine.run(1, set(), (0.0, 0.0))
    assert s.walk_3d(xa, za)["at"] == [100.0, -50.0]
    out = s.walk_3d(xa, za, x=400.0, z=250.0)
    assert out["reached"] and abs(s.machine.x - 400) <= 12 and abs(s.machine.z - 250) <= 12, out
    assert s.machine.angle > 0.7                                          # the camera turned on the way
    s.machine.wall = 450.0
    out = s.walk_3d(xa, za, x=900.0, z=250.0)
    assert not out["reached"] and "nearer" in out["stopped"] and s.machine.x == 450.0, out
    with pytest.raises(HarnessError):
        s.walk_3d("0xZZ", za, x=1.0, z=1.0)
    still = s.walk_3d(f"0x{BASE + 0x1004:X}", za, x=1.0, z=1.0)           # the height: a push moves only z
    assert not still["reached"] and "one line" in still["stopped"], still
    s.close()


def test_a_dreamcast_is_read_at_0x8c000000_and_its_mirrors():
    """flycast gives the Dreamcast's 16 MB of main memory and no map of it: read at 0x8C000000, and the same at
    0x0C000000 and 0xAC000000, as the console has it (little-endian, no swapping); main memory starts there."""
    import ctypes as C
    from yomikata.harness.machine import Machine
    host = bytearray(0x1000)
    host[0x10:0x18] = b"SEGA KAT"
    block = (C.c_char * len(host)).from_buffer(host)

    class Lib:
        def retro_get_memory_data(self, which):
            return C.addressof(block) if which == 2 else None

        def retro_get_memory_size(self, which):
            return len(host) if which == 2 else 0

    class Core:
        maps, lib = [], Lib()

        def ram_base(self):
            return None

        def ram(self):
            return bytes(host)
    machine = Machine.__new__(Machine)
    machine.platform, machine.core, machine.cartridge = "dc", Core(), b""
    assert machine.read(0x8C000010, 8) == machine.read(0x0C000010, 8) == machine.read(0xAC000010, 8) == b"SEGA KAT"
    assert machine.ram()[0] == 0x8C000000 and machine.read(0x8D000000, 4) == b""
