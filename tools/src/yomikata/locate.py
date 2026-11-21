"""Finding a game's text in a RAM snapshot when its encoding is unknown (a game's own table).

For each line a reader OCR'd while the snapshot was taken, the line's runs of Japanese characters are searched for by
their **pattern of repeated characters** alone: positions holding the same character must hold the same code,
different characters different codes. That needs no knowledge of the table. It's tried for 1-byte codes and for
2-byte codes in both byte orders and alignments. OCR misreads are tolerated by also trying overlapping windows of a
long run. Matches give code -> character pairs; pairs from all lines and snapshots are combined, and a code read as
two different characters (or a character with two codes) is reported as a conflict, not guessed.

The kana layout is then easy to read off: `gojuon_offsets` shows code minus the character's gojuon position for the
plain kana, which is a single number when a game keeps kana in gojuon order (Human's games do)."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

JP = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fc\u4e00-\u9fff\u3005\u3001\u3002\u300c-\u300f\uff01\uff1f\u2026\u30fb\uff10-\uff19]+")
GOJUON = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
VARIANTS = [("u16le", "<u2", 0), ("u16le", "<u2", 1), ("u16be", ">u2", 0), ("u16be", ">u2", 1), ("u8", "u1", 0)]
MIN_PAIRS = 3          # a window needs at least this many repeated-character pairs to mean anything


def windows(line: str, min_pairs: int = 3, min_len: int = 8, step: int = 1) -> list[tuple[str, int, int]]:
    """(segment, run number, start within the run): from each starting point of each Japanese run, the shortest
    window holding at least `min_pairs` repeated-character pairs. Windows rather than whole runs, so that a misread
    or a control code inside the line only spoils the windows that cross it."""
    out = []
    for r, run in enumerate(JP.findall(line)):
        for i in range(0, len(run), step):
            for j in range(i + min_len, len(run) + 1):
                if pairs(run[i:j]) >= min_pairs:
                    out.append((run[i:j], r, i))
                    break
    return list(dict.fromkeys(out))


def pairs(seg: str) -> int:
    return sum(1 for i in range(len(seg)) for j in range(i + 1, len(seg)) if seg[i] == seg[j])


def codes(ram: bytes, dtype: str, align: int) -> np.ndarray:
    if dtype == "u1":
        return np.frombuffer(ram, dtype=np.uint8).astype(np.int64)
    n = (len(ram) - align) // 2
    return np.frombuffer(ram, dtype=dtype, count=n, offset=align).astype(np.int64)


def isomorph(a: np.ndarray, seg: str) -> np.ndarray:
    n = len(seg)
    L = len(a) - n
    if L <= 0:
        return np.array([], dtype=np.int64)
    m = a[:L] != 0
    for i in range(n):
        for j in range(i + 1, n):
            same = a[i:i + L] == a[j:j + L]
            m &= same if seg[i] == seg[j] else ~same
            if not m.any():
                return np.array([], dtype=np.int64)
    return np.nonzero(m)[0]


@dataclass
class Result:
    encoding: str
    hits: list[dict] = field(default_factory=list)
    mapping: dict[int, str] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)

    def gojuon_offsets(self) -> dict[str, dict[int, int]]:
        out = {"hiragana": Counter(), "katakana": Counter()}
        for code, ch in self.mapping.items():
            if ch in GOJUON:
                out["hiragana"][code - GOJUON.index(ch)] += 1
            elif "ァ" <= ch <= "ヺ" and chr(ord(ch) - 0x60) in GOJUON:
                out["katakana"][code - GOJUON.index(chr(ord(ch) - 0x60))] += 1
        return {k: dict(v.most_common(4)) for k, v in out.items()}


def locate(snapshots: list[tuple[bytes, list[str]]], max_hits_per_window: int = 3) -> list[Result]:
    """Search every snapshot for every OCR line; one Result per encoding variant that found anything."""
    by_enc: dict[str, Result] = {}
    votes: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for ram, lines in snapshots:
        for name, dtype, align in VARIANTS:
            a = codes(ram, dtype, align)
            step = 1 if dtype == "u1" else 2
            for line in lines:
                # A match is trusted when two or more windows agree where their run starts, or a whole run with
                # several repeats matches: random data rarely does either.
                support: dict[tuple[int, int], set] = defaultdict(set)
                found_at: dict[tuple[int, int], list] = defaultdict(list)
                for seg, run, start in windows(line):
                    if pairs(seg) < MIN_PAIRS:
                        continue
                    found = isomorph(a, seg)
                    if not 0 < len(found) <= max_hits_per_window:      # nothing, or too common to trust
                        continue
                    for h in found:
                        key = (run, int(h) - start)
                        support[key].add(-1 if pairs(seg) >= 5 else start)    # a very strong window counts alone
                        found_at[key].append((seg, int(h)))
                for key, starts in support.items():
                    if len(starts) < 2 and -1 not in starts:
                        continue
                    res = by_enc.setdefault(name, Result(name))
                    for seg, h in found_at[key]:
                        cs = [int(x) for x in a[h:h + len(seg)]]
                        res.hits.append({"segment": seg, "offset": int(align + step * h), "codes": cs})
                        for c, ch in zip(cs, seg):
                            votes[name][c][ch] += 1
    for name, res in by_enc.items():
        chars: dict[str, set] = defaultdict(set)
        for c, cnt in votes[name].items():
            ch, n = cnt.most_common(1)[0]
            if len(cnt) > 1:
                res.conflicts.append(f"{c:04X}: " + ", ".join(f"{k}×{v}" for k, v in cnt.most_common()))
            res.mapping[c] = ch
            chars[ch].add(c)
        res.conflicts += [f"{ch}: codes " + ", ".join(f"{c:04X}" for c in sorted(cs)) for ch, cs in chars.items() if len(cs) > 1]
    # The real reading is the one whose kana agree on one gojuon offset; the same bytes read at the neighbouring
    # alignment or the other byte order match just as often but scatter.
    def agreement(r: Result) -> int:
        offs = r.gojuon_offsets()
        return max([0] + [n for d in offs.values() for n in d.values()])
    return sorted(by_enc.values(), key=lambda r: (-agreement(r), len(r.conflicts), -len(r.hits)))
