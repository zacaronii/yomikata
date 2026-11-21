"""Sorting a release by how its script is stored: runs of Japanese text in standard encodings across the disc's files.

A "run" is at least MIN_RUN characters in a row that decode as Japanese, with a realistic share of kana (script, not
a font table or a list of kanji). The counts per encoding decide the entry's `encoding`; files that look compressed
(high entropy) are counted too, because a game whose script is compressed on disc shows little readable text there
even though it is readable in memory. Streamed XA audio/video sectors are skipped."""

from __future__ import annotations

import re
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .disc import ISO9660, open_data_track

MIN_RUN = 8
KANA_SHARE = 0.3
MAX_FILE = 64 * 1024 * 1024
STREAM_EXT = {".STR", ".XA", ".XAM", ".XAS", ".STX", ".DA", ".PSS", ".MOV", ".VAG"}

# Runs of Japanese characters only (kana, kanji, punctuation, full-width forms); plain ASCII never starts a match,
# which keeps a scan of a whole disc fast.
SJIS = re.compile(rb"(?:[\x81-\x84\x88-\x9f\xe0-\xea][\x40-\x7e\x80-\xfc]){%d,}" % MIN_RUN)
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
    out = {}
    for name, pattern, codec, step in (("shift_jis", SJIS, "cp932", 1), ("utf16le", UTF16, "utf-16-le", 2),
                                       ("utf8", UTF8, "utf-8", 1)):
        total, best = 0, ""
        for m in pattern.finditer(data):
            chunk = m.group(0)
            if step == 2 and len(chunk) % 2:
                chunk = chunk[:-1]
            try:
                text = chunk.decode(codec)
            except UnicodeDecodeError:
                continue
            jp = _japanese(text)
            if jp >= MIN_RUN and _kana(text) >= KANA_SHARE * jp and script_like(text):
                total += jp
                if jp > _japanese(best):
                    best = text
        out[name] = (total, best)
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
            for enc, (n, best) in runs(data).items():
                res.chars[enc] += n
                if n:
                    res.files_with_text[enc] += 1
                if best and len(best) > len(res.sample.get(enc, "")):
                    res.sample[enc] = best
    return res
