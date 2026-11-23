"""`check` on made-up snapshots: counts, agreement with a reader's lines, the page."""

import gzip
import json

from yomikata.check import agreement, check, page

ENTRY = {"id": "ZQ-0001", "platform": "ps1", "encoding": "shift_jis",
         "layouts": [{"message": {"address": "0x80000100", "charset": "shift_jis", "max_bytes": 64, "terminator": "00"}}]}
PNG = bytes.fromhex("89504e470d0a1a0a")       # only the signature: the page embeds whatever the file holds


def snapshot(folder, name, text, meta):
    ram = bytearray(0x400)
    raw = text.encode("cp932")
    ram[0x100:0x100 + len(raw)] = raw
    (folder / f"{name}.ram.gz").write_bytes(gzip.compress(bytes(ram)))
    if meta is not None:
        (folder / f"{name}.json").write_text(json.dumps({"base_address": "0x80000000", **meta}), encoding="utf-8")
    return folder / f"{name}.ram.gz"


def test_counts_and_page(tmp_path):
    snaps = [snapshot(tmp_path, "a", "つくりもののぎょう", {"ocr": ["つくりものの ぎょう"]}),
             snapshot(tmp_path, "b", "べつのぎょう", {"ocr": ["まったくちがうもの"]}),
             snapshot(tmp_path, "c", "", {"ocr": [], "no_text": True}),
             snapshot(tmp_path, "d", "のこりもの", {"ocr": [], "no_text": True}),
             snapshot(tmp_path, "e", "なにか", None)]
    (tmp_path / "a.screen.png").write_bytes(PNG)
    result = check(ENTRY, snaps, tmp_path, base=0x80000000)
    counts = result.counts()
    assert counts["snapshots"] == 5 and counts["with a message"] == 4
    assert counts["with a reader's lines"] == 2 and counts["of those, agreeing 80% or more"] == 1
    assert counts["marked as showing no text"] == 2 and counts["of those, reading nothing"] == 1
    assert result.rows[4].seen is None and result.rows[4].agreement is None
    html = page(result, "ZQ-0001")
    assert "つくりもののぎょう" in html and "data:image/png;base64," in html and "no lines" in html


def test_agreement_ignores_spaces_and_tolerates_a_misread():
    assert agreement("あいうえお\nかきくけこ", ["あいうえお かきくけこ"]) == 1.0
    assert agreement("あいうえおかきくけこ", ["あいうえおかきくげこ"]) == 0.9
    assert agreement("", []) == 1.0 and agreement("のこり", []) == 0.0


def test_a_choice_counts_as_text_read(tmp_path):
    entry = dict(ENTRY, layouts=[{"choices": {"address": "0x80000100", "charset": "shift_jis", "max_bytes": 16,
                                              "terminator": "00", "rows": {"count": 2, "stride": 16}}}])
    ram = bytearray(0x400)
    for i, answer in enumerate(("はい", "いいえ")):
        raw = answer.encode("cp932")
        ram[0x100 + 16 * i:0x100 + 16 * i + len(raw)] = raw
    (tmp_path / "a.ram.gz").write_bytes(gzip.compress(bytes(ram)))
    (tmp_path / "a.json").write_text(json.dumps({"base_address": "0x80000000", "ocr": ["はい いいえ"]}), encoding="utf-8")
    result = check(entry, [tmp_path / "a.ram.gz"], tmp_path)
    assert result.rows[0].agreement == 1.0 and result.counts()["with a choice's answers"] == 1
