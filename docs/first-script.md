# A first script, from a game file

How to get from a game you own to a script that reads it, with the harness (docs/harness.md) and nothing else:
no snapshots sent by a reader, no modified emulator. The steps name the harness's tools; an agent calls them,
and a person can through any MCP client. What the script may do is in docs/script-api.md.

Game text and pictures stay in the harness's scratch folder and in your own eyes. The repository gets
addresses, counts and the script.

## 1. Which game it is

`identify` gives the game's platform, its id and what names its build (a disc's boot-executable SHA-1, a
cartridge's header checksum). `load` it in the console's core.

```sh
yomikata new-script ps1 SLPS-01234 --title "題名 (Title)" --build <the hash>
```

writes `games/ps1/SLPS-01234.lua` and, where the game has no entry, a small `SLPS-01234.json`, and prints a test
to start from. Everything in the skeleton that is an address is a place to fill in.

## 2. To a line of text, and a way back to it

`steps` plays: buttons held, then a wait, with `shot: true` to see where you are on one sheet. Titles want a
long press of start more often than a short one, and intros run for thousands of frames: wait rather than press.

At the first line of story, `waypoint_save`. A waypoint keeps every move since power-on, so it survives a new
build of the core; `state_save` freely on top of it for the short way back.

## 3. Where the text is

With a line on screen, in this order (docs/harness.md, "Finding where the text is"):

- `text_runs`: a game in a standard encoding shows its text here, often twice: the script where it lies, and the
  buffer it is typed into. The one to read is the one that changes with the screen.
- `find` with the line, or with parts of it.
- `find_relative` when neither finds it: the codes are the game's own. Then the script needs a table beside it
  (`games/<platform>/<id>.tbl`), which grows from the codes each found line gives.

Take two snaps at different lines and ask `changed`: what differs is the text and whatever counts it. Look for
what says a line is *on screen* and *complete* (a flag, a count of characters typed, a wait state): `changed`
between a snap with the window up and one without, `same_as_a` and `same_as_b` with more snaps to narrow it.
`refs` finds what points at the text, when the text moves.

## 4. The build check

Snap early after power-on, at the title and in play, and ask `suggest_build_words` with the three. It gives
places that hold the same in all of them and the Lua lines that check them. Words taken from play alone are
often part of something loaded later, and the script then refuses the game at its title.

## 5. Write it, try it

Fill in the skeleton. `script_try` loads it on the running game (send what `yomikata bundle` prints, so `lib/`
modules are inside it); `run_until` with `event: "text"` runs to the next thing it says. Read what it says
against the screen. Decide what one emitted text is: a line, a page, what a window gained. A text that types
out is said when it is whole, not letter by letter. A choice's answers go out with `channel = "choice"` and the
cursor with `reader.cursor`.

`lib/` has what earlier games needed: `tbl` (a table and its decoding), `inplace` (a script read where it
lies), `tiles` (a window kept as tiles, and what a scrolling window gained), `markup`.

## 6. Check it

A stretch of the game, a snap at each step:

```json
{"steps": [{"buttons": ["a"], "until": {"limit": 600, "event": "text"}, "times": 40, "snap": true}], "prefix": "check"}
```

Then `sheet` the snaps a dozen at a time, read each picture, and give each snap its lines with `snap_lines` (or
`no_text`). `compare` counts how many of the script's readings agree with them. Take snaps where there is no
text too (the title, a menu, a walk): a script must read nothing there. Where the game has an entry with a
layout, give `compare` the entry as well.

Also: `repeat` a few steps to see that the core repeats itself here; load a state and see that the script says
the line that is up, once; and `script_try` it on the earliest snap's moment (`waypoint_go` with `replay`, or a
fresh `load`) to see the build check hold from the start.

## 7. The test, and the pull request

Paste the test `new-script` printed into `tools/tests/test_scripts.py` and make its memory look as the game's
does: the script must say the line, say nothing when the window is down, and refuse another build. Then:

```sh
yomikata validate && yomikata index && pytest tools/tests
```

In the pull request: the game and build, the core and its SHA-256, the counts from `compare`, what was played
(how far into the game, which kinds of text), and what wasn't reached. If memory was written to reach a moment
(`poke`), list every check that relied on it and what was written. No game text beyond a sample line or two in
the description, and none in the repository.
