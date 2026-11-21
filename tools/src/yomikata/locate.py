"""Finding a game's text in a RAM snapshot when its encoding is unknown (a game's own table).

For each line a reader OCR'd while the snapshot was taken, the line's runs of Japanese characters are searched for by
their **pattern of repeated characters** alone: positions holding the same character must hold the same code,
different characters different codes. That needs no knowledge of the table. It's tried for 1-byte codes and for
2-byte codes in both byte orders and alignments. OCR misreads are tolerated by also trying overlapping windows of a
long run. Matches give code -> character pairs; pairs from all lines and snapshots are combined, and a code read as
two different characters (or a character with two codes) is reported as a conflict, not guessed.

The kana layout is then easy to read off: `gojuon_offsets` shows code minus the character's gojuon position for the
plain kana, which is a single number when a game keeps kana in gojuon order (Human's games do).

Most games on later systems use a standard encoding, and there the question is only *where* the line is (the
address a layout needs). `find_standard` answers that first, by plain search, in a fraction of the time, and
`propose` turns its matches into a layout's `message` block when the lines stand in one fixed buffer."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import numpy as np

from .layout import LayoutError, Ram, read_text

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
        return np.frombuffer(ram, dtype=np.uint8)
    n = (len(ram) - align) // 2
    return np.frombuffer(ram, dtype=dtype, count=n, offset=align)


def isomorph(a: np.ndarray, seg: str) -> np.ndarray:
    """Every position where `a` repeats its codes exactly as `seg` repeats its characters (first code not zero)."""
    n = len(seg)
    L = len(a) - n
    if L <= 0:
        return np.array([], dtype=np.int64)
    order = sorted(((i, j) for i in range(n) for j in range(i + 1, n)), key=lambda p: seg[p[0]] != seg[p[1]])
    # The first repeated pair is checked over all of memory; it leaves so few places (one in 65,536 for two-byte
    # codes) that the other pairs are only looked at there. Without a repeat, every nonzero place is a candidate.
    (i, j), rest = (order[0], order[1:]) if order and seg[order[0][0]] == seg[order[0][1]] else ((0, 0), order)
    at = np.nonzero((a[i:i + L] == a[j:j + L]) & (a[:L] != 0))[0]
    for i, j in rest:
        if not len(at):
            break
        same = a[at + i] == a[at + j]
        at = at[same if seg[i] == seg[j] else ~same]
    return at


STANDARD = {"shift_jis": "cp932", "euc_jp": "euc_jp", "utf16le": "utf-16-le", "utf16be": "utf-16-be", "utf8": "utf-8"}


def find_standard(snapshots: list[tuple[bytes, list[str]]], min_len: int = 4, max_hits: int = 16) -> dict[str, list[dict]]:
    """Where the OCR'd lines stand in a standard encoding: {encoding: [{snapshot, offset, segment}]}, the encoding
    with the most matched characters first. A run is matched piece by piece (`min_len` characters to start, then
    as far as memory agrees), so a misread, a row break or a control code inside the line only splits it. Where
    copies of the line break at different points (a window stored row by row beside a script copy in one piece),
    the search goes on from the earliest break, so each copy's later pieces are found too. A piece found in more
    than `max_hits` places says nothing and is skipped. UTF-16 is only looked for at even offsets."""
    out: dict[str, list[dict]] = {}
    for n, (ram, lines) in enumerate(snapshots):
        for name, codec in STANDARD.items():
            covered: list[range] = []                # bytes already matched in this snapshot: a longer copy's tail
            for run in dict.fromkeys(r for line in lines for r in JP.findall(line)):
                i = 0
                while i + min_len <= len(run):
                    try:
                        key = run[i:i + min_len].encode(codec)
                    except UnicodeEncodeError:
                        i += 1
                        continue
                    hits, at = [], ram.find(key)
                    while at >= 0 and len(hits) <= max_hits:
                        if at % 2 == 0 or not name.startswith("utf16"):    # at an odd address it's the other byte
                            hits.append(at)                                # order's text, read one byte off
                        at = ram.find(key, at + 1)
                    shortest = 0
                    for at in hits if len(hits) <= max_hits else []:
                        if any(at in r for r in covered):
                            continue
                        k = min_len
                        while i + k < len(run):
                            try:
                                more = run[i:i + k + 1].encode(codec)
                            except UnicodeEncodeError:
                                break
                            if ram[at:at + len(more)] != more:
                                break
                            k += 1
                        out.setdefault(name, []).append({"snapshot": n, "offset": at, "segment": run[i:i + k]})
                        covered.append(range(at, at + len(run[i:i + k].encode(codec))))
                        shortest = min(shortest or k, k)
                    i += shortest or 1
    return dict(sorted(out.items(), key=lambda kv: -sum(len(h["segment"]) for h in kv[1])))


TEXT = re.compile(r"[\u0020-\u007e\u2015\u2026\u3000-\u30ff\u4e00-\u9fff\uff00-\uffef]+")
MAX_ROWS = 8


def propose(snapshots: list[tuple[bytes, list[str]]], hits: list[dict], charset: str, base: int = 0,
            reach: int = 4096, slack: int = 16) -> dict | None:
    """A layout's `message` block from `find_standard`'s matches in one encoding, or None when no place was seen
    to change from one line to another (a game also keeps script and backlog copies, where an old line stays put
    and the next one stands further on, so a single line can't tell them from the window's buffer).

    A place counts when one snapshot held a line there and another snapshot held a different line within `slack`
    characters of it (OCR may miss a line's first characters) and no longer the first. The buffer starts at the
    lowest such place, moved back over any text right before it that OCR missed. Matches further on that begin
    right after empty space are taken as later rows of a window stored row by row; their distances give the
    stride. The result is then read with the reference reader: `agreement` is how much of each snapshot's OCR it
    gives back (0-1; None where the buffer reads empty while something else is on screen) and `longer` how many
    more characters it held than the screen showed (a line still typing out, in a buffer filled at once)."""
    unit = 2 if charset.startswith("utf16") else 1
    zero = bytes(unit)
    changed = Counter()
    for a in hits:
        was = a["segment"].encode(STANDARD[charset])
        for b in hits:
            if b["snapshot"] != a["snapshot"] and abs(b["offset"] - a["offset"]) <= slack * unit \
                    and a["segment"] not in b["segment"] and b["segment"] not in a["segment"] \
                    and snapshots[b["snapshot"]][0][a["offset"]:a["offset"] + len(was)] != was:
                changed[a["offset"]] += 1
    if not changed:
        return None
    groups: list[list[int]] = []
    for o in sorted(changed):
        groups[-1].append(o) if groups and o - groups[-1][-1] <= reach else groups.append([o])
    start = max(groups, key=lambda g: sum(changed[o] for o in g))[0]

    back = 0                                             # text right before the first match, in any snapshot
    for ram, _ in snapshots:
        k = unit
        while k <= 32 * unit and start - k >= 0 and ram[start - k:start - k + unit] != zero:
            try:
                if TEXT.fullmatch(ram[start - k:start].decode(STANDARD[charset])):
                    back = max(back, k)
            except UnicodeDecodeError:
                pass
            k += unit
    start -= back

    later = {h["offset"] - start for h in hits if start < h["offset"] <= start + reach
             and snapshots[h["snapshot"]][0][h["offset"] - unit:h["offset"]] == zero}
    stride = math.gcd(*later) if later else 0
    spec = {"address": f"0x{base + start:08X}", "charset": charset}
    if stride >= 8 * unit:
        seen = count = max(later) // stride + 1
        while count <= MAX_ROWS and not any(any(ram[start + count * stride:start + (count + 1) * stride]) for ram, _ in snapshots):
            count += 1                                   # rows nothing was seen in, up to where other data begins
        count = seen if count > MAX_ROWS else count
        spec.update(max_bytes=stride * count, terminator=zero.hex(), rows={"count": count, "stride": stride})
    else:
        seen, longest = 1, 0
        for ram, _ in snapshots:
            n = 0
            while ram[start + n:start + n + unit] not in (zero, b""):
                n += unit
            longest = max(longest, n)
        spec.update(max_bytes=max(64, 1 << (2 * longest - 1).bit_length()), terminator=zero.hex())

    agreement, longer = [], []
    for ram, lines in snapshots:
        want = "".join(JP.findall("".join(lines)))
        try:
            got = "".join(JP.findall(read_text(spec, Ram(base, ram), {})))
        except LayoutError:
            got = ""
        same = sum(m.size for m in SequenceMatcher(None, want, got, autojunk=False).get_matching_blocks())
        agreement.append(None if want and not got else same / len(want) if want else float(not got))
        longer.append(max(0, len(got) - len(want)))
    return {"message": spec, "agreement": agreement, "longer": longer, "rows_seen": seen}


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
