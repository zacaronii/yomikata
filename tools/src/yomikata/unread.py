"""The pictures a script had no reading for, and the dictionary they are read into (docs/harness.md, "A game's
own font").

A script for a game that brings its characters as pictures knows each by a hash of its bytes (lib/glyphs.lua),
and logs a picture its dictionary hasn't got, once: `glyph <HASH> <width>x<height>x<bits> <hex>`. The harness
keeps those lines in scratch as it plays. From them this draws a sheet to read, asks the second reader, and
writes readings into the dictionary. The pictures are game data and stay in scratch; the dictionary is hashes
and characters."""

from __future__ import annotations

import re
from dataclasses import dataclass

LINE = re.compile(r"glyph ([0-9A-Fa-f]{16}) (\d+)x(\d+)x(\d+) ((?:[0-9a-fA-F]{2})+)")


@dataclass(frozen=True)
class Picture:
    width: int
    height: int
    bits: int
    data: bytes

    def pixels(self):
        """The picture as rows of values: a row is a whole number of bytes, the leftmost pixel in the high bits."""
        import numpy as np
        row = (self.width * self.bits + 7) // 8
        bits = np.unpackbits(np.frombuffer(self.data, dtype=np.uint8).reshape(self.height, row), axis=1)
        cells = bits[:, :self.width * self.bits].reshape(self.height, self.width, self.bits)
        return cells.dot(1 << np.arange(self.bits - 1, -1, -1)).astype(np.uint8)


def logged(text: str) -> dict[str, Picture]:
    """The pictures logged in a text (a harness's log, a host's), by hash, in the order they came up. A line
    whose bytes aren't what its size says is left out."""
    out: dict[str, Picture] = {}
    for mark, width, height, bits, data in LINE.findall(text):
        picture = Picture(int(width), int(height), int(bits), bytes.fromhex(data))
        if (0 < picture.bits <= 8 and picture.width and picture.height
                and len(picture.data) == picture.height * ((picture.width * picture.bits + 7) // 8)):
            out.setdefault(mark.upper(), picture)
    return out


def sheet(pictures: dict[str, Picture], columns: int = 16, scale: int = 3):
    """The pictures side by side, strokes white, each under the start of its hash."""
    import numpy as np
    from PIL import Image, ImageDraw
    width = max((p.width for p in pictures.values()), default=1) * scale + 12
    height = max((p.height for p in pictures.values()), default=1) * scale + 16
    width = max(width, 40)
    image = Image.new("L", (columns * width, max(1, (len(pictures) + columns - 1) // columns) * height), 40)
    draw = ImageDraw.Draw(image)
    for i, (mark, picture) in enumerate(pictures.items()):
        x, y = (i % columns) * width, (i // columns) * height
        grey = (picture.pixels().astype(np.uint16) * 255 // ((1 << picture.bits) - 1)).astype(np.uint8)
        image.paste(Image.fromarray(grey).resize((picture.width * scale, picture.height * scale), Image.NEAREST),
                    (x + 4, y + 13))
        draw.text((x + 4, y + 1), mark[:4], fill=255)
    return image


ROW = re.compile(r"glyph-line((?: (?:[0-9A-Fa-f]{16}|_))+)")


def rows(text: str) -> list[list[str | None]]:
    """The rows of text logged with the pictures: each the hashes of its pictures in order, None for a space."""
    out = []
    for marks in ROW.findall(text):
        row = [None if mark == "_" else mark.upper() for mark in marks.split()]
        if row not in out:
            out.append(row)
    return out


def rows_to_read(rows: list[list[str | None]], unread, most: int = 2) -> dict[int, list[str | None]]:
    """The rows that show an unread picture not yet shown in `most` rows before: what to read them in. Each by
    its number among the rows logged, which stays the same as readings are added."""
    shown: dict[str, int] = {}
    out = {}
    for number, row in enumerate(rows):
        new = {mark for mark in row if mark in unread and shown.get(mark, 0) < most}
        if new:
            out[number] = row
            for mark in {mark for mark in row if mark in unread}:
                shown[mark] = shown.get(mark, 0) + 1
    return out


def _strip(row: list[str | None], pictures: dict[str, Picture], height: int):
    """A row's pictures side by side as values from 0 to 255, and where each begins; None for one not logged."""
    import numpy as np
    parts, starts, x = [], [], 0
    for mark in row:
        picture = pictures.get(mark) if mark else None
        if mark and picture is None:
            return None, []
        grey = (np.zeros((height, height // 2), dtype=np.uint8) if picture is None else
                (picture.pixels().astype(np.uint16) * 255 // ((1 << picture.bits) - 1)).astype(np.uint8))
        grey = np.pad(grey, ((0, max(0, height - grey.shape[0])), (0, 0)))[:height]
        parts.append(grey)
        starts.append(x)
        x += grey.shape[1]
    return (np.hstack(parts) if parts else np.zeros((height, 1), dtype=np.uint8)), starts


def row_sheet(rows: dict[int, list[str | None]], pictures: dict[str, Picture], unread, scale: int = 2):
    """Rows of text drawn whole, a row a line under its number, the start of its hash over each picture that
    has no reading."""
    from PIL import Image, ImageDraw
    height = max((p.height for p in pictures.values()), default=1)
    strips = [(number, row, *_strip(row, pictures, height)) for number, row in rows.items()]
    strips = [found for found in strips if found[2] is not None]
    step, left = height * scale + 16, 40
    image = Image.new("L", (left + max((s.shape[1] for _, _, s, _ in strips), default=1) * scale + 8,
                            max(1, len(strips)) * step), 40)
    draw = ImageDraw.Draw(image)
    for i, (number, row, strip, starts) in enumerate(strips):
        draw.text((2, i * step + step // 2), str(number), fill=255)
        image.paste(Image.fromarray(strip).resize((strip.shape[1] * scale, height * scale), Image.NEAREST),
                    (left, i * step + 13))
        for mark, x in zip(row, starts):
            if mark in unread:
                draw.text((left + x * scale, i * step + 1), mark[:4], fill=255)
    return image


def row_readings(rows: list[list[str | None]], read: dict[int, str]) -> dict[str, str]:
    """Rows read whole, each by its number: the row's characters as they stand, spaces left out, a character
    for each picture. Gives each picture's hash and its character; a row read to another length than it has
    pictures, or a picture read as two characters, is an error."""
    out: dict[str, str] = {}
    for number, line in read.items():
        marks = [mark for mark in rows[number] if mark] if 0 <= number < len(rows) else None
        characters = [c for c in line if not c.isspace()]
        if marks is None or len(marks) != len(characters):
            raise ValueError(f"row {number} has {len(marks or [])} pictures, and was read as {len(characters)} characters")
        for mark, character in zip(marks, characters):
            if out.setdefault(mark, character) != character:
                raise ValueError(f"row {number}: {mark[:4]} was read as {out[mark]} and as {character}")
    return out


def second_rows(rows: list[list[str | None]], pictures: dict[str, Picture], reader=None) -> dict[str, list[str]]:
    """The second reader on whole rows: it reads a line of text better than a character alone. Where it gives
    as many characters as the row has pictures, each picture takes its character: a picture to what it was
    taken for, row by row."""
    from PIL import Image
    from . import glyphread
    reader = reader or glyphread.second_reader()
    height = max((p.height for p in pictures.values()), default=1)
    out: dict[str, list[str]] = {}
    for row in rows:
        strip, _ = _strip(row, pictures, height)
        if strip is None:
            continue
        page = Image.fromarray(255 - strip)
        page = page.resize((page.width * 3, page.height * 3), Image.BILINEAR).convert("RGB")
        read = re.sub(r"[．・.]{2,}", "…", "".join(reader(page).split()))
        marks = [mark for mark in row if mark]
        if len(read) == len(marks):
            for mark, character in zip(marks, read):
                out.setdefault(mark, []).append(character)
    return out


def second(pictures: dict[str, Picture], reader=None) -> dict[str, str]:
    """What the second reader takes each picture for (glyphread: manga-ocr, from a scratch environment)."""
    from . import glyphread
    reader = reader or glyphread.second_reader()
    return {mark: reader(glyphread.enlarged(picture.pixels() * 2 >= (1 << picture.bits) - 1, scale=4))
            for mark, picture in pictures.items()}


def add(dictionary: str, readings: dict[str, str], marks: list[str]) -> str:
    """The dictionary's text with readings put in: each given by the start of a hash, which must be the start
    of one hash only among `marks` and those the dictionary has; an empty reading takes the hash out. The
    notes it begins with are kept, and its lines come out in order."""
    head = [line for line in dictionary.splitlines() if line.startswith("#")]
    known = {k.upper(): v for k, v in (line.split("=", 1) for line in dictionary.splitlines()
                                       if "=" in line and not line.startswith("#"))}
    for start, character in readings.items():
        fits = sorted({m for m in list(marks) + list(known) if m.startswith(start.upper())})
        if len(fits) == 1 and character and known.get(fits[0], character) != character and len(start) < 16:
            raise ValueError(f"{start} is {known[fits[0]]} in the dictionary, not {character}: take it out first ({start}=)")
        if len(fits) != 1:
            raise ValueError(f"{start} is the start of {len(fits)} hashes" + (": " + " ".join(fits) if fits else ""))
        if character == "":
            known.pop(fits[0], None)
        elif len(character) != 1 or character.isspace():
            raise ValueError(f"{start}: one character, not {character!r}")
        else:
            known[fits[0]] = character
    return "".join(line + "\n" for line in head) + "".join(f"{k}={v}\n" for k, v in sorted(known.items()))
