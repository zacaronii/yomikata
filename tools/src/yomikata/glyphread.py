"""A second reader for a font's glyphs, and the checks that go with it (docs/harness.md).

Reading a small glyph by eye goes wrong a few times in a hundred, and more for rare kanji. A second reader
that makes other mistakes catches most of it: where the two agree the reading has been made two ways, and where
they differ it is read again, in a sentence. The reader here is manga-ocr (the model kha-white/manga-ocr-base,
from Hugging Face): it isn't installed with the tools. Put it in a scratch environment (`pip install manga-ocr`)
and run this with that Python."""

from __future__ import annotations

import json
from pathlib import Path


def pictures(path: Path, rows: int, row_bytes: int) -> dict[str, object]:
    """Glyphs as `glyph_pairs` writes them (a code to its rows as hex, a bit a pixel), as arrays of 0 and 1."""
    import numpy as np
    out = {}
    for code, rows_hex in json.loads(Path(path).read_text(encoding="utf-8")).items():
        data = bytes.fromhex(rows_hex)[:rows * row_bytes]
        out[code] = np.unpackbits(np.frombuffer(data, dtype=np.uint8)).reshape(rows, row_bytes * 8)
    return out


def enlarged(glyph, scale: int = 8, margin: int = 3):
    """Black on white, enlarged, with a margin: what a reader of printed text takes best."""
    import numpy as np
    from PIL import Image
    ink = np.asarray(glyph) != 0
    while ink.shape[1] > 1 and not ink[:, -1].any():        # the empty columns right of a narrow glyph
        ink = ink[:, :-1]
    page = np.pad(np.where(ink, 0, 255).astype(np.uint8), margin, constant_values=255)
    return Image.fromarray(page).resize((page.shape[1] * scale, page.shape[0] * scale), Image.BILINEAR).convert("RGB")


def second_reader():
    try:
        from manga_ocr import MangaOcr
    except ImportError:
        raise RuntimeError("manga-ocr isn't installed in this Python: make a scratch environment, "
                           "`pip install manga-ocr`, and run yomikata from it") from None
    return MangaOcr()


def read(glyphs: dict[str, object], reader=None) -> dict[str, str]:
    """What the second reader takes each glyph for."""
    reader = reader or second_reader()
    return {code: reader(enlarged(glyph)) for code, glyph in glyphs.items()}


def compare(first: dict[str, str], second: dict[str, str]) -> list[dict]:
    """A row for each code: both readings, and whether they agree. A code only one has is `unread`."""
    rows = []
    for code in sorted(set(first) | set(second)):
        mine, other = first.get(code), second.get(code)
        rows.append({"code": code, "first": mine, "second": other,
                     "status": "unread" if mine is None else "agree" if mine == other else "differ"})
    return rows


def sentences(texts: list[str], characters: str, width: int = 8, most: int = 2) -> dict[str, list[str]]:
    """For each character, the shortest stretches of the game's text that hold it, the character in [ ]: what to
    read a doubtful reading in."""
    out: dict[str, list[str]] = {ch: [] for ch in characters}
    for text in texts:
        for ch in out:
            at = text.find(ch)
            if at >= 0 and len(out[ch]) < most:
                piece = (text[max(0, at - width):at] + "[" + ch + "]" + text[at + 1:at + 1 + width]).replace("\n", "/")
                if piece not in out[ch]:
                    out[ch].append(piece)
    return out
