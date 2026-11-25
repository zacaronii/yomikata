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
| `read`, `find` | Memory by address, a region by offset; where a text (Shift-JIS, EUC-JP, UTF-16 either way, UTF-8) or bytes stand |
| `snap` | Keeps a moment under a name, and writes it to scratch as an inbox-style snapshot for `yomikata run` and `check` |
| `snap_lines` | Says what a snap's screen showed, once read off its picture: its lines, or that it has no text |
| `sheet` | The pictures of several snaps as one image, each under its name |
| `repeat` | Does the same frames or steps again and says whether the game ended up the same: whether the core repeats itself |
| `differs` | How this moment's memory differs from a snap's, also one another harness wrote |
| `changed` | Places that differ between two snaps, the same as the first in some others and as the second in others: a flag, a counter, a cursor |
| `refs` | Words that point at an address or just past it, in the console's byte order |
| `state_save`, `state_load` | States by name, in scratch |
| `script_try` | Loads a script on the running game: its load error (another build, a missing region), or its header |
| `compare` | Runs a script over snaps and counts: what it reads against the lines given with `snap_lines`, and, given an entry, against that entry's layout (as `yomikata compare`). Counts hold no text |
| `lua` | One line with the script functions on live memory |
| `poke` | Only with `--allow-poke`: writes bytes into main memory, to reach a moment. Recorded ("Writing memory", below) |
| `overlay` | The picture with the script's layout cells drawn on it |

The way to a script, with them: `load`, `steps` to a line (a sheet shows where you are), `find` it, `snap` at a
few moments, `changed` and `refs` to find what says it is on screen, write the script, `script_try`, `run_until`
it emits and read what it says, `overlay`. Then the check: `steps` through a stretch of the game with a snap at
each, `sheet` to read the snaps' screens, `snap_lines` for each, and `compare`.

A step is an object: `buttons`, `stick`, `hold` (frames they are held, 4 unless said), then `wait` (frames) or
`until` (what `run_until` takes, with its `limit`), `times` to repeat it, `snap` (true, or a name) and `shot`
(true: its picture goes on the sheet). For example, a press and a wait for the next line, twenty times, a snap
at each: `{"buttons": ["a"], "until": {"limit": 600, "event": "text"}, "times": 20, "snap": true}`.

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

Notes on cores:

- A savestate loads only in the core build that made it. Note the core's SHA-256 with every state kept.

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
