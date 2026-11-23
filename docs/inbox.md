# Sample inbox

Readers that pull text from emulator memory can upload **memory samples** to an inbox instead of handing files over
by hand. A sample pairs a line the reader OCR'd with the bytes found in memory at that moment; a handful of them
pins down a game's byte order and kana order when its script is compressed on disc (see `docs/roadmap.md`, M1/M4).

Samples contain script text, so they live in the inbox's own data folder, **never in this repository**. Tables
built from them come back as ordinary pull requests.

## Uploading (readers)

`POST /api/samples` with `Authorization: Bearer <token>` and one game's samples as JSON (at most 4 MB, 500 samples):

```json
{
  "id": "SLPS-01001", "platform": "ps1", "title": "ムーンライトシンドローム", "genkan_version": "…",
  "exe_sha1": "",
  "samples": [
    {"ocr": "the line OCR read", "bytes_hex": "…256 bytes either side…", "address": "0x801A2B40",
     "unit": 2, "byte_order": "little", "probe": "repeat", "context_start": "0x801A2A40"}
  ]
}
```

- `id`, `platform`, `samples[].ocr` and `samples[].bytes_hex` are required; other small fields are kept as sent.
- `address` must be in the console's main RAM (PS1 `0x00000000`–`0x001FFFFF` and its `0x80…`/`0xA0…` mirrors,
  PSP `0x08000000`–`0x09FFFFFF`, Super Famicom work RAM `0x7E0000`–`0x7FFFFF`, Game Boy Advance `0x02000000`–`0x0203FFFF` and `0x03000000`–`0x03007FFF`, Game Boy `0xC000`–`0xDFFF`, DS `0x02000000`–`0x023FFFFF`). A match elsewhere, such as the BIOS ROM, is a coincidence, so it's refused.
- Send the whole file again whenever it grows: samples already stored are recognised and skipped
  (answer: `{"ok": true, "id": …, "added": 3, "stored": 41}`).
- `exe_sha1` may be empty: an inbox with the library mounted identifies the release from the serial and fills it in.
- `GET /api/samples` lists what's stored per game (count, last upload, whether the exe hash is known).
- Errors: `400` with the reason, `401` without the token, `413` when too large.

## RAM snapshots (readers)

When matching a line on the device is unreliable (short lines, unknown tables), a reader can send the **whole main
RAM** taken while a line is on screen, and let the inbox's side do the searching: `POST /api/snapshots`, same token,
`multipart/form-data` with two parts:

| Part | |
|---|---|
| `meta` | JSON: `id`, `platform` (`ps1`, `ps2`, `psp`, `sfc`, `gba`, `gb`, `gbc`, `nds`), `base_address` (where the dump starts, e.g. `0x80000000` for PS1, `0x08000000` for PSP, `0x7E0000` for Super Famicom; one stretch of RAM per snapshot), `ocr` (the line or lines on screen, 1-10; or `"no_text": true` and no lines, see below), and optionally `vram_format` (a short name for how the `vram` part is laid out, e.g. `ps1-15bit`), `title`, `exe_sha1`, `captured_at`, `app`, `emulator`, `note` |
| `ram` | The raw RAM bytes (PS1: 2 MB; PSP: up to 32 MB; Super Famicom: the 128 KB of work RAM; Game Boy Advance: 256 KB; Game Boy: 8 KB; DS: 4 MB), optionally gzipped |
| `state` | Optional: the emulator's savestate (libretro `retro_serialize`), gzipped, up to 64 MB once unpacked (PCSX ReARMed: 4 MB, PPSSPP: 40 MB). On PCSX ReARMed it holds the 1 MB of video memory, which `yomikata vram` extracts |
| `vram` | Optional: the video memory itself, when the core gives it out (PS1: 1 MB; Super Famicom: 64 KB, which holds the text font's tiles; Game Boy Advance: 96 KB; Game Boy: 8 KB, Color 16 KB; DS: 656 KB). More than the console has is refused |
| `screen` | Optional: the frame on screen as a PNG, up to 4 MB |

Upload at most 80 MB. Other parts are ignored.

- The dump must lie inside main RAM (`base_address` + size), as with sample addresses.
- Answers: `{"ok": true, "id": …, "added": true, "sha1": …, "stored": 3}`. The same RAM sent again gives
  `"added": false`. `400` with the reason, `401`, `413`.
- Snapshots hold script text: they're kept gzipped in the inbox (the newest 40 per game), never in the catalog.
- `GET /api/snapshots` lists them per game (count, lines, how many carry each optional part, last upload).
- Why the extras: some games never hold a line as codes in RAM (it's decompressed briefly, or drawn straight to video
  memory). Then the font in video memory and the frame on screen are what tie glyphs to characters.
- Two or three snapshots taken at different lines of the same scene are far more useful than one: each line is a
  separate constraint when working out a game's own table.
- A snapshot of a screen **with no text on it** (the window closed, a scene change) is worth sending beside
  them: what differs from the snapshots with a line shows where the game keeps "the window is open", so a layout
  can say when the text in memory is no longer on screen. Send it with `"no_text": true` and an empty `ocr`. An
  empty `ocr` without it is refused, so a screen OCR failed on is never stored as an empty one.
- `yomikata where <snapshots>` says what holds the lines. For a standard encoding: the places, and which is the
  same in every snapshot. For a game's own codes it searches by pattern of repeated characters, and then puts each
  place to the test: with the code=character pairs a place gives, are the *other* lines found too, in codes too
  rare in that memory to line up by chance? Places that pass are listed (address, cell size, kept loaded like a
  script or only there while on screen, two-tile characters with the `rows` numbers for a layout, copies with
  attribute bits); the rest are counted as likely chance. So it needs snapshots of two or more different lines
  that share some characters. `--absent <snapshots without the text>` drops loaded script; `--table` adds the
  pairs it found.
- For a game in a standard encoding, `yomikata locate <snapshots>` goes one step further: when a place in memory was
  seen to change from one line to another, it prints that buffer as a layout's `message` block (address, rows and
  stride if the window is stored row by row) and reads it back against each snapshot's OCR. Three snapshots of
  different lines, plus one with no window on screen, are enough; one line alone can't tell the window's buffer
  from the script and backlog copies a game also keeps.

## Hook reports (readers)

`POST /api/hooks` with the same token: one hook a reader found and checked in play, in the report format of
[hook-finder.md](hook-finder.md) (at most 256 KB). A report for the same address, register and disc version
replaces the earlier one (`{"ok": true, "id": …, "replaced": true, "reports": 1}`). `GET /api/hooks` lists them.
Whoever runs the inbox turns them into catalog entries with `yomikata hooks <data>/hooks` and opens a pull request.

## Running one

The tools image runs it: `yomikata serve` (port 8080), configured by environment variables.

| Variable | |
|---|---|
| `YOMIKATA_INBOX_TOKEN` | Required. The bearer token readers send |
| `YOMIKATA_DATA` | Where samples are kept (default `/data`) |
| `YOMIKATA_LIBRARY` | Optional, read-only: a RomM-style library (`psx/`, `psp/`, `nds/`, `3ds/` folders) used to fill in exe hashes |
| `YOMIKATA_SCRATCH` | Optional: where CHDs are unpacked while identifying (deleted after each) |

```yaml
services:
  yomikata:
    image: ghcr.io/<you>/yomikata-tools:latest
    command: ["serve"]
    environment:
      YOMIKATA_INBOX_TOKEN: ${YOMIKATA_INBOX_TOKEN}
      YOMIKATA_LIBRARY: /library
      YOMIKATA_SCRATCH: /data/scratch
    volumes:
      - ./data:/data
      - /path/to/romm_library/roms:/library:ro
```

Keep it on your own network (or behind a VPN): the token is the only protection.
