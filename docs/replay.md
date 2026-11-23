# Replay

Some games never keep their text in RAM as plain codes, or keep their font in a format only the game's own code
understands. Watching the game frame by frame is the way in, and it doesn't need the device: a reader sends a
**savestate** with a snapshot (docs/inbox.md), and the same libretro core runs it headless on any Linux machine,
as often as needed.

`yomikata replay` loads a core, the game image and the savestate, optionally presses buttons (`--press a` to
advance a line), then records **RAM and the frame on every frame** (`ram.npy`, `frames.npy`, and a PNG every 10
frames). Comparing frames shows what changes when each character appears: a typing timer, a script pointer, the
decoded line.

```sh
tools/scripts/get-core.sh pcsx_rearmed ./cores          # official Linux build; prints its SHA-256
docker run --rm --network none \
  -v "$PWD/cores:/cores:ro" -v /path/to/games:/games:ro -v /path/to/inbox/snapshots:/snaps:ro -v "$PWD/out:/out" \
  ghcr.io/<you>/yomikata-tools:latest replay --core /cores/pcsx_rearmed_libretro.so \
  --game "/games/Some Game (Japan).chd" --state /snaps/ps1/SLPS-00000/<sha1>.state.gz --press a --frames 150 -o /out
```

- **Isolation:** cores are native code downloaded from libretro, so run replays in a container with **no network**,
  the games and the inbox **read-only**, and only an output folder writable.
- **Compatibility:** a savestate loads only in a core that expects the same state size. The runner reports both
  (`state … bytes, core expects …`). Savestates from an Android (ARM) build of PCSX ReARMed load in the Linux x86_64
  build of the same core: the state holds the console, not the host, and the BIOS image is inside it.
- **Output holds game data** (RAM, frames): keep it out of the repository and delete it when done.

## Scripted runs, with or without a savestate

`--steps` plays a script instead of `--press`/`--frames`, and saves RAM and the frame only where the script says.
Without `--state` the game boots from power-on, so a layout can be worked out for a game nobody has played on a
device yet. A core that needs a BIOS looks for it in `--system`: use your own console's; the catalog has none.

A core asks its frontend for its options and takes its own default where it gets no answer; `--option KEY=VALUE`
answers (repeatable). PCSX ReARMed runs without a memory card unless told
`--option pcsx_rearmed_memcard1=libretro`, and a game that won't start without one (Dragon Quest VII) stops at
its first menu. The card is empty at each run and isn't part of a savestate, so a save made in one run is gone in
the next: get past the menu and `save:` there.

```sh
yomikata replay --core /cores/pcsx_rearmed_libretro.so --game "/games/Some Game (Japan).chd" --system /system \
  --steps "wait:900, start:4, wait:300, 30x(a:4, wait:40, snap), save:line30" --watch 0x8010AC4D -o /out/run1
```

| Step | |
|---|---|
| `wait:N` | Run N frames |
| `a:N`, `up+a:N` | Hold the button(s) for N frames (RetroPad names: on PS1 `a` is circle, `b` cross) |
| `snap` | Save the frame and RAM as `s000.png` and `s000.ram.gz`, counting up |
| `sheet` | Only add the frame to the contact sheet |
| `save:<name>` | Write `<name>.state.gz`, to start a later run from with `--state` |
| `Nx(…)` | Repeat |

Every run also writes `sheet.png` (all snapped and sheeted frames in order, to see at a glance where the script
got to) and `end.state.gz`. `--watch` prints the byte at each address per snap, which shows on which press a byte
changes. A core is deterministic: the same steps from the same start reach the same moment every time, so a run can
be repeated with snaps at other delays.

A cartridge core (one that doesn't ask for a path) is handed the file's bytes, so the image must be the plain
file, not a zip. Such a console starts with whatever was in its memory: a core may fill work RAM at random on
each power-on (bsnes does), so two runs from power-on differ in every byte the game hasn't written yet. Save a
state once the game is up and start the runs to be compared from it.

## From snaps to a layout

The way the PS1 layouts of Memories Off and Memories Off 2nd were found, without a device:

1. **Get to the text.** Boot, look at the sheet, add presses until lines are advancing. Save a state there.
2. **Sample each line at several delays** after the press, e.g. `20x(a:4, wait:4, snap, wait:8, snap, wait:16, snap,
   wait:30, snap, wait:50, snap)`: early snaps catch the line typing, late ones catch it waiting.
3. **Find the text.** Read a few words off a frame and run `yomikata find "<words>" s012.ram.gz --absent s002.ram.gz`.
   `--absent` drops the places that hold the words at other moments too, which is the script the game keeps
   loaded; what is left are the buffers of the moment (the window's rows, the name, a backlog).
4. **Find the flags.** Sort the snaps by what the frame shows and run `yomikata contrast --same <snaps where the
   game waits> --other <snaps where it types>`: it lists the bytes that hold one value in all of the first group and
   never in the second. The more snaps on both sides, the shorter the list; a flag worth using sits near the
   game's other text state, not on the stack. The same works for a name plate, a hidden window, an open choice.
5. **Write the layout and read it back** from every snap: `yomikata check games/<platform>/<id>.json s*.ram.gz
   -o <scratch folder>` prints the counts (snapshots, with a message, fields that failed, characters that didn't
   decode) and writes `report.html`, each reading beside its frame, to go through by eye. `yomikata layout` shows
   one snap in full (docs/format.md). Then a reader checks it during play; until one has, the entry stays `auto`.

First use (Persona 2, SLPS-02100): a Genkan state loaded and replayed pixel-identical to the device's screenshot;
recording 150 frames after pressing confirm showed the character card typing at one character per 4 frames.
