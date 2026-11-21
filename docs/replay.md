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

First use (Persona 2, SLPS-02100): a Genkan state loaded and replayed pixel-identical to the device's screenshot;
recording 150 frames after pressing confirm showed the character card typing at one character per 4 frames.
