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


def test_amagami_loses_the_engines_marks_and_keeps_its_line_breaks():
    # Made-up lines in the form of the game's: a voice, waits, the portrait's turns, a reading, the player's names.
    e = entry("ps2/SLPS-25918.json")
    assert clean_line("「V8000そうだね……W45EF3もう春だよね」", e) == "「そうだね……もう春だよね」"
    assert clean_line("晴れた日の空は、W15／とても青い気がした……。", e) == "晴れた日の空は、\nとても青い気がした……。"
    assert clean_line("「V8041もうね、W30EoF9朝から／Fm3ねむいからEcFe4m7」", e) == "「もうね、朝から\nねむいから」"
    assert clean_line("「V8003『腹がR空R1すいてはR戦R3いくさはできぬ』EF3Ecってね」", e) == "「『腹が空いては戦はできぬ』ってね」"
    assert clean_line("（R山田R3やまだR花子R3はなこ……）", e) == "（山田花子……）"
    assert clean_line("「V8015Nm君の話、W15Wv120Nnはどう思う？B60P1SdLe」", e) == "「橘君の話、純一はどう思う？」"
    assert clean_line("W30Ec", e) is None                                  # nothing but marks: no line
    assert clean_line("１０時に、Ｂ組で", e) == "１０時に、Ｂ組で"                # full-width letters and digits are text
