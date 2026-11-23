"""Super Famicom ids from made-up headers (tools/tests/identify-vectors/sfc.json), plain and zipped."""

import json
import zipfile
from pathlib import Path

import pytest

from yomikata.identify import identify

VECTORS = json.loads((Path(__file__).parent / "identify-vectors" / "sfc.json").read_text())


def image(v: dict) -> bytes:
    rom = bytearray(v["size"])
    at = int(v["header_at"], 16)
    rom[at:at + 0x30] = bytes.fromhex(v["header"])
    return bytes(512) * v["copier_header"] + bytes(rom)


@pytest.mark.parametrize("v", VECTORS, ids=lambda v: v["name"])
def test_sfc_id(tmp_path, v):
    path = tmp_path / "game.sfc"
    path.write_bytes(image(v))
    r = identify(path)
    assert (r.platform, r.id, r.header_crc32) == ("sfc", v["id"], v["header_crc32"])


def test_sfc_in_a_zip(tmp_path):
    v = VECTORS[0]
    path = tmp_path / "game.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("notes.txt", "made up")
        z.writestr("game.smc", image(v))
    assert identify(path).id == v["id"]
