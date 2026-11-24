"""The catalog's scripts and lib/ modules, run by the reader runtime on made-up memory. No game's text is here:
the messages below are written for the tests, in the form the scripts' notes describe."""

import os
import struct
from pathlib import Path

import pytest

from yomikata import reader as rd
from yomikata.bundle import BundleError, bundle

try:
    rd.library()
except rd.ReaderUnavailable as e:        # pragma: no cover
    if os.environ.get("YOMIKATA_REQUIRE_READER"):
        raise
    pytest.skip(str(e), allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]


def sparse(chunks: dict[int, bytes]):
    """Memory with a few stretches in it: {address: bytes}."""
    def read(address: int, size: int) -> bytes:
        for start, data in chunks.items():
            if start <= address < start + len(data):
                return data[address - start:address - start + size]
        return b""
    return read


def test_markup_tokens():
    with rd.Reader(sparse({})) as r:
        r.load(bundle_text('local markup = require("lib.markup")\n'
                           'function walk(s) local out = {} for kind, value in markup.tokens(s) do '
                           'out[#out + 1] = kind .. "=" .. value end return table.concat(out, "|") end\n'
                           'function name(s) return markup.name(s) end'))
        assert r.eval('walk("<speed 2>ab<k>\\ncd<p>")') == "tag=speed 2|text=ab|tag=k|text=\ncd|tag=p"
        assert r.eval('walk("ab<wai")') == "text=ab"            # a tag still being written
        assert r.eval('walk("")') == ""
        assert r.eval('name("speed 2")') == "speed\t2"
        assert r.eval('name("k")') == "k\t"


def bundle_text(source: str) -> str:
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "t.lua"
        path.write_text(source, encoding="utf-8")
        return bundle(path, ROOT)


def test_bundle_keeps_the_header_first_and_refuses_what_is_not_in_lib(tmp_path):
    script = tmp_path / "X.lua"
    script.write_text('-- reader: title あ (A)\n-- reader: no hooks\n\n-- notes\nlocal m = require("lib.markup")\n',
                      encoding="utf-8")
    text = bundle(script, ROOT)
    assert text.startswith("-- reader: title あ (A)\n-- reader: no hooks\n\n-- notes\nlocal __loaded")
    assert rd.header(text) == [("title", "あ (A)"), ("no hooks", "")]
    assert text.count('__module["lib.markup"]') == 1
    script.write_text('local m = require("lib.nothing")', encoding="utf-8")
    with pytest.raises(BundleError):
        bundle(script, ROOT)
    script.write_text('local m = require("os")', encoding="utf-8")
    with pytest.raises(BundleError):
        bundle(script, ROOT)
    script.write_text("reader.log('alone')", encoding="utf-8")
    assert bundle(script, ROOT) == "reader.log('alone')"


# ---- games/gc/G8MJ01.lua ----

PM_MESSAGE = 0x80550384
PM_BUFFER = 0x80A12340


def paper_mario(message: str | None, game: bytes = b"G8MJ01", buffer: int = PM_BUFFER):
    chunks = {0x80000000: game + bytes([0, 0]) + bytes(0x14) + bytes.fromhex("C2339F3D"),
              PM_MESSAGE: struct.pack(">I", buffer if message is not None else 0)}
    if message is not None:
        chunks[buffer] = bytes(0x20) + message.encode("cp932") + b"\0"
    return sparse(chunks)


def paper_mario_reader(message):
    r = rd.Reader(paper_mario(message), platform="gc", game_id="G8MJ01")
    r.load(bundle(ROOT / "games/gc/G8MJ01.lua"), "G8MJ01.lua")
    r.take()
    return r


def test_paper_mario_bubble_pages_and_furigana():
    message = ("<dkey><speed 2>やあ、<r>勇者<Rゆうしゃ>よ。\nよく<wait 10>来たな・・・<k><p>\n"
               "さあ、<r>旅<Rたび>に<wpos 1 2 3>出よう。<k>")
    with paper_mario_reader(message) as r:
        r.frame(1)
        r.frame(2)                                          # the same message: said once
        first, second = r.take("text")
        assert (first["text"], first["ruby"]) == ("やあ、勇者よ。\nよく来たな・・・", [[3, 2, "ゆうしゃ"]])
        assert (second["text"], second["ruby"]) == ("さあ、旅に出よう。", [[3, 1, "たび"]])
        assert first["meta"] == {"page": 1, "pages": 2} and second["meta"] == {"page": 2, "pages": 2}
        assert first["id"] != second["id"] and first["id"].endswith("-1")
        # Another message in the same buffer.
        r.read = paper_mario("つぎの<r>話<Rはなし>。<k>")
        r.frame(3)
        assert [e["text"] for e in r.take("text")] == ["つぎの話。"]
        # No bubble: nothing; and the same message coming up again is said again.
        r.read = paper_mario(None)
        r.frame(4)
        assert r.take("text") == []
        r.read = paper_mario("つぎの<r>話<Rはなし>。<k>")
        r.frame(5)
        assert len(r.take("text")) == 1
        assert not [e for e in r.take("log") if e["level"] == "error"]


def test_paper_mario_pointer_that_points_nowhere():
    for buffer in (0x00000001, 0x817FFFF0, 0x90000000):
        with rd.Reader(paper_mario("あ", buffer=buffer), platform="gc", game_id="G8MJ01") as r:
            r.load(bundle(ROOT / "games/gc/G8MJ01.lua"))
            r.take()
            r.frame(1)
            assert r.take() == []


def test_paper_mario_another_game():
    with rd.Reader(paper_mario("あ", game=b"GXXJ01"), platform="gc", game_id="GXXJ01") as r:
        with pytest.raises(rd.ScriptError, match="another build"):
            r.load(bundle(ROOT / "games/gc/G8MJ01.lua"))
