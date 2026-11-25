"""The skeleton of a new script: its header, a build check, the frame of what reads the text, and a test on
made-up memory to start from. Nothing here knows the game: every address is a place to fill in."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .catalog import SCRIPT_ID
from .layout import RAM_BASE

SCRIPT = '''-- reader: title {title}
-- reader: reads (say what: the message window a line at a time, a choice's answers, …)
-- reader: version {id}
-- reader: build {build}
-- reader: no hooks
-- reader: no layout where the text is drawn isn't looked into

-- How the game keeps what it shows.
--
-- (Say it here, for whoever reads this next: where the text is, in what codes, what says a line is on screen,
-- what was checked and what wasn't.)

local TEXT = {text}   -- the text on screen (fill in)
local SHOWN = {shown}  -- not 0 while the window is up (fill in)

{check}

reader.on_state_loaded(function()
    reader.forget()
end)

reader.on_frame(function()
    if (mem.u8(SHOWN) or 0) == 0 then
        reader.forget("line")
        return
    end
    local bytes = mem.cstr(TEXT, 512)
    if reader.changed("line", bytes) then
        local line = text.sjis(bytes)
        if line ~= "" then
            reader.emit{{ text = line, id = text.hash(line) }}
        end
    end
end)
'''

CHECK = '''-- Words of the program that are there from power-on (fill in: the harness's suggest_build_words gives them).
if mem.u32({word}) ~= 0x00000000 then
    error("this script is for another build of the game", 0)
end'''

TEST = '''
# ---- games/{platform}/{id}.lua ----
# For tools/tests/test_scripts.py: memory made up to look as the game's does, and what the script must say.

def {name}_memory(line: str = "", shown: int = 1):
    return sparse({{{text}: line.encode("cp932") + b"\\\\0", {shown}: bytes([shown if line else 0]),
                   # … and the words the build check reads
                   }})


def test_{name}_a_line_and_another_build():
    with rd.Reader({name}_memory("こんにちは"), platform="{platform}", game_id="{id}") as r:
        r.load(bundle(ROOT / "games/{platform}/{id}.lua"), "{id}.lua")
        r.frame(1)
        assert [e["text"] for e in r.take("text")] == ["こんにちは"]
        r.read = {name}_memory()
        r.frame(2)
        r.read = {name}_memory("こんにちは")
        r.frame(3)
        assert [e["text"] for e in r.take("text")] == ["こんにちは"]         # said again when it comes back
    # with rd.Reader(<memory of another build>, …): pytest.raises(rd.ScriptError, match="another build")
'''


def new_script(root: Path, platform: str, ident: str, title: str, build: str, check: str = "") -> tuple[list[Path], str]:
    """Writes games/<platform>/<id>.lua, and <id>.json where the game has no entry. Never overwrites. Gives what
    it wrote, and a test to paste and fill in."""
    if platform not in RAM_BASE:
        raise ValueError(f"platforms are: {', '.join(sorted(RAM_BASE))}")
    if not SCRIPT_ID.fullmatch(ident):
        raise ValueError("an id is capitals, digits and dashes, as `yomikata identify` gives it")
    ps2 = re.fullmatch(r"[A-Z]{4}-[0-9]{5}-[0-9a-f]{8}", build)
    if not ps2 and not re.fullmatch(r"[0-9A-Fa-f]{8}|[0-9A-Fa-f]{40}|[A-Z0-9]{6}-[0-9A-F]{2}", build):
        raise ValueError("a build is an exe SHA-1 (40 hex), a header CRC-32 (8 hex), a GameCube id-version, "
                         "or a PlayStation 2 serial-CRC (SLPM-12345-1a2b3c4d)")
    folder = Path(root) / "games" / platform
    script = folder / f"{ident}.lua"
    if script.exists():
        raise ValueError(f"{script} is there already")
    base = RAM_BASE[platform]
    text, shown, word = f"0x{base + 0x1000:08X}", f"0x{base + 0x2000:08X}", f"0x{base + 0x100:08X}"
    folder.mkdir(parents=True, exist_ok=True)
    script.write_text(SCRIPT.format(title=title, id=ident, build=build if ps2 or len(build) == 9 else build.lower(),
                                    text=text, shown=shown, check=check.strip() or CHECK.format(word=word)),
                      encoding="utf-8")
    written = [script]
    entry = folder / f"{ident}.json"
    if not entry.exists():
        native, _, roman = title.partition(" (")
        kind = "header_crc32" if len(build) == 8 else "exe_sha1"
        data = {"id": ident, "platform": platform, "title": native,
                **({"title_romaji": roman.rstrip(")")} if roman else {}),
                **({"versions": [{kind: build.lower()}]} if len(build) in (8, 40) else {}),
                "encoding": "unknown", "status": "partial",
                "sources": ["(say how the script was written and checked)"]}
        entry.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(entry)
    name = re.sub(r"[^a-z0-9]+", "_", ident.lower()).strip("_")
    return written, TEST.format(platform=platform, id=ident, name=name, text=text, shown=shown)
