"""The reader runtime through its Python binding, on made-up memory (docs/script-api.md).

Needs the library built from runtime/ (its README). Without it these tests are skipped, unless
YOMIKATA_REQUIRE_READER is set, as CI sets it."""

import gzip
import json
import os
import struct

import pytest

from yomikata import reader as rd
from yomikata.cli import main
from yomikata.run import run

try:
    rd.library()
except rd.ReaderUnavailable as e:        # pragma: no cover
    if os.environ.get("YOMIKATA_REQUIRE_READER"):
        raise
    pytest.skip(str(e), allow_module_level=True)

BASE = 0x80000000


def made_up() -> bytearray:
    """4 KB at 0x80000000: numbers at 0x10, a pointer at 0x40 to a Shift-JIS line at 0x100, a UTF-16 one at 0x200."""
    mem = bytearray(0x1000)
    mem[0x10:0x18] = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF])
    mem[0x20:0x24] = struct.pack("<f", 1.5)
    mem[0x24:0x28] = struct.pack(">f", -2.25)
    mem[0x28:0x30] = struct.pack(">d", 0.1)
    struct.pack_into(">I", mem, 0x40, BASE + 0x100)
    mem[0x100:0x100 + 9] = "こんにちは".encode("cp932")[:8] + b"\0"
    mem[0x200:0x206] = "あ犬".encode("utf-16-le") + b"\0\0"
    mem[0xFFE:0x1000] = b"AB"
    return mem


def reader(mem=None, **kw) -> rd.Reader:
    kw.setdefault("platform", "gc")
    kw.setdefault("game_id", "GXXJ01")
    return rd.Reader(rd.memory(BASE, bytes(made_up() if mem is None else mem)), **kw)


def ev(r: rd.Reader, code: str) -> str:
    return r.eval(code)


def test_little_and_big_endian_reads():
    with reader() as r:
        assert ev(r, "mem.u8(0x80000010)") == "1"
        assert ev(r, "mem.u16(0x80000010)") == str(0x2301)
        assert ev(r, "mem.u16be(0x80000010)") == str(0x0123)
        assert ev(r, "mem.u32(0x80000010)") == str(0x67452301)
        assert ev(r, "mem.u32be(0x80000010)") == str(0x01234567)
        assert ev(r, "mem.u64be(0x80000010)") == str(0x0123456789ABCDEF)
        assert ev(r, "string.format('%x', mem.u64(0x80000010))") == "efcdab8967452301"


def test_signed_reads():
    with reader() as r:
        assert ev(r, "mem.s8(0x80000014)") == str(0x89 - 0x100)
        assert ev(r, "mem.s16(0x80000014)") == str(0xAB89 - 0x10000)
        assert ev(r, "mem.s16be(0x80000014)") == str(0x89AB - 0x10000)
        assert ev(r, "mem.s32(0x80000014)") == str(0xEFCDAB89 - (1 << 32))
        assert ev(r, "mem.s32be(0x80000014)") == str(0x89ABCDEF - (1 << 32))
        assert ev(r, "mem.s32be(0x80000010)") == str(0x01234567)


def test_floats():
    with reader() as r:
        assert ev(r, "mem.f32(0x80000020)") == "1.5"
        assert ev(r, "mem.f32be(0x80000024)") == "-2.25"
        assert ev(r, "mem.f64be(0x80000028)") == "0.1"


def test_memory_that_is_not_there():
    with reader() as r:
        assert ev(r, "mem.u8(0x90000000)") == "nil"
        assert ev(r, "mem.u32be(0x80000FFE)") == "nil"        # two of its four bytes are there
        assert ev(r, "mem.u16be(0x80000FFE)") == str(0x4142)
        assert ev(r, "#mem.read(0x80000FFE, 16)") == "2"
        assert ev(r, "#mem.read(0x7FFFFFF0, 16)") == "0"
        assert ev(r, "#mem.cstr(0x80000FFE)") == "2"            # stops where memory ends
        assert ev(r, "mem.u8(-1)") == "nil"                     # the bits are the address


def test_strings_and_text():
    with reader() as r:
        assert ev(r, "text.sjis(mem.cstr(mem.u32be(0x80000040)))") == "こんにち"
        assert ev(r, "#mem.cstr(0x80000100, 3)") == "3"
        assert ev(r, "text.utf16le(mem.wstr(0x80000200))") == "あ犬"
        assert ev(r, "text.len(text.utf16le(mem.wstr(0x80000200)))") == "2"
        assert ev(r, "text.utf16be('\\x30\\x42')") == "あ"
        assert ev(r, "text.eucjp('\\xA4\\xA2')") == "あ"
        assert ev(r, "text.sjis('\\x82')") == "�"
        assert ev(r, "text.hash('abc')") == "e71fa2190541574b"   # 64-bit FNV-1a
        with pytest.raises(rd.ScriptError):
            ev(r, "mem.read(0x80000000, 2 * 1024 * 1024)")


def test_regions():
    vram = bytes(range(256)) * 4
    with reader(regions={"vram": vram}) as r:
        assert ev(r, "mem.region('vram').size") == "1024"
        assert ev(r, "mem.region('vram').u16(2)") == str(0x0302)
        assert ev(r, "mem.region('vram').u16be(2)") == str(0x0203)
        assert ev(r, "#mem.region('vram').read(1020, 16)") == "4"
        assert ev(r, "mem.region('vram').u8(1024)") == "nil"
        assert ev(r, "mem.region('oam')") == "nil"
        assert ev(r, "mem.u8(2)") == "nil"                      # the game's memory, not the region
        with pytest.raises(rd.ScriptError):
            ev(r, "mem.region('../x')")
    with reader() as r:
        assert ev(r, "mem.region('vram')") == "nil"


def test_needs_a_region_the_host_lacks():
    script = "-- reader: no hooks\n-- reader: needs vram\nreader.log('ran')\n"
    with reader() as r:
        with pytest.raises(rd.ScriptError, match="needs vram"):
            r.load(script)
        assert not any(e.get("message") == "ran" for e in r.take("log"))
    with reader(regions={"vram": b"\0" * 16}) as r:
        r.load(script)
        assert any(e.get("message") == "ran" for e in r.take("log"))


def test_read_only():
    with reader() as r:
        for write in ("w8", "w16", "w32", "w64"):
            with pytest.raises(rd.ScriptError, match="read-only"):
                ev(r, f"mem.{write}(0x80000010, 1)")
        with pytest.raises(rd.ScriptError, match="no hooks"):
            ev(r, "reader.hook(0x80003100, function() end)")
        # Furoshiki's actions and progress load and are left alone.
        r.load("-- reader: api 3\nreader.action('Skip the opening', function() mem.w8(0x80000010, 9) end)\n"
               "reader.progress{items = {}, done = function() end, mark = function() end}\n")
        assert ev(r, "mem.u8(0x80000010)") == "1"


def test_sandbox():
    with reader() as r:
        for gone in ("io", "os", "package", "debug", "require", "dofile", "loadfile"):
            assert ev(r, gone) == "nil"
        assert ev(r, "load('\\27Lua')") .startswith("nil")     # no compiled chunks
        assert ev(r, "load('return 1 + 1')()") == "2"
        assert ev(r, "utf8.char(0x3042), ('x'):rep(3), math.max(1, 2), coroutine ~= nil") == "あ\txxx\t2\ttrue"


def test_identity():
    with reader(build_id="0F1E2D", base=0x08804000, platform="psp", game_id="ULJM00000") as r:
        assert ev(r, "reader.game_id, reader.title_id, reader.build_id, reader.base") == \
            f"ULJM00000\tULJM00000\t0F1E2D\t{0x08804000}"
    with reader() as r:
        assert ev(r, "reader.build_id, reader.base, reader.picture()") == "nil\tnil"
        r.picture(640, 480)
        assert ev(r, "reader.picture()") == "640\t480"


EMIT = """-- reader: no hooks
reader.on_frame(function(frame)
    local bytes = mem.cstr(mem.u32be(0x80000040), 64)
    if reader.changed("message", bytes) then
        reader.emit{ text = text.sjis(bytes), speaker = "博士", id = text.hash(bytes), window = "message",
                     meta = { page = 2, last = true, tag = "a" } }
    end
end)
"""


def test_emit_and_changed():
    mem = made_up()
    with reader(mem) as r:
        r.load(EMIT)
        r.take()
        r.frame(10)
        r.frame(11)                                         # nothing changed: nothing emitted
        (event,) = r.take("text")
        assert event == {"seq": 1, "frame": 10, "channel": "dialogue", "speaker": "博士", "text": "こんにち",
                         "ruby": [], "id": event["id"], "meta": {"last": True, "page": 2, "tag": "a"},
                         "window": "message"}
        assert len(event["id"]) == 16
        mem[0x100:0x104] = "さよ".encode("cp932")
        r.read = rd.memory(BASE, bytes(mem))
        r.frame(12)
        (event,) = r.take("text")
        assert (event["seq"], event["frame"], event["text"]) == (2, 12, "さよにち")
        ev(r, "reader.forget('message')")
        r.frame(13)
        assert [e["seq"] for e in r.take("text")] == [3]


def test_emit_fields():
    with reader() as r:
        ev(r, "reader.emit{text = ''}")                     # an empty text emits nothing
        ev(r, "reader.emit{text = 'はい\\nいいえ', channel = 'choice'}")
        ev(r, "reader.emit{text = 'はい\\nいいえ', channel = 'choice'}")
        a, b = r.take("text")
        assert a["channel"] == "choice" and a["id"] == b["id"] and a["seq"] + 1 == b["seq"]
        assert "window" not in a
        ev(r, "local b = reader.builder() b:add('今日は'):add_ruby('晴', 'は'):add('れ') "
              "reader.emit{text = b:text(), ruby = b:ruby()}")
        (event,) = r.take("text")
        assert event["text"] == "今日は晴れ" and event["ruby"] == [[3, 1, "は"]]
        # A span outside the text is dropped with a warning; the line still goes out.
        ev(r, "reader.emit{text = 'あい', ruby = {{0, 1, 'a'}, {1, 5, 'b'}}}")
        events = r.take()
        assert [e["ruby"] for k, e in events if k == "text"] == [[[0, 1, "a"]]]
        assert any(k == "log" and e["level"] == "warning" for k, e in events)
        with pytest.raises(rd.ScriptError):
            ev(r, "reader.emit{text = 'あ', ruby = {{0, 1.5, 'a'}}}")
        with pytest.raises(rd.ScriptError):
            ev(r, "reader.emit{text = {}}")
        ev(r, "reader.emit{text = 'a\\xffb'}")              # not UTF-8: made so
        assert r.take("text")[0]["text"] == "a�b"


def test_layout():
    with reader() as r:
        assert ev(r, "reader.layout('w', {glyphs = {}})") == "false"       # nothing emitted for it
        ev(r, "reader.emit{text = 'あい\\nう', window = 'w'}")
        assert ev(r, "reader.layout('w', {box = {8, 300, 600, 100}, "
                     "glyphs = {{16, 310, 24, 24}, {40, 310, 24, 24}, false, {16, 334.5, 24, 24}, {1, 2, 3, 4}}})") == "true"
        (layout,) = r.take("layout")
        assert layout == {"window": "w", "seq": 1, "space": [640, 480], "box": [8, 300, 600, 100],
                          "glyphs": [[16, 310, 24, 24], [40, 310, 24, 24], None, [16, 334.5, 24, 24]]}
        ev(r, "reader.layout('w', {space = {320, 240}, glyphs = {{0, 0, 8, 8}}})")
        (layout,) = r.take("layout")
        assert layout["space"] == [320, 240] and layout["glyphs"] == [[0, 0, 8, 8], None, None, None]
        ev(r, "reader.layout('w', nil)")
        ev(r, "reader.layout('w', nil)")                                    # closed once
        assert r.take("layout") == [{"window": "w", "seq": 1, "closed": True}]
        with pytest.raises(rd.ScriptError, match="one screen"):
            ev(r, "reader.layout('w', {screen = 'bottom', glyphs = {}})")


def test_layout_screens_and_space():
    with reader(platform="nds") as r:
        ev(r, "reader.emit{text = 'あ', window = 1}")
        ev(r, "reader.layout(1, {screen = 'bottom', glyphs = {{0, 0, 8, 8}}})")
        (layout,) = r.take("layout")
        assert layout["screen"] == 1 and layout["space"] == [256, 192] and layout["window"] == "1"
    with reader(platform=None) as r:
        ev(r, "reader.emit{text = 'あ', window = 'w'}")
        with pytest.raises(rd.ScriptError, match="give space"):
            ev(r, "reader.layout('w', {glyphs = {{0, 0, 8, 8}}})")
        assert ev(r, "reader.layout('w', {space = {640, 448}, glyphs = {{0, 0, 8, 8}}})") == "true"


def test_cursor():
    with reader() as r:
        ev(r, "reader.cursor(-1)")
        ev(r, "reader.cursor(1, 'horizontal')")
        ev(r, "reader.cursor(1, 'horizontal')")             # the same again: said once
        ev(r, "reader.cursor(nil)")
        assert r.take("cursor") == [{"index": -1, "horizontal": False}, {"index": 1, "horizontal": True},
                                    {"closed": True}]


def test_state_loaded_closes_layouts_and_tells_the_script():
    with reader() as r:
        r.load("reader.on_state_loaded(function() reader.forget() reader.log('loaded') end)\n"
               "reader.on_frame(function() if reader.changed('m', 1) then reader.emit{text = 'あ', window = 'w'} "
               "reader.layout('w', {glyphs = {{0, 0, 8, 8}}}) end end)")
        r.frame(1)
        r.take()
        r.state_loaded()
        events = r.take()
        assert events[0] == ("layout", {"window": "w", "seq": 1, "closed": True})
        assert ("log", {"level": "info", "message": "loaded"}) in events
        r.frame(2)
        assert [e["seq"] for e in r.take("text")] == [2]


def test_another_build():
    script = "if mem.u32be(0x80000010) ~= 0x7C0802A6 then error('this script is for another build of the game', 0) end\n" \
             "reader.on_frame(function() reader.emit{text = 'x'} end)"
    with reader() as r:
        with pytest.raises(rd.ScriptError) as e:
            r.load(script)
        assert str(e.value) == "this script is for another build of the game"
        r.frame(1)                                          # not loaded: nothing runs
        assert r.take("text") == []


def test_a_script_for_a_later_runtime_is_refused():
    assert rd.script_level() == 1
    with reader() as r:
        r.load("-- reader: runtime 1\nreader.log('ran')")
        with pytest.raises(rd.ScriptError, match="later reader runtime"):
            r.load("-- reader: no hooks\n-- reader: runtime 2\nreader.log('ran')")
        assert sum(1 for e in r.take("log") if e.get("message") == "ran") == 1


def test_syntax_error():
    with reader() as r:
        with pytest.raises(rd.ScriptError, match="broken.lua:1"):
            r.load("local = 1", "broken.lua")


def test_instruction_limit_in_a_callback():
    with reader(call_instructions=200_000) as r:
        # The script's own pcall can't keep a spent callback going.
        r.load("reader.on_frame(function() pcall(function() while true do end end) reader.emit{text = 'after'} end)")
        r.take()
        r.frame(1)
        events = r.take()
        assert not [e for k, e in events if k == "text"]
        assert any(k == "log" and "instruction budget" in e["message"] for k, e in events)
        # Each call gets the whole limit anew.
        r.load("reader.on_frame(function() for i = 1, 20000 do end reader.emit{text = 'done'} end)")
        for n in range(1, 4):
            r.frame(n)
        assert len(r.take("text")) == 3


def test_instruction_limit_while_loading():
    with reader(load_instructions=300_000) as r:
        with pytest.raises(rd.ScriptError, match="instruction budget"):
            r.load("while true do end")
        r.load("local n = 0 for i = 1, 1000 do n = n + i end")


def test_memory_limit():
    with reader(memory_limit=4 * 1024 * 1024) as r:
        with pytest.raises(rd.ScriptError, match="not enough memory"):
            r.load("local t = {} for i = 1, 1e7 do t[i] = ('x'):rep(1000) .. i end")
        r.load("local t = {} for i = 1, 100 do t[i] = ('x'):rep(1000) .. i end")
        assert ev(r, "1 + 1") == "2"


def test_a_state_that_cannot_start():
    with reader(memory_limit=1024) as r:
        with pytest.raises(rd.ScriptError):
            r.load("reader.log('x')")
        with pytest.raises(rd.ScriptError):
            ev(r, "1")


def test_five_failures_switch_a_callback_off():
    with reader() as r:
        r.load("local n = 0\n"
               "reader.on_frame(function() n = n + 1 if n ~= 3 then error('bad') end end)\n"       # fails 1, 2; ok at 3
               "reader.on_frame(function(frame) reader.emit{text = 'tick ' .. frame} end)\n"
               "function calls() return n end")
        r.take()
        for frame in range(1, 13):
            r.frame(frame)
        events = r.take()
        # Failed twice, worked once (which starts the count again), then failed five times running: 8 calls.
        assert ev(r, "calls()") == "8"
        assert sum(1 for k, e in events if k == "log" and "handler failed" in e["message"]) == 7
        assert sum(1 for k, e in events if k == "log" and "disabled after 5" in e["message"]) == 1
        # The other callback went on.
        assert len([e for k, e in events if k == "text"]) == 12


def test_data_files():
    table = "# made up\n0\tあ\n1\tい\n".encode()
    with reader(data={"charset": table}) as r:
        assert ev(r, "#reader.data('charset')") == str(len(table))
        assert ev(r, "reader.data('tbl')") == "nil"


def test_header():
    source = ("-- reader: title 架空の物語 (A Made-up Story)\n-- reader: reads the message window\n"
              "--reader:   version 1.00\n-- reader: build 0F1E\n-- reader: build 2D3C\n-- reader: no hooks\n"
              "-- reader: no layout the window's place isn't found\n-- reader: needs vram\n\n"
              "-- a comment\nlocal x = 1\n-- reader: api 3\n")
    assert rd.header(source) == [("title", "架空の物語 (A Made-up Story)"), ("reads", "the message window"),
                                 ("version", "1.00"), ("build", "0F1E"), ("build", "2D3C"), ("no hooks", ""),
                                 ("no layout", "the window's place isn't found"), ("needs", "vram")]


STORY = """-- reader: no hooks
local names = { [1] = "博士", [2] = "助手" }
reader.on_frame(function(frame)
    if mem.u8(0x80000300) == 0 then
        if reader.changed("open", false) then reader.layout("message", nil) reader.forget("message") end
        return
    end
    reader.changed("open", true)
    local bytes = mem.cstr(mem.u32be(0x80000040), 64)
    if reader.changed("message", bytes) then
        local line = text.sjis(bytes)
        reader.emit{ text = line, speaker = names[mem.u8(0x80000301)], id = text.hash(bytes), window = "message",
                     meta = { frame = frame, ratio = frame / 4 } }
        local cells = {}
        for i = 1, text.len(line) do cells[i] = { 16 + 24 * (i - 1), 320, 24, 24 } end
        reader.layout("message", { box = { 8, 300, 624, 160 }, glyphs = cells })
    end
    reader.cursor(mem.s8(0x80000302) >= 0 and mem.s8(0x80000302) or nil)
end)
"""


def story_frames() -> list[bytes]:
    """A made-up run: the window opens, three lines pass, a cursor moves, the window closes."""
    frames, mem = [], made_up()
    mem[0x302] = 0xFF
    lines = ["こんにち", "さよなら", "また明日"]
    for step in range(24):
        mem[0x300] = 0 if step < 2 or step > 20 else 1
        mem[0x301] = 1 + (step // 6) % 2
        line = lines[min(step // 7, 2)].encode("cp932")
        mem[0x100:0x100 + len(line) + 1] = line + b"\0"
        mem[0x302] = step % 3 if 10 <= step < 16 else 0xFF
        frames.append(bytes(mem))
    return frames


def play() -> list[tuple[int, bytes]]:
    with reader() as r:
        r.load(STORY, "story.lua")
        for number, mem in enumerate(story_frames(), start=1):
            r.read = rd.memory(BASE, mem)
            r.frame(number)
        return r.raw


def test_two_runs_give_the_same_events():
    first, second = play(), play()
    assert first == second
    texts = [json.loads(line)["text"] for kind, line in first if kind == rd.TEXT]
    assert texts == ["こんにち", "さよなら", "また明日"]
    kinds = {kind for kind, _ in first}
    assert {rd.TEXT, rd.LAYOUT, rd.CURSOR, rd.LOG} <= kinds


def test_nothing_differs_between_two_states():
    code = ("local t, out = {}, {} for i = 1, 40 do t['key' .. i * 7919] = i end "
            "for k in pairs(t) do out[#out + 1] = k end "
            "return table.concat(out, ' '), math.random(1, 1000000), math.random()")
    with reader() as a, reader() as b:
        first = ev(a, code)
        assert first == ev(b, code)
        ev(a, "math.randomseed()")
        ev(b, "math.randomseed()")
        assert ev(a, "math.random(1, 1000000)") == ev(b, "math.random(1, 1000000)")
    with reader() as c:
        assert ev(c, code) == first


def snapshots(tmp_path) -> list:
    paths = []
    for n, mem in enumerate(story_frames()[4:19:7]):       # one moment of each line
        path = tmp_path / f"s{n:03d}.ram.gz"
        path.write_bytes(gzip.compress(mem))
        (tmp_path / f"s{n:03d}.json").write_text(json.dumps({"id": "GXXJ01", "platform": "ngc",
                                                             "base_address": "0x80000000"}))
        paths.append(path)
    return paths


def test_run_over_snapshots(tmp_path):
    script = tmp_path / "GXXJ01.lua"
    script.write_text(STORY, encoding="utf-8")
    paths = snapshots(tmp_path)
    readings = run(script, paths)
    assert [[e["text"] for e in r.texts()] for r in readings] == [["こんにち"], ["さよなら"], ["また明日"]]
    assert all(r.error is None for r in readings)
    # As consecutive moments of one run: one reader, so the sequence numbers go on.
    readings = run(script, paths, sequence=True)
    assert [e["seq"] for r in readings for e in r.texts()] == [1, 2, 3]


def test_run_reports_another_build(tmp_path, capsys):
    script = tmp_path / "GXXJ01.lua"
    script.write_text("error('this script is for another build of the game', 0)", encoding="utf-8")
    paths = snapshots(tmp_path)
    assert main(["run", str(script), str(paths[0])]) == 1
    assert "another build" in capsys.readouterr().err


INTERFACE = """-- reader: no hooks
reader.on_frame(function()
    if reader.changed("menu", mem.u8(0x80000300)) then
        reader.emit{ text = "もちもの", channel = "interface", window = "menu", id = "menu:items" }
        reader.layout("menu", { space = { 640, 480 }, glyphs = { {40, 40, 20, 20}, {60, 40, 20, 20}, {80, 40, 20, 20}, {100, 40, 20, 20} } })
        reader.emit{ text = "こんにちは" }
        reader.emit{ text = "しらないもの", channel = "banner" }
    end
end)
"""


def test_interface_text_is_emitted_and_kept_from_the_story(tmp_path, capsys):
    with reader() as r:
        r.load(INTERFACE)
        r.take()
        r.frame(1)
        events = r.take()
        texts = [e for k, e in events if k == "text"]
        assert [(e["channel"], e["id"] if e["channel"] == "interface" else "") for e in texts] == \
            [("interface", "menu:items"), ("dialogue", ""), ("banner", "")]
        (layout,) = [e for k, e in events if k == "layout"]
        assert layout["window"] == "menu" and layout["seq"] == texts[0]["seq"] and len(layout["glyphs"]) == 4
    script = tmp_path / "GXXJ01.lua"
    script.write_text(INTERFACE, encoding="utf-8")
    paths = snapshots(tmp_path)[:1]
    (reading,) = run(script, paths)
    assert [e["channel"] for e in reading.texts()] == ["interface", "dialogue", "banner"]
    assert [e["text"] for e in reading.story()] == ["こんにちは"]         # the reader's: no menus, nothing unknown
    assert main(["run", "--story", str(script), str(paths[0])]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["channel"] for e in lines if e["kind"] == "text"] == ["dialogue"]


def test_run_command(tmp_path, capsys):
    script = tmp_path / "GXXJ01.lua"
    script.write_text(STORY, encoding="utf-8")
    paths = snapshots(tmp_path)
    assert main(["run", str(script), *map(str, paths)]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(e["snapshot"], e["text"]) for e in lines if e["kind"] == "text"] == \
        [("s000", "こんにち"), ("s001", "さよなら"), ("s002", "また明日")]
    assert not [e for e in lines if e["kind"] == "log"]
