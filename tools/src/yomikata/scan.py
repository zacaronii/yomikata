"""Sorting a release by how its script is stored: runs of Japanese text in standard encodings across the disc's files.

A "run" is at least MIN_RUN characters in a row that decode as Japanese, with a realistic share of kana (script, not
a font table or a list of kanji). The counts per encoding decide the entry's `encoding`; files that look compressed
(high entropy) are counted too, because a game whose script is compressed on disc shows little readable text there
even though it is readable in memory. Streamed XA audio/video sectors are skipped.

Some Shift-JIS games keep their hiragana as single bytes 0xA1-0xDF (half-width katakana, kana.py). Any binary data
is full of those bytes, so they only count where a stretch of a file spells like Japanese with them: voiced marks
at the usual rate, each after a kana that takes one. There a run is read as the hiragana it stands for, and the
share of it is reported, so an entry can carry the cleanup that turns it back."""

from __future__ import annotations

import re
import zlib
from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .disc import ISO9660, open_data_track
from .kana import fold, spelling

MIN_RUN = 8
KANA_SHARE = 0.3
HALFWIDTH_SHARE = 0.3          # of the Shift-JIS prose: above this, the game keeps its hiragana as single bytes
BLOCK = 64 * 1024              # single-byte hiragana is judged over stretches of a file this long, which must hold
MIN_MARKS, MARK_SHARE, MISPLACED = 5, 0.03, 0.1    # this many voiced marks, per kana, and at most these misplaced
MAX_FILE = 64 * 1024 * 1024
STREAM_EXT = {".STR", ".XA", ".XAM", ".XAS", ".STX", ".DA", ".PSS", ".MOV", ".VAG"}

# Runs of Japanese characters only (kana, kanji, punctuation, full-width forms); plain ASCII never starts a match,
# which keeps a scan of a whole disc fast.
SJIS = re.compile(rb"(?:[\x81-\x84\x88-\x9f\xe0-\xea][\x40-\x7e\x80-\xfc]){%d,}" % MIN_RUN)
SJIS_KANA = re.compile(rb"(?:[\x81-\x84\x88-\x9f\xe0-\xea][\x40-\x7e\x80-\xfc]|[\xa1-\xdf]){%d,}" % MIN_RUN)
UTF16 = re.compile(rb"(?:[\x00-\xff][\x30\x4e-\x9f\xff]){%d,}" % MIN_RUN)
UTF8 = re.compile(rb"(?:\xe3[\x80-\x83][\x80-\xbf]|[\xe4-\xe9][\x80-\xbf][\x80-\xbf]|\xef[\xbc-\xbd][\x80-\xbf]){%d,}" % MIN_RUN)


def _kana(text: str) -> int:
    return sum(1 for c in text if "ぁ" <= c <= "ヺ")


def _japanese(text: str) -> int:
    return sum(1 for c in text if "ぁ" <= c <= "ヺ" or "一" <= c <= "鿿" or c in "、。「」！？…ー")


PARTICLES = set("のにはをがてたでとも")


def script_like(text: str) -> bool:
    """Prose, not noise: no character dominates, it isn't a character list in code order (font tables), and the
    common particles turn up about as often as in real Japanese."""
    chars = [c for c in text if not c.isspace()]
    n = len(chars)
    if n < MIN_RUN:
        return False
    top = max(chars.count(c) for c in set(chars))
    if top > 0.25 * n or len(set(chars)) < 0.3 * n:
        return False
    rising = sum(1 for a, b in zip(chars, chars[1:]) if ord(b) > ord(a))
    if rising > 0.8 * (n - 1):
        return False
    return sum(1 for c in chars if c in PARTICLES) >= 0.06 * n


def runs(data: bytes) -> dict[str, tuple[int, str]]:
    """Per encoding: Japanese characters found in script-like runs, and the longest run (for a sample)."""
    return {name: (total, best) for name, (total, best, _) in _runs(data).items()}


def _decoded(pattern: re.Pattern, data: bytes, codec: str, step: int = 1):
    """Each match that decodes: where it starts and ends, and its text."""
    for m in pattern.finditer(data):
        chunk = m.group(0)
        if step == 2 and len(chunk) % 2:
            chunk = chunk[:-1]
        try:
            yield m.start(), m.end(), chunk.decode(codec)
        except UnicodeDecodeError:
            continue


def _single_byte(data: bytes) -> list[tuple[int, int, str, int]]:
    """Shift-JIS runs that keep their hiragana as single bytes: start, end, the text read as hiragana, and how
    many single bytes it had."""
    found = [(at, end, text, spelling(text)) for at, end, text in _decoded(SJIS_KANA, data, "cp932")]
    blocks: dict[int, list[int]] = {}
    for at, _, _, counts in found:
        blocks[at // BLOCK] = [a + b for a, b in zip(blocks.get(at // BLOCK, (0, 0, 0)), counts)]
    spelled = {b for b, (kana, marks, misplaced) in blocks.items()
               if marks >= max(MIN_MARKS, MARK_SHARE * kana) and misplaced <= MISPLACED * marks}
    return [(at, end, fold(text), kana) for at, end, text, (kana, _, misplaced) in found
            if kana and not misplaced and at // BLOCK in spelled]


def _runs(data: bytes) -> dict[str, tuple[int, str, int]]:
    """As `runs`, with how many of the characters were single-byte hiragana (Shift-JIS only)."""
    out = {}
    for name, pattern, codec, step in (("shift_jis", SJIS, "cp932", 1), ("utf16le", UTF16, "utf-16-le", 2),
                                       ("utf8", UTF8, "utf-8", 1)):
        texts = [(at, text, 0) for at, _, text in _decoded(pattern, data, codec, step)]
        if name == "shift_jis" and (single := _single_byte(data)):
            starts = [at for at, *_ in single]

            def inside(at: int) -> bool:            # a two-byte run that one of these already holds
                i = bisect_right(starts, at) - 1
                return i >= 0 and at < single[i][1]
            texts = [t for t in texts if not inside(t[0])] + [(at, text, kana) for at, _, text, kana in single]
        total, best, halfwidth = 0, "", 0
        for _, text, single in texts:
            jp = _japanese(text)
            if jp >= MIN_RUN and _kana(text) >= KANA_SHARE * jp and script_like(text):
                total += jp
                halfwidth += single
                if jp > _japanese(best):
                    best = text
        out[name] = (total, best, halfwidth)
    return out


def compressed(data: bytes) -> bool:
    """High entropy: zlib can't shrink a sample of it."""
    sample = data[: 256 * 1024]
    return len(sample) >= 64 * 1024 and len(zlib.compress(sample, 6)) > 0.95 * len(sample)


@dataclass
class ScanResult:
    chars: dict[str, int] = field(default_factory=dict)
    files_with_text: dict[str, int] = field(default_factory=dict)
    compressed_bytes: int = 0
    scanned_bytes: int = 0
    sample: dict[str, str] = field(default_factory=dict)
    halfwidth_kana: int = 0

    def single_byte_hiragana(self) -> bool:
        """The Shift-JIS script keeps its hiragana as half-width katakana bytes (kana.py)."""
        return self.encoding() == "shift_jis" and self.halfwidth_kana >= HALFWIDTH_SHARE * self.chars["shift_jis"]

    def encoding(self) -> str:
        """The script's encoding if enough of it reads on disc, else compressed / unknown."""
        best = max(self.chars, key=self.chars.get) if self.chars else None
        if best and self.chars[best] >= 2000:
            return best
        if self.compressed_bytes >= 0.3 * max(self.scanned_bytes, 1):
            return "compressed"
        return "unknown"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["encoding"] = self.encoding()
        d["single_byte_hiragana"] = self.single_byte_hiragana()
        return d


def scan_disc(path: Path, scratch: Path | None = None) -> ScanResult:
    res = ScanResult(chars={"shift_jis": 0, "utf16le": 0, "utf8": 0},
                     files_with_text={"shift_jis": 0, "utf16le": 0, "utf8": 0})
    with open_data_track(path, scratch) as img:
        iso = ISO9660(img)
        for name, e in iso.walk():
            if Path(name).suffix.upper() in STREAM_EXT or img.is_form2(e.lba):
                continue
            data = iso.img.read(e.lba, min(e.size, MAX_FILE))
            res.scanned_bytes += len(data)
            if compressed(data):
                res.compressed_bytes += len(data)
            for enc, (n, best, halfwidth) in _runs(data).items():
                res.chars[enc] += n
                res.halfwidth_kana += halfwidth
                if n:
                    res.files_with_text[enc] += 1
                if best and len(best) > len(res.sample.get(enc, "")):
                    res.sample[enc] = best
    return res
