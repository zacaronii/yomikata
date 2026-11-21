"""The reference cleanup against the real catalog entries' rules."""
import json
from pathlib import Path

import pytest

from yomikata.cleanup import apply, clean_line

GAMES = Path(__file__).resolve().parents[2] / "games"


def entry(path):
    return json.loads((GAMES / path).read_text(encoding="utf-8"))


def test_dunamis_drops_ruby_readings_and_colour_tags():
    e = entry("psp/ULJM06119.json")
    assert clean_line("[しょう]少[ねん]年は%0#ff0000ff;走った\\k", e, e["hooks"][0]) == "少年は走った"


def test_danganronpa_keeps_coloured_text_without_its_tags():
    e = entry("psp/NPJH50515.json")
    assert clean_line("<CLT 3>希望<CLT>の学園\n", e, e["hooks"][0]) == "希望の学園"


def test_tokimeki_choice_hook_drops_dialogue_and_keeps_choices():
    e = entry("psp/NPJH50127.json")
    choice = e["hooks"][2]
    assert clean_line("一緒に帰ろうよ。", e, choice) is None              # dialogue also passes here: dropped
    assert clean_line("一緒に帰る", e, choice) == "一緒に帰る"            # a choice
    assert clean_line("行こう\\nか", e, e["hooks"][1]) == "行こう\nか"


def test_grisaia_remaps_half_width_katakana_and_strips_tags():
    e = entry("psp/ULJM06232.json")
    assert clean_line("$tｺﾝﾆﾁﾊ@", e, e["hooks"][0]) == "こんにちは"


def test_replacement_syntax_and_portability_guard():
    assert apply("a1b2", [{"regex": "([a-z])(\\d)", "replace": "$2$1$$"}]) == "1a$2b$"
    with pytest.raises(ValueError):
        apply("x", [{"regex": "(?<=a)x", "replace": ""}])                  # lookbehind isn't portable
