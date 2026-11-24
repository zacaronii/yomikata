# Scripts (draft)

**Status: a draft.** The reader runtime in `runtime/` runs this, and `yomikata run` runs a script over snapshots
with it; the first script is `games/gc/G8MJ01.lua`. Until scripts replace them, the JSON entries and
`docs/format.md` stay as they are.

A **script** is one Lua 5.4 file for one release of a game. A reader app runs it beside the game: the script reads
the game's memory once a frame and says what text is on screen, who says it, which answers a choice offers and,
where it can tell, where each character is drawn. It replaces an entry's `layouts`: a layout describes where text
is, a script works it out, and working it out is what games with pages, typing, compressed scripts and their own
codes need.

Two rules shape everything below:

- **Stock emulators only.** A script reads memory the emulator already gives out: a libretro core's memory map,
  PPSSPP's debugger, Azahar's RPC. It never stops the game at an instruction, so there are no hooks.
- **Read-only.** A script never changes the game's memory.

## Furoshiki's API, and what differs

The API is the memory-only part of [Furoshiki](https://github.com/sommepo/furoshiki-scripts)'s script API
(`docs/script-api.md` there), with a few additions. The names, arguments and results below are meant to be the
same, so that a Furoshiki script that says `-- reader: no hooks` runs here unchanged, and a script written here runs
there once the additions are filled in (see "Running on Furoshiki").

| | Here |
|---|---|
| `reader.hook` | Not there: calling it is an error, as in a Furoshiki script that says `no hooks` |
| `mem.w8`, `mem.w16`, `mem.w32`, `mem.w64` | Always an error |
| `reader.action`, `reader.progress` | Accepted, so such a script loads, but never offered to the player and never run |
| Big-endian reads (`mem.u32be`, …) | **Added**, for GameCube and Wii |
| `mem.region(name)` | **Added**: video memory and other memory outside the address space |
| `-- reader: build`, `-- reader: needs` | **Added** header lines |
| How often `reader.on_frame` runs | A host may skip frames (see "Frames") |
| Limits | A host sets its own, not below the floor in "Limits" |

## A script

```lua
-- reader: title 架空の物語 (A Made-up Story)
-- reader: reads the message window and the speaker's name
-- reader: version 1.00
-- reader: build 0F1E2D3C4B5A69788796A5B4C3D2E1F001234567
-- reader: no hooks
-- reader: no layout the window's place isn't found yet

-- Made-up addresses, for the shape of a script. The message is a Shift-JIS string the game allocates;
-- a big-endian word points at it, and a byte beside that word is nonzero while the window is open.

local WINDOW_OPEN = 0x80123400
local MESSAGE_PTR = 0x80123404
local SPEAKER     = 0x80123410

if mem.u32be(0x80003100) ~= 0x7C0802A6 then
    error("this script is for another build of the game", 0)
end

reader.on_state_loaded(function() reader.forget() end)

reader.on_frame(function()
    if mem.u8(WINDOW_OPEN) ~= 1 then
        reader.forget("message")
        return
    end
    local at = mem.u32be(MESSAGE_PTR)
    if not at or at == 0 then return end
    local bytes = mem.cstr(at, 1024)
    if reader.changed("message", bytes) then
        reader.emit{
            text = text.sjis(bytes),
            speaker = text.sjis(mem.cstr(SPEAKER, 32)),
            id = text.hash(bytes),
        }
    end
end)
```

## Files

- The source is `games/<platform>/<id>.lua`, with the id of `docs/format.md` ("Identity"). The ids are Furoshiki's
  for PS1, PS2 and PSP. *Open: Furoshiki names 3DS scripts by title id, this catalog by product code.*
- Data a script needs goes beside it as `<id>.<name>`: a character table as `<id>.tbl` (Thingy, `docs/format.md`) or
  `<id>.charset` (Furoshiki's: a decimal glyph number, a tab and the character on each line, `#` for comments).
  `reader.data("tbl")` hands the file over as a string; the script reads it. Codes and characters only, as ever.
- A script may `require` a module from `lib/` (walking tags, tiles, reading a `.tbl`). What is run and published
  is one file per game with its modules put in (`yomikata bundle`, which `yomikata run` does by itself), header
  lines still first, so a host never loads anything but the script and its data.
- **No game data,** as everywhere in this repository: addresses, codes, tables and logic are fine; script text,
  glyph pictures and dumps are not. A name plate drawn as a picture may have its names listed by number.

## Header lines

Comment lines at the very top, before any code. A host reads them without running the script.

| Line | | |
|---|---|---|
| `-- reader: title <name>` | required | The title as on the title screen, then the English or romanised name in brackets |
| `-- reader: reads <what>` | required | In one line, what the script reads; what it doesn't goes in the comment below the header |
| `-- reader: version <release>` | required | The release or disc version it was written for |
| `-- reader: build <id>` | required, **added** | A build it was checked on: the SHA-1 of the boot executable, a cartridge's `header_crc32` (`docs/format.md`), or for a GameCube disc its id and version (below). One line per build. The index is made from these |
| `-- reader: no hooks` | required | Every script here is memory-only |
| `-- reader: no layout <why>` | when so | The script can't say where its text is drawn, and why |
| `-- reader: needs <name> …` | when so, **added** | Regions the script can't work without (`vram`). A host that lacks one doesn't load the script, and says so |
| `-- reader: api <n>` | | Furoshiki's: 2 with `reader.progress`, 3 with `reader.action` |
| `-- reader: touch …`, `buttons …`, `audio …` | | Furoshiki's hints to its apps. Kept; a host may ignore them |

A host ignores a header line it doesn't know. *Open: whether Furoshiki's `tools/check.py` accepts `build` and
`needs`.*

## The build

Addresses belong to one build of one game. A script is tied to its build twice:

1. The `build` lines, so a host that can hash the game's file picks the right script before it runs anything.
2. **A check in memory when the script loads**, which every script must have, because a host that only sees
   memory can't hash anything: compare a few words of the game's code, or the id and version the console keeps
   (a GameCube game's are at `0x80000000` and `0x80000007`), and call `error(…, 0)` when they differ.

**GameCube:** a build is the disc's id and its version as two hex digits, `G8MJ01-00`. The console keeps both
at the start of memory (the id's six characters at `0x80000000`, the version's byte at `0x80000007`), so a host
that only sees memory knows the build, the script's own check reads the same two, and the inbox stores them
with each snapshot.

`reader.build_id` is the host's own hash of the build, or nil. Furoshiki's is "a hash of the program as it
ships"; *open: which hash, and whether it equals this catalog's `exe_sha1` on any console.* Until that is known a
script shouldn't compare it to a constant.

## Frames

A host calls the script's `on_frame` functions between two frames of the game, never during one, so every read in
one call sees the same moment. Furoshiki calls once for every frame. A host here may call less often (an
emulator reached over a socket can't answer sixty times a second), so:

- the argument and `reader.frame()` count the game's frames, and may jump;
- a script must not rely on seeing every frame. A state that lasts one frame may be missed; follow state the game
  keeps (a page number, a pointer, a count of typed characters), not the moment it changes.

## `reader`

| | |
|---|---|
| `reader.on_frame(function(frame) … end)` | Run the function on each tick, in the order registered |
| `reader.on_state_loaded(function() … end)` | Run it after a savestate was loaded. Every layout is closed first; the script forgets what it knew about the screen |
| `reader.frame()` | Frames since the game started |
| `reader.game_id`, `reader.title_id` | The running game's id, as in the file's name |
| `reader.build_id` | See "The build" |
| `reader.base` | Where the main program was loaded on a console that loads it at a place of its own (PSP); nil elsewhere |
| `reader.picture()` | Width and height of the game's picture in pixels; nothing before one was shown |
| `reader.emit{…}` | Text is on screen (below) |
| `reader.layout(window, {…})` | Where that text is drawn (below) |
| `reader.cursor(index [, "horizontal"])` | Which answer of the choice the game's cursor is on, from 0; -1 on none yet; `reader.cursor(nil)` when there is no choice |
| `reader.changed(slot, value)` | True when `value` differs from the last one passed for `slot`, and remembers it. The first value is a change unless it is nil |
| `reader.forget(slot)`, `reader.forget()` | Forget one slot's value, or all |
| `reader.builder()` | Builds a text with furigana: `b:add(s)`, `b:add_ruby(base, reading)`, then `b:text()` and `b:ruby()` for `emit` |
| `reader.data(name)` | The file `<id>.<name>` as a string, or nil |
| `reader.log(…)`, `reader.warn(…)` | To the host's log. `print` is `reader.log` |
| `reader.action(label, f)`, `reader.progress{…}` | Accepted and left alone (read-only) |

### `reader.emit`

Emit a text **when the game shows it, once**. Not before the player sees it, and not again while it stays up:
hosts count what was read. A text that types out is emitted when it starts, whole, if the game holds it whole.

| Field | |
|---|---|
| `text` | Required. UTF-8, lines separated by `\n`, without furigana readings and without the game's codes. An empty text emits nothing |
| `speaker` | The name the game shows, if any |
| `channel` | `"dialogue"` (default) or `"choice"`: a choice's answers, one per line |
| `id` | What this text is in the game, the same every time it is shown: best a hash of its bytes (`text.hash`). The same id again means the player is rereading. Default: a hash of channel, speaker and text |
| `ruby` | The game's own furigana: `{ {start, length, reading}, … }`, counted in code points of `text` from 0. `reader.builder()` keeps the counts right |
| `window` | A name or number for what the text is shown in. Needed for `reader.layout`; a later text in the same window takes this one's place |
| `meta` | A flat table of strings, numbers and booleans, kept with the event and not shown |

### `reader.layout`

Says where the text last emitted for `window` is drawn, so a player can point at a word on the picture.

```lua
reader.layout("message", {
    space  = {640, 480},                       -- the coordinates' own width and height
    box    = {32, 320, 576, 128},              -- the window: x, y, width, height (optional)
    glyphs = { {48, 336, 24, 24}, {72, 336, 24, 24}, false, {48, 364, 24, 24} },
})
```

- `glyphs` has one entry for each code point of the text, in order: its cell, or `false` where nothing is drawn (a
  line break). Give the cell the game steps its pen by, not the glyph's tight outline.
- `space`: give it. Without it the size is the platform's: 1280×720 on `ps1`, `ps2`, `psp` and `dc` and
  400×240 (320×240 below) on `3ds`, as Furoshiki has them; the console's own pixels on the others (`gc` and `wii`
  640×480, `nds` 256×192 on both screens, `gba` 240×160, `sfc` 256×224, `gb` and `gbc` 160×144). On a platform
  not listed here a layout without `space` is an error. *Open: Furoshiki's size for the PSP is taken to be that
  of "the PlayStations", and its DS size isn't known.*
- `screen = "bottom"` on a console with two screens; `"top"` is the default.
- Report a layout again when it changes, not every frame. Close it with `reader.layout(window, nil)` when the
  window closes or shows something else.
- It returns true when taken, false when no text was emitted for that window.

A memory-only script often can't know the cells: the game works them out as it draws. Then it says
`-- reader: no layout <why>`, and the host falls back on what it does without one.

## `mem`

Addresses are the game's own, as its code uses them. Reads never fail hard: where memory isn't there, a number is
nil and a string is shorter, or empty.

| | |
|---|---|
| `mem.u8(a)`, `mem.u16(a)`, `mem.u32(a)`, `mem.u64(a)`, `mem.s32(a)`, `mem.f32(a)`, `mem.f64(a)` | Little-endian. nil unless every byte is there |
| `mem.u16be(a)`, `mem.u32be(a)`, `mem.u64be(a)`, `mem.s16be(a)`, `mem.s32be(a)`, `mem.f32be(a)`, `mem.f64be(a)` | **Added.** The same, big-endian |
| `mem.s8(a)`, `mem.s16(a)` | **Added.** Signed, little-endian |
| `mem.w8`, `mem.w16`, `mem.w32`, `mem.w64` | Always an error (read-only) |
| `mem.read(a, n)` | Up to `n` bytes as a string (at most 1 MB) |
| `mem.cstr(a [, limit])` | The bytes before the first zero byte (limit 4096 unless given) |
| `mem.wstr(a [, limit])` | The bytes before the first zero 16-bit unit |
| `mem.region(name)` | **Added.** A region by name, or nil (below) |

A script picks the reads that fit its console; nothing is swapped for it, and `mem.cstr` and `mem.read` give
bytes as they lie in memory.

### Address spaces

| Platform | Main memory | |
|---|---|---|
| `ps1` | `0x80000000`, 2 MB | The mirrors at `0x00000000` and `0xA0000000` read the same |
| `ps2` | `0x00000000`, 32 MB | |
| `psp` | `0x08000000`, up to 64 MB | |
| `gc` | `0x80000000`, 24 MB | Big-endian |
| `wii` | `0x80000000`, 24 MB, and `0x90000000`, 64 MB | Big-endian |
| `nds` | `0x02000000`, 4 MB | |
| `gba` | `0x02000000`, 256 KB, and `0x03000000`, 32 KB | |
| `sfc` | `0x7E0000`, 128 KB | |
| `gb`, `gbc` | `0xC000`, 8 KB | |

A host maps these onto what its emulator gives out (a libretro core's memory descriptors, or its system RAM at
the base above). Other platforms are added here as their first script arrives.

### Regions

Memory that has no address the game's code would use, or that the host reads another way:

```lua
local vram = mem.region("vram")       -- nil where the host doesn't give it out
local tile = vram.u16(0x5800 + 2 * cell)
```

A region has `size` and the reads of `mem` (`u8` … `read`, the big-endian ones too), taking an offset from its
start. `vram` is laid out per console as `docs/inbox.md` says for a snapshot's `vram` part. A script that can't
do without it says `-- reader: needs vram`; one that can do something without it tests for nil.

## `text`

Each takes a string of bytes and gives UTF-8. What doesn't decode is U+FFFD.

| | |
|---|---|
| `text.sjis(bytes)` | Shift-JIS as Windows reads it (cp932) |
| `text.eucjp(bytes)` | EUC-JP |
| `text.utf16le(bytes)`, `text.utf16be(bytes)` | UTF-16 |
| `text.hash(bytes)` | 16 hex digits (64-bit FNV-1a), for `id` |
| `text.len(s)` | Length in code points |

Lua's own `string`, `table`, `math`, `utf8` and `coroutine` are there. A game's own codes, its tags and its
furigana marks are the script's to take out, with those.

## Limits

A script can't open files, reach the network or start anything; `load` takes source text only; `io`, `os`,
`package` and `debug` aren't there.

| | Floor every host gives | Furoshiki |
|---|---|---|
| Lua instructions while loading | 100 million | 500 million |
| Lua instructions in one call of a function | 2 million | 10 million |
| Memory the script holds | 32 MB | 128 MB |

A script written to the floor runs everywhere. Going over ends that call with an error the script's own `pcall`
can't catch; a function that fails five times running is switched off, and the host says so. *The floor is a
first guess, to be measured on a handheld running a GameCube game before it is fixed.*

## What a host provides

- Reads of the game's memory by address, as in "Address spaces", and the named regions it has.
- A tick between frames, with the frame count.
- **A call after a savestate was loaded,** before the next tick. A host that has savestates must make it: the
  script's `on_state_loaded` functions run, and every layout is closed. Without it a script goes on believing
  the line of the moment before the load is on screen.
- The game's id, and its build hash where it can work one out.
- The script's source, and its data files by name.
- A place for what comes out: emitted texts, layouts, the cursor, log lines.

It doesn't provide writes, controller presses or anything of the device.

A host names the platform as this catalog does (`docs/format.md`). Where its own name differs it maps it:
Genkan and RomM say `ngc` for the GameCube, the catalog says `gc`.

The interface is `runtime/include/yomikata_reader.h`; `runtime/README.md` says how to build and embed it.

## Running on Furoshiki

A script that uses only what Furoshiki has runs there as it is. The additions:

- The big-endian and signed reads can be written in Lua over `mem.read` and `string.unpack`. The build puts those
  few lines in front of a script that uses them, each as `mem.u32be = mem.u32be or function …`, so a published
  script brings its own.
- `mem.region` is nil there, which a script must expect anyway; one that says `needs vram` doesn't run.
- Furoshiki has no GameCube, Wii or cartridge consoles today, so this matters first for PS1, PS2, PSP and DS.

## Checking a script

- `yomikata run games/<platform>/<id>.lua <snapshots>` runs a script with the same runtime the apps use, over
  inbox snapshots (`docs/inbox.md`): memory, and video memory as the region `vram` where a snapshot has it. Each
  snapshot gets a reader of its own and a couple of ticks, and what the script emitted is printed as JSON
  lines. That is game text: for a terminal or a scratch folder.
- A snapshot is one moment. What a script does over time (a page turning, a line typing) is checked on a
  recording from `yomikata replay`: `--sequence` takes the snapshots as consecutive moments of one run.
- **The same memory gives the same events.** A script has no clock, and `math.random` and the order `pairs` walks
  a table of strings in are the same in every run, so two runs over one recording must emit the same events, byte for byte; a script that doesn't is wrong
  (it keeps something across a savestate, or reads memory it shouldn't). The runtime's own tests hold it to this.
- Still to come: counts against the lines a reader saw (`yomikata check` for scripts), and CI's checks of form:
  the script parses; the header lines are there; no `reader.hook` and no writes; a build check exists; against
  empty memory it stops with its "another build" error, inside the limits.

## Notes: Furoshiki's engine and tables

From reading `shared/reader/core` in Furoshiki (GPL-2.0-or-later) and one `.charset` file, for the runtime's plan:

- `lua_api.cpp` (about 950 lines) is the Lua side: the sandbox, the limits, `reader`, `mem`, `text`. About three
  quarters of it is what this page keeps; hooks, registers and writes are about a tenth. It reaches the game only
  through the engine, never the emulator.
- `engine.cpp` (about 900 lines) loads scripts, runs frame functions and stores emits and layouts. About a fifth is
  hooks. Its one tie to the rest of the app is an audio position taken at each emit.
- `text_codec.cpp` (about 250 lines) and its Shift-JIS table are the decoders.
- All of it sits behind a small `Host` class: read memory, list regions, write, log. Only C++20 was seen in these
  files, and Lua is built as C++ so its errors are exceptions.
- So starting the runtime from `lua_api.cpp` and the decoders, with a smaller engine written for it, looks
  practical, and keeps `emit`, `changed`, ids and limits behaving the same. Not read yet: `types.h`, `console.h`,
  `registers.h` and the build files.
- A `.charset` is plain text the script parses itself; `reader.data` only hands it over. So a Thingy `.tbl` works
  the same way with a few lines of Lua in `lib/`, and the catalog's tables stay as they are.

## Open questions

1. `build_id`: which hash Furoshiki uses per console.
2. 3DS ids: title id or product code.
3. Whether Furoshiki's checker accepts header lines it doesn't know.
4. Default `space`: Furoshiki's sizes for the PSP and the DS.
5. The limits' floor, once measured.
6. Where the built scripts are published, and what `index.json` says about them.
