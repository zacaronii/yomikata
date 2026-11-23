# Roadmap

1. **M0:** format, schema, tools (identify, validate, index), CI, and the first hand-made entries.
2. **M1:** sort a whole PS1 library: every game gets an entry, at least its `encoding`. Disc files are scanned for
   runs of Shift-JIS / UTF-16 / UTF-8 Japanese (ISO 9660 files only; streamed XA audio/video is skipped). Because
   many games keep their script compressed on disc, a memory sample from a reader during play (Genkan) can confirm
   or upgrade the classification.
3. **M2:** tables built from the game's font: find the font (TIM or raw 2/4-bit tile sheets, 12×12 or 16×16), cut
   it into glyphs, and match each glyph against Japanese fonts rendered at the same size (image matching, which is
   more reliable than OCR on clean bitmaps; OCR only breaks ties). The kana come from relative search and shared
   tables. Validation decodes the script and checks that it segments into dictionary words and that control codes
   fall at line ends. A review sheet (glyph, guess, confidence) lets a person confirm a table quickly; doubtful
   glyphs stay `partial`, never guesses.
   So far the matching is done by a person: `yomikata font` cuts the font out of video memory (tiles on cartridge
   consoles, a grid in a PS1 texture) into a sheet with each glyph's code, and writes the table's skeleton, filled
   where `yomikata where` already found a pair. Matching glyphs by machine was tried on two games and misread
   too many kanji to trust.
4. **M3:** PSP hook profiles move into the catalog; DS and 3DS sorting (PS2 and GameCube when there are games to test).
5. **M4:** tables learned by readers during play (OCR text paired with the bytes in memory) arrive as pull requests.
6. **M5:** layouts, for reading depth without hooks: where each game keeps its message, speaker, how much is shown,
   and its choices (`layouts`, docs/format.md). Found with `locate`, snapshots and `replay`, and checked with
   `yomikata layout` against a snapshot before a PR. For text kept as tiles, `yomikata where` (from the frame), `yomikata font` and
   `yomikata propose` do the finding from a few snapshots with video memory; `yomikata check` counts the result.
