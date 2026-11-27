"""`yomikata state-text`: two made-up states, each with a different page on screen; the word that moves with
the page is found in both, and the fixed root that holds it."""

import gzip
import json
import struct

import pytest

from yomikata import statetext
from yomikata.cli import main

BASE = 0x80000000


def state(page: bytes, other: bytes, at: int, obj: int, lead: int) -> bytes:
    """A made-up state: `lead` bytes of the core's own, then RAM (the anchor first): two messages, the page shown
    at `at`; an object at `obj` whose +0x90 points into the page (it types: 2 bytes in); a fixed root at 0x803AE9A0
    pointing at the object; and a stray word that points into the page in this state only."""
    mem = bytearray(0x100000)
    mem[0:6] = b"GXXE01"
    mem[at:at + len(page)] = page
    mem[0x60000:0x60000 + len(other)] = other
    struct.pack_into(">I", mem, obj + 0x90, BASE + at + 2)
    struct.pack_into(">I", mem, 0xAE9A0, BASE + obj)
    struct.pack_into(">I", mem, 0x70000 + (at >> 4) % 0x400 * 4, BASE + at)   # a stray: elsewhere in each
    return gzip.compress(bytes(lead) + bytes(mem))


ONE, TWO = "ほんとうだ".encode("cp932"), "いいえ、ちがう".encode("cp932")


def test_the_word_that_moves_with_the_page_and_its_root(tmp_path):
    a = state(ONE, TWO, 0x50100, 0x40000, 0x1234)
    b = state(TWO, ONE, 0x52200, 0x41000, 0x99)
    pages = [(statetext.ram(statetext.blob(s), b"GXXE01", BASE, 0x100000), t) for s, t in ((a, ONE), (b, TWO))]
    out = statetext.follow(pages)
    assert out["found"] == [1, 1]
    assert [x["slot"] for x in out["slots"]] == []                # the object moved: no one slot in both
    # The object at the same place in both: its +0x90 is the slot, and the fixed root holds it 0x90 before.
    b = state(TWO, ONE, 0x52200, 0x40000, 0x99)
    pages[1] = (statetext.ram(statetext.blob(b), b"GXXE01", BASE, 0x100000), TWO)
    out = statetext.follow(pages)
    assert out["slots"] == [{"slot": "0x80040090", "into": [2, 2]}]
    assert {"root": "0x800AE9A0", "offset": "0x90", "slot": "0x80040090"} in out["roots"]
    (tmp_path / "a.state.gz").write_bytes(a)
    (tmp_path / "b.state.gz").write_bytes(b)
    with pytest.raises(statetext.StateTextError):
        statetext.follow(pages[:1])
    with pytest.raises(statetext.StateTextError):
        statetext.ram(statetext.blob(a), b"NOPE", BASE, 0x100000)


def test_the_command(tmp_path, capsys):
    a = state(ONE, TWO, 0x50100, 0x40000, 0x10)
    b = state(TWO, ONE, 0x52200, 0x40000, 0x20)
    (tmp_path / "a.state.gz").write_bytes(a)
    (tmp_path / "b.state.gz").write_bytes(b)
    assert main(["state-text", "--state", str(tmp_path / "a.state.gz"), "--text", "ほんと",
                 "--state", str(tmp_path / "b.state.gz"), "--text", "hex:" + TWO[:6].hex(),
                 "--anchor", "GXXE01", "--size", "0x100000"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["slots"][0]["slot"] == "0x80040090" and "ほんと" not in json.dumps(out, ensure_ascii=False)
    assert main(["state-text", "--state", str(tmp_path / "a.state.gz"), "--text", "x", "--text", "y",
                 "--anchor", "GXXE01"]) == 1
