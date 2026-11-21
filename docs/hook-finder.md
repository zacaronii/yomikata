# Finding a hook

A **hook** is the place in a game's code that every line of dialogue passes through, plus the register that holds a
pointer to the line when it gets there. A reader that sets a breakpoint there gets each line as text the moment the
game shows it, with no OCR and no memory searching. Hooks are per release *and* per version: the same disc id can
need a different address after a patch or a reissue.

Hooks can only be found while the game runs, so this is something a reader app does during play. Any app can
implement it (Genkan does). This page is the method and the report format, so results from any app land in the
catalog the same way.

## Method

The steps assume an emulator with a scriptable debugger (PPSSPP's WebSocket debugger, for PSP games): read memory,
set memory and execution breakpoints, read registers, step.

1. **Wait for a line of dialogue on screen** and read it with OCR.
2. **Find the line in memory:** search RAM for it in the game's encoding (or with the entry's table). It's usually in
   more than one place, e.g. the script buffer, a copy for the text box, and the glyph renderer's queue.
3. **Watch reads:** put a memory *read* breakpoint on each copy and advance to the next line. Note every instruction
   that reads the new line's text.
4. **Walk back to the function entry** for each instruction, set an execution breakpoint there, and on the next
   line note which argument register (`a0`–`a3`, or `t0` and so on) points at the start of the line.
5. **Keep only real candidates.** Over **three or more consecutive lines**:
   - the same entry point fires once per line, not hundreds of times per frame (memory copies and renderers fire
     for everything, and are rejected);
   - the register points at the start of the new line each time.
6. **Check it before reporting:** read the next **5 to 10 lines** through the candidate hook only and compare each
   with OCR, after cleanup (`docs/format.md`; the tools include a reference implementation). Report the match rate.
   If lines carry tags (ruby, colour codes), add `cleanup` rules so the hook's text equals what's on screen.

## Report

Send one report per hook to an inbox (`POST /api/hooks`, see `docs/inbox.md`), or turn it into an entry by hand
(`CONTRIBUTING.md`):

```json
{
  "id": "NPJH50372", "platform": "psp", "disc_version": "1.03", "title": "ダンガンロンパ",
  "exe_sha1": "",
  "hook": {"address": "0x08812ABC", "register": "a1", "charset": "utf16le", "cleanup": []},
  "evidence": {"lines_checked": 8, "lines_matched": 8,
               "pairs": [{"ocr": "line OCR read", "hook": "line the hook gave"}]},
  "reporter": {"app": "Genkan", "version": "…"}, "emulator": {"name": "PPSSPP", "version": "…"},
  "notes": ""
}
```

| Field | |
|---|---|
| `id`, `platform` | The release id the emulator reports (PSP: `DISC_ID`) |
| `disc_version` | PSP `PARAM.SFO` `DISC_VERSION` (`1.03`); distinguishes printings with the same id |
| `exe_sha1` | Optional: SHA-1 of the boot executable as stored on disc (`EBOOT.BIN`). An inbox with the library fills it in |
| `hook` | `address`, `register`, `charset` (`shift_jis`, `utf16le`, `utf8`, `table`), optional `cleanup` |
| `evidence` | `lines_checked`, `lines_matched` against OCR; up to 20 short `pairs` for review |
| `reporter`, `emulator` | Which app and emulator found it |

`pairs` contain script text: an inbox keeps them, and they are never written into the catalog. `yomikata hooks`
turns stored reports into entries: by default only with at least 5 lines checked and 80% matching. A new game gets a
new entry (status `auto`); an existing entry gains the hook, or the version in an existing hook's `versions`.
