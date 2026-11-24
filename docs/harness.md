# The emulator harness (work in progress)

A game run headless and kept at hand, so that an agent can play to a text, find it in memory, and try a script on
it, without a device and without a person passing snapshots. It is `yomikata replay`'s core runner kept alive as
a session, offered as tools over [MCP](https://modelcontextprotocol.io) on standard input and output.

**Status:** the tools and the server are tested on a made-up machine. Software-rendered cores go through the
same runner `yomikata replay` uses. A core that draws with the graphics card (Dolphin) gets an OpenGL context
without a window, drawn by the CPU; that path is written and its context checked alone, not yet with the core.

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
| `screenshot` | The picture now |
| `read`, `find` | Memory by address, a region by offset; where a text (Shift-JIS, EUC-JP, UTF-16 either way, UTF-8) or bytes stand |
| `snap` | Keeps a moment under a name, and writes it to scratch as an inbox-style snapshot for `yomikata run` and `check` |
| `changed` | Places that differ between two snaps, the same as the first in some others and as the second in others: a flag, a counter, a cursor |
| `refs` | Words that point at an address or just past it, in the console's byte order |
| `state_save`, `state_load` | States by name, in scratch |
| `script_try` | Loads a script on the running game: its load error (another build, a missing region), or its header |
| `lua` | One line with the script functions on live memory |
| `overlay` | The picture with the script's layout cells drawn on it |

The way to a script, with them: `load`, `run` to a line, `screenshot` to read it, `find` it, `snap` at a few
moments, `changed` and `refs` to find what says it is on screen, write the script, `script_try`, `run` and read
what it emits, `overlay`, then `snap` a set and `yomikata check` it.

## Running it

```sh
tools/scripts/get-core.sh pcsx_rearmed /path/to/cores        # prints the core's SHA-256: note it with what you find
export YOMIKATA_GAMES=… YOMIKATA_CORES=… YOMIKATA_SNAPSHOTS=… YOMIKATA_SCRATCH=…
claude mcp add yomikata-harness -- "$PWD/tools/scripts/harness.sh"
```

The image is `tools/Dockerfile.harness`: the tools image with Mesa's software OpenGL and the libraries the
Dolphin core needs. A core that needs a BIOS looks for it in `system/` under scratch: put your own console's
there.

Notes on cores:

- A savestate loads only in the core build that made it. Note the core's SHA-256 with every state kept.
- The same steps from the same state reach the same moment on the software-rendered cores (docs/replay.md).
  Whether Dolphin does is still to be measured.
- Dolphin has no renderer that works without a context: its `Software` and `Null` settings crash on the first
  frame, because the core sets its video side up only when a frontend's context comes up. It needs `/dev/shm`
  larger than Docker's default (the script gives 2 GB).
