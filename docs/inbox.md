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
  PSP `0x08000000`–`0x09FFFFFF` …). A match elsewhere, such as the BIOS ROM, is a coincidence, so it's refused.
- Send the whole file again whenever it grows: samples already stored are recognised and skipped
  (answer: `{"ok": true, "id": …, "added": 3, "stored": 41}`).
- `exe_sha1` may be empty: an inbox with the library mounted identifies the release from the serial and fills it in.
- `GET /api/samples` lists what's stored per game (count, last upload, whether the exe hash is known).
- Errors: `400` with the reason, `401` without the token, `413` when too large.

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
