# The emulator harness (work in progress)

A game run headless and kept at hand, so that an agent can play to a text, find it in memory, and try a script on
it, without a device and without a person passing snapshots. It is `yomikata replay`'s core runner kept alive as
a session, offered as tools over [MCP](https://modelcontextprotocol.io) on standard input and output.

**Status:** the tools and the server are tested on a made-up machine, and have been run end to end with the
Dolphin core on a GameCube game: loaded, a reader's savestate from the inbox restored, the game's script
tried, lines advanced with presses and read, the layout overlaid on the frame. The same was done with PCSX
ReARMed on a PS1 game, from a reader's savestate, and that game's script written and checked with it.

## Shut in

The harness runs native code from elsewhere (libretro cores) on games, so it is run in a container that can
reach nothing it doesn't need. `tools/scripts/harness.sh` starts it so:

- **no network** at all;
- **read-only:** the library's game folders, a folder of cores, an inbox's snapshots (for readers' savestates);
- **written:** one scratch folder of its own: states, snaps, and whatever a core keeps (its saves go there, never
  to a save of yours);
- nothing else of the machine; no added privileges; as user 99:100; with a cap on processors and memory, at low
  priority; one at a time (the container has a fixed name).

Inside, every path a tool takes is resolved and refused if it leaves its folder, by `..` or through a link.

What the tools return holds game text and pictures. That is for the agent's eyes and the scratch folder: the
catalog's rule stands, nothing of a game goes into the repository.

## Tools

| | |
|---|---|
| `status`, `games` | What is loaded; the library's folders and files |
| `load` | A game in a core, from power-on or from a savestate (one saved here, or a reader's in the inbox) |
| `run` | N frames with buttons and the left stick held. With a script loaded, what it emitted on the way |
| `run_until` | Runs until a place in memory changes or holds a value, the script emits something, or the picture has stood still; at most a given number of frames. Says how far it ran and why it stopped |
| `steps` | Several moves in one call: hold buttons, then wait (frames, or until something as `run_until`), repeat, snap, take pictures. The pictures come back as one sheet |
| `screenshot` | The picture now |
| `read` | Memory by address, a region by offset |
| `find` | Where a text or bytes stand, in live memory or in snaps. A text is looked for in Shift-JIS, EUC-JP, UTF-16 either way, UTF-8, Shift-JIS as 16-bit little-endian numbers, and the game's own table when one is given; in parts too, when the whole isn't found |
| `text_runs` | The stretches of memory that read as Japanese, longest first: what to hold against the screen before any line is known |
| `find_relative` | A line read off the screen, looked for in codes of the game's own: where the same characters have the same code and different ones different codes. Gives the codes, the start of a table |
| `identify` | Which release a game file is: platform, id, and what names its build |
| `suggest_build_words` | Places that hold the same in snaps from power-on, the title and play, and the Lua lines that check them: a script's build check |
| `on_disc` | Whether a stretch of memory lies on the game's disc as it is, and where: if a place's text or font does, every place can be read from the disc without playing to it |
| `core_options` | The loaded core's options as it declares them, and what was given at load |
| `snap` | Keeps a moment under a name, and writes it to scratch as an inbox-style snapshot for `yomikata run` and `check` |
| `snap_lines` | Says what a snap's screen showed, once read off its picture: its lines, or that it has no text |
| `sheet` | The pictures of several snaps as one image, each under its name |
| `repeat` | Does the same frames or steps again and says whether the game ended up the same: whether the core repeats itself |
| `differs` | How this moment's memory differs from a snap's, also one another harness wrote |
| `changed` | Places that differ between two snaps, the same as the first in some others and as the second in others: a flag, a counter, a cursor |
| `refs` | Words that point at an address or just past it, in the console's byte order |
| `state_save`, `state_load` | States by name, in scratch. A state keeps the way to it beside it |
| `waypoint_save`, `waypoint_go`, `waypoints` | A moment kept with every move that leads to it from power-on, and gone back to: by its state, or by playing the moves again when the core is another build |
| `script_try` | Loads a script on the running game: its load error (another build, a missing region), or its header |
| `compare` | Runs a script over snaps and counts: what it reads against the lines given with `snap_lines`, and, given an entry, against that entry's layout (as `yomikata compare`). Counts hold no text |
| `lua` | One line with the script functions on live memory |
| `poke` | Only with `--allow-poke`: writes bytes into main memory, to reach a moment. Recorded ("Writing memory", below) |
| `overlay` | The picture with the script's layout cells drawn on it |

The way to a script, with them: `load`, `steps` to a line (a sheet shows where you are), `find` it, `snap` at a
few moments, `changed` and `refs` to find what says it is on screen, write the script, `script_try`, `run_until`
it emits and read what it says, `overlay`. Then the check: `steps` through a stretch of the game with a snap at
each, `sheet` to read the snaps' screens, `snap_lines` for each, and `compare`.

docs/first-script.md walks through all of it, from a game file to a pull request.

A step is an object: `buttons`, `stick`, `hold` (frames they are held, 4 unless said), then `wait` (frames) or
`until` (what `run_until` takes, with its `limit`), `times` to repeat it, `snap` (true, or a name) and `shot`
(true: its picture goes on the sheet). For example, a press and a wait for the next line, twenty times, a snap
at each: `{"buttons": ["a"], "until": {"limit": 600, "event": "text"}, "times": 20, "snap": true}`.

### Finding where the text is

In the order to try them:

1. `text_runs`, with a line on screen. A game that keeps its script in a standard encoding shows it here, and
   often the buffer it is typed into as well: the address of the stretch that reads as the screen does.
2. `find` with the line as you read it, when you want one line's places only, or only know part of it
   (`pieces`). It finds a text kept as 16-bit cells too (`sjis_le16`).
3. Nothing found: the codes are the game's own. `find_relative` with the line: it needs characters that repeat
   in it, and takes the line's rows with a line break between them. Where the game has other codes among the
   line's, the whole doesn't fit and its rows are tried by twos and alone (`parts`). A window that keeps tiles
   often has a second cell for each character (`stride`: 2) and rows that aren't next to each other: give one
   row.
4. Surer than one line: `lines`, two or more snaps each with what its screen shows at the same place. One
   place must fit them all, and the characters the lines share must have the same code, so lines without a
   repeat will do.
5. A script read where it lies keeps each line somewhere else, so two moments share no place: `apart`, several
   lines of one scene looked for together in one memory, each wherever it is. Two of them must share three
   characters or more. Give kana stretches: a game may keep kanji in two bytes, and a name the player chose
   as a code that stands for it, and either breaks a line that is taken as one code a character.
6. A game that lets the player enter a name tells its codes outright. On the naming screen, snap, enter one
   character, snap, enter another, snap: the places that took a value one after the other are the name, and
   the values are those characters' codes. A few kana give the rule the rest follow (a row, an offset between
   hiragana and katakana), and with a handful of codes a plain `find` by bytes finds the dialogue. This is what
   found Final Fantasy IX's text, after every search by the line itself had failed.
7. The codes it gives are the start of the game's table. With them as a table, `find` (`table`) looks for
   other lines, and each new line adds characters.

What it finds may be the script where it lies, the buffer the game types into, or a copy of the screen's
tiles; which one changes as the line changes (`changed`, `run_until` on its first cell) says which to read.

Seen on games that already have scripts: `text_runs` gave both the script and the typed buffer of a game with
16-bit Shift-JIS cells; `find_relative` found a line of a game with its own 16-bit codes from two of its rows,
though not from all three, which have a colour code among them; and with two moments it found a tile window's
copy of the screen (its rows' two halves), not the window's own buffer, which had moved to another slot between
the two.

### Snaps on disk

A snap is written to `snaps/` in scratch as an inbox-style snapshot: `<name>.ram.gz`, a `<name>.<region>.gz`
for each region, `<name>.json` and `<name>.png`. The first snap after a game is loaded is whole. The later
ones are kept as what differs from that first one (`tools/src/yomikata/snapfile.py` has the form): the same
file names, read by everything here that takes snapshots (`run`, `check`, `compare`, `locate`, `where`,
`propose`, the harness's own tools). Measured over thirty snaps of a PlayStation game: 535 KB for the first and
some 60 KB for each of the others, where each was 500 KB before; over ten of a Super Famicom game, 31 KB and
then 1.4 KB.

What follows from it: the later snaps need the first one beside them. Delete or overwrite it and they say so
instead of reading wrongly. `yomikata expand <files>` rewrites snaps as whole ones, for moving a few elsewhere or
for a tool that doesn't know the form. Snapshots a reader uploads to an inbox are never kept this way.

### Waypoints

A state is the quick way back to a moment, and it only loads in the core build that made it. A waypoint is the
lasting one: the game, the core and its options, and every move since power-on, as frames with the buttons and
the stick held; with a state for speed and the picture to check by.

The harness keeps the way as it goes: each `run`, `run_until` and `steps` adds the frames it actually ran, so a
wait "until" is kept as the number of frames it took. `state_save` writes the way beside the state, so a state
loaded later carries on from power-on; a state without one (a reader's, from the inbox) is where the way then
starts, and a waypoint made from there needs that state. A memory write on the way is part of it: replaying
such a waypoint writes again, needs `--allow-poke`, and the waypoint says how many writes it holds.

`waypoint_go` loads the game if it isn't loaded, and takes the state when the core file is the one that made
it (by its SHA-256); otherwise, or with `replay`, it plays the moves. Either way it says whether the picture is
the one kept. A replay that gets there keeps a state of the new build. What is checked is the picture, not
memory: from power-on only PCSX ReARMed ends with the same memory (above), and the picture is what a script's
text is read against.

Replayed in a second harness, from power-on: 11,660 frames with ten presses on PCSX ReARMed, 2,636 frames with
nine presses on bsnes-mercury, and 1,500 frames without a press on Dolphin; each reached its picture. A long
way through a game's random events hasn't been tried on the two cores that don't repeat exactly: if one stops
reaching its picture, make the waypoint from a state taken early instead.

### A game's own font

A game whose codes are its own draws from a font of its own, and nothing in memory says which character a glyph
is. Where memory has a list of the glyphs (where each one's picture is in a texture page) and video memory has
the pictures, `yomikata glyphs` draws them from a snap, in code order, for reading:

```sh
yomikata glyphs snaps/place.ram.gz --list 0x800D7110 --count @0x800D710C \
    --dictionary tables/ps1/SLPS-02000.glyphs --sheet /scratch/work/unread.png
```

It prints each glyph's number, a fingerprint of its picture and its character where the dictionary has it, and
writes a sheet of the glyphs it hasn't: the first of each fingerprint only, so a character is read once however
many fonts and places it turns up in. Sheets are game data and stay in scratch; the catalog keeps the readings
(`tables/<platform>/<id>.glyphs`, fingerprint=character) and the tables made from them.

Reading a glyph by itself goes wrong a few times in a hundred at this size. What catches it is the game's own
text: decode the place's messages with the readings and read the sentences. A wrong kanji shows at once in a
sentence; a glyph that stays in doubt is left out, and comes out as U+FFFD, rather than guessed.

Three things the tools do to catch a wrong reading, none of them sure by itself:

- beside each unread glyph, `glyphs` names the three pictures already read that look most like it (`like:`), so
  that a glyph is told from what it could be taken for, not read alone;
- a character the dictionary gives to two pictures is said (`glyphs.twice`): a font draws a character one way,
  so one of the two readings is wrong;
- the read-through: `yomikata run --dump --unconfirmed <file>` prints a place's texts that hold a kanji not
  yet confirmed in a sentence, each such kanji in 【 】, shortest first. The file lists the characters someone
  has read in a sentence and found right (`tables/<platform>/<id>.read`); it grows as places are read through.

Before reading a font place by place, ask `on_disc` whether the place's text and font lie on the disc as they
do in memory. Where they do, a small script over the disc image finds every place at once
(`tools/scripts/ff9_places.py` is one) and nothing has to be played to or written.

On Final Fantasy IX: the fixed font's 461 kanji were read from five sheets; the second place's own font had 218
glyphs, of which the dictionary from the first place already had 88, and its 130 new ones were one sheet.

### Writing memory

Off unless the harness is started with `--allow-poke`; then there is one more tool, `poke`: bytes at an address
of main memory, with a sentence saying why.

It is for REACHING a moment the game is slow to get to: a chapter, a flag, a position, a timer. Then the game
produces its text itself, and that text is what a script is checked on. It is never for writing the text, or a
buffer a script reads: that proves a script reads what was written, not what the game shows. Where there is
no other way to show something (a choice nobody could reach in play), such a write is a made-up test and is
marked `synthetic`.

So that this can be told afterwards:

- every write goes into `record/memory-writes.jsonl` in scratch: when, the game, the frame, the address, the
  bytes before and after, why, and whether it was marked synthetic;
- a snap taken after a write says how many came before it (`memory_writes`), `compare` counts such snaps apart,
  and a state saved after one is noted in the record;
- a pull request lists every check that relied on a write and what was written. A count with written snaps in
  it is quoted as such.

The count starts again when a game is loaded. A state saved after a write carries it: a file beside the state
(`<name>.writes.json`) says how many, and loading that state, in this session or a later one, sets the count, so
what is snapped from there is marked too. Loading a state from before the writes clears it.

### A script that reads a line only as it appears

Some games keep nothing that says which line is on screen once it is drawn; a script for one reads the line
in the frames it is being typed. Run over a still snap it reads nothing, so `compare` can't check it that
way. A snap taken while a script is loaded keeps what the script said since the snap before (`said`), and
`compare` with `live` counts that against the snap's lines instead: step to each line with `until` on the
script's event, wait until the line stands, snap, and read the picture.

### A call's time

A call that runs the game stops by itself after `--call-seconds` (120 unless said) and says so: `"stopped":
"time"`, with how far it got; the game stands where it stopped and the next call goes on. This is what a slow
core does to a long run, and nothing is lost by it.

A core that hangs inside one frame can't be stopped that way. A call that hasn't come back a minute after its
time is answered with an error that says so, and the harness ends: its container goes, states and snaps in
scratch stay. Start it again and load a state.

## Running it

```sh
tools/scripts/get-core.sh pcsx_rearmed /path/to/cores        # prints the core's SHA-256: note it with what you find
export YOMIKATA_GAMES=… YOMIKATA_CORES=… YOMIKATA_SNAPSHOTS=… YOMIKATA_SCRATCH=…
claude mcp add yomikata-harness -- "$PWD/tools/scripts/harness.sh"
```

The image is `tools/Dockerfile.harness`: the tools image with Mesa's software OpenGL and the libraries the
Dolphin core needs. A core that needs a BIOS looks for it in `system/` under scratch, or in a folder of its own
given as `YOMIKATA_SYSTEM` (mounted read-only): your own console's, never one from elsewhere. PCSX ReARMed plays
most games without one; some show nothing until it has one.

### Does a core repeat itself?

Everything found by replaying rests on the same steps reaching the same moment. `repeat` checks it: it does
the same frames or steps two or more times, from this moment (going back by a state kept in memory) or from
power-on (loading the game anew), and says whether memory, video memory and the picture ended up the same, and
where memory differs. `differs` holds this moment against a snap written by another harness, for a core that
can't be loaded twice in one.

Measured with the cores named, on one machine:

| Core | From a state | From power-on |
|---|---|---|
| PCSX ReARMed | the same: 3 runs of 752 frames with presses | the same: 2 runs of 900 frames |
| bsnes-mercury (balanced) | the same: 3 runs of 634 frames with presses | not the same: after 1500 frames some 16,300 bytes of work RAM differ (`0x7FC001`-`0x7FFFFF`), also between two harnesses; the picture and video memory are the same |
| Dolphin (one emulation thread) | the same: 3 runs of 496 frames with presses | not the same: after 900 frames in two harnesses, 110 of 24 million bytes differ, the console's clock and what derives from it |

So: from a state all three repeat; from power-on only PCSX ReARMed does. On bsnes-mercury, loads made within
the same second matched and loads seconds apart didn't, which fits memory the core fills from the clock at
power-on and the game hasn't written yet; that reading comes from these runs, not from the core's source. A
route kept for these two cores should start from a state made early, not from power-on, or be checked on what
the game shows rather than on all of memory.

Neither core has an option that pins it (`core_options` lists what a core declares): bsnes-mercury declares
seven, none about memory at power-on or randomness; Dolphin's 113 have a CPU clock rate but no setting for the
console's clock. So there is nothing to give at `load` for this.

Notes on cores:

- A savestate loads only in the core build that made it. Note the core's SHA-256 with every state kept.

PCSX ReARMed (a PlayStation game):

- It gives out no video memory, but its savestate holds it. The harness takes it from there: `read` with
  `region: "vram"`, a snap's `vram` part and a script's `mem.region("vram")` have the console's 1024x512 words.
  It costs a savestate each frame it is asked for, so a script that reads it every frame runs slowly here.

Dolphin (`dolphin_libretro.so`, a GameCube or Wii game):

- It has no renderer that works without a context: the core sets its video side up only when a frontend's
  context comes up, and without one the first frame crashes. The harness gives it OpenGL drawn by the CPU (Mesa's
  llvmpipe), so no graphics card is used. Measured with 8 processors allowed: 45 frames a second in a scene of
  the game, over 100 while it starts.
- Give it the option `dolphin_main_cpu_thread=disabled` (one emulation thread). With it, two runs of 900 frames
  from power-on ended with 117 of memory's 24 million bytes different, all in the console's clock and what
  derives from it: close to repeatable, not exactly.
- A savestate made by the same build on an Android device (arm64) loads here (x86-64) and goes on. It must not be
  loaded in the core's first frames, or the game stands still: the harness runs 300 frames first.
- It can't be loaded twice in one harness: the second load never comes back. The harness refuses it and says
  to start again.
- It needs `/dev/shm` larger than Docker's default (the script gives 2 GB), and libcurl, libEGL and libusb
  (in the harness image).
- Memory is given by address (the 24 MB from `0x80000000`), in the console's byte order.
- Once, a session stopped answering with one processor busy and nothing coming back, from its first call; the
  same calls ran in seconds when made again. The cause isn't known. The harness now gives such a call up by
  itself ("A call's time", above): seen working on a second load of this core, given up after its time.
