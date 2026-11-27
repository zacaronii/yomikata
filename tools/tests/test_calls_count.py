"""A game's calls are counted once, the same by the harness's budget (record.counts) and by `yomikata calls`
(calls.summary), whatever lines the record holds for them: most calls write a line when they begin and another
when they end, a call whose process died has only the first, and an older call only the last. Counting the
record's lines instead gives nearly twice as many."""
import json
from pathlib import Path

from yomikata.calls import summary
from yomikata.harness import record


def _line(**entry) -> str:
    return json.dumps(entry, ensure_ascii=False) + "\n"


def test_the_budget_and_yomikata_calls_count_a_game_alike(tmp_path):
    a, b = "snes/Made Up (Japan).zip", "gb/Other (Japan).zip"
    lines = []
    for k in range(5):                                       # five calls of a, each begun and finished
        lines += [_line(at="2026-01-01T00:00:00Z", tool="steps", game=a, session="s", call=f"a{k}", begun=True),
                  _line(at="2026-01-01T00:00:01Z", tool="steps", game=a, session="s", call=f"a{k}", ok=True, seconds=1)]
    lines.append(_line(at="2026-01-01T00:00:02Z", tool="load", game=a, session="s", call="a5", begun=True))   # died
    lines.append(_line(at="2026-01-01T00:00:03Z", tool="load", game=b, session="t", ok=True))                 # older form
    lines += [_line(at="2026-01-01T00:00:04Z", tool="run", game=b, session="t", call="b1", begun=True),
              _line(at="2026-01-01T00:00:05Z", tool="run", session="t", call="b1", ok=True)]   # the game on its first line only
    (tmp_path / "record").mkdir()
    path = tmp_path / "record" / "calls.jsonl"
    path.write_text("".join(lines), encoding="utf-8")
    _, budget = record.counts(tmp_path)
    told = {row["game"]: row["calls"] for row in summary(path)}
    assert budget == told == {a: 6, b: 2}, (budget, told)
    raw = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if json.loads(line).get("game") == a)
    assert raw == 11                                         # lines, not calls
    assert next(r for r in summary(path) if r["game"] == a)["failed"] == 1    # the one that died
