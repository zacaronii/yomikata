# Formats

## Identity

An entry is keyed by **release**, not title: the id the hardware or emulator reports.

| Platform | id | Where it comes from |
|---|---|---|
| PS1, PS2 | `SLPS-01234` | `SYSTEM.CNF`: `BOOT = cdrom:\SLPS_012.34;1` (`BOOT2` on PS2) |
| PSP | `ULJM06119` | `UMD_DATA.BIN` / `PARAM.SFO` `DISC_ID` (what PPSSPP reports) |
| GameCube | `GXXJ01` | first 6 bytes of the disc header |
| DS | `AXXJ` | game code in the cartridge header (0x0C) |
| 3DS | `CTR-P-AXXJ` | product code in the NCCH header (0x150) |

`yomikata identify <image>` prints all of this for CHD, cue/bin and ISO images and DS/3DS card images.

**Multi-disc games:** each disc has its own serial. One entry covers the game; the other discs' serials go in
`aliases`, and `versions[].id` says which disc a hash belongs to.

**Versions:** `versions` lists every release the entry is known to fit, by the SHA-1 of the boot executable (discs)
or a CRC-32 of the header (cartridges). A client that finds no match should warn, not guess.

## Entry (`games/<platform>/<id>.json`)

| Field | |
|---|---|
| `id`, `aliases` | The release id; other ids sharing the entry |
| `platform` | `ps1`, `ps2`, `psp`, `nds`, `3ds`, `gc`, … (see the schema) |
| `title`, `title_romaji` | As printed on the release; romanised for searching |
| `versions` | `{exe, exe_sha1, id?, note?}` or `{header_crc32, …}` |
| `encoding` | `shift_jis`, `utf16le`, `utf8`, `table`, `compressed` (script only compressed on disc; read from memory), `unknown` |
| `tables` | Thingy files applied in order; later ones win |
| `unit` | Bytes per code for table decoding (1 or 2); required with `tables` |
| `controls` | Hex code (memory byte order) → `end`, `newline`, `wait`, `clear`, `page`, `name`, `color`, `pause`, `choice`, `ignore` |
| `cleanup` | In order: `{regex, replace}` or `{remap: {from: to}}`. Declarative only, so any language can apply it |
| `hooks` | `{address, register, charset, versions?, cleanup?}`: code addresses each line passes through (PSP via PPSSPP) |
| `layouts` | Where the game keeps what it shows, for readers that read memory instead of hooking (see Layouts) |
| `status` | `verified` (confirmed in play), `auto` (built by tools, passed validation), `partial` (known gaps) |
| `coverage` | `{chars_in_table, chars_seen_in_script}` |
| `sources` | Where it came from, with credit |
| `notes` | Short; no script text |

An entry that only says `"encoding": "shift_jis"` is still useful: readers can skip guessing.

## Cleanup

`cleanup` is applied in order to each decoded line (a hook's or a layout text's own list first, then the entry's),
so every client gets the same text:

- `{regex, replace}` replaces every match. Keep patterns to what Java, Python and JavaScript all read: classes,
  groups, lazy quantifiers, and the inline flags `(?m)` and `(?s)` only at the very start (JavaScript clients turn
  them into flags). No lookbehind and no named groups. In `replace`, `$1`…`$9` insert a group (Python clients
  convert them to `\1`), and `$$` is a literal `$`.
- `{remap: {from: to}}` replaces single characters, e.g. half-width katakana a game uses for hiragana.
- A line that is empty after cleanup is **dropped**. Use this to filter: a hook that also sees lines another hook
  already reports can clean those to nothing.

Reference implementation: `tools/src/yomikata/cleanup.py` (`clean_line(text, entry, hook)`), tested against the
catalog's own entries, so clients can compare their output with it.

## Layouts

A hook needs the emulator to stop the game at an instruction; most stock emulators can't do that. But much of
what a reader wants is kept in memory anyway: the message the window shows (often whole, before it types out),
how much of it is on screen, a flag while the game waits for a press, a choice's answers. A layout says where.
Every field is optional, and a layout with only `message` is already useful.

```json
"layouts": [{
  "poll": 20,
  "message": {"address": "0x08A10004", "charset": "utf16le", "max_bytes": 540,
              "terminator": "0000", "rows": {"count": 5, "stride": 108}},
  "speaker": {"address": "0x08A10002", "type": "u16"},
  "waiting": {"base": "0x08B00000", "offsets": ["0x2C"]}
}]
```

(A made-up example: a message of five UTF-16 rows, a speaker number, and a flag in an object the game allocates.)

| Field | |
|---|---|
| `versions` | `exe_sha1` values the layout was confirmed on; empty = every listed version. A reader picks the layout naming its version, else one naming none (see below) |
| `endian` | Of pointers and numbers: `little` (default) or `big` (GameCube, Wii) |
| `poll` | How often reading is enough: `"frame"`, or a rate in Hz |
| `message` | *text*: what the message window shows |
| `speaker` | *text* (the name), or *number* with `speaker_names` (`{"3": "苗木"}`, names only) for name plates that are pictures |
| `shown` | *number*: how many characters of the message are on screen |
| `waiting`, `choosing` | *flag*: the line is complete and waits for a press; a choice is up |
| `choices`, `choice_count`, `choice_cursor` | *text* with one answer per row; how many rows count; which answer (from 0) the cursor is on |
| `when` | *flag*: the addresses hold what the layout says (see below) |
| `notes` | Short; no script text |

Some games use the same memory for something else outside their script: a movie's frames, the title screen. A
layout's own `when` is true only while its addresses mean what it says. While it is false a reader reads
nothing else: every *text* is empty, `waiting` and `choosing` are false, and the numbers (`shown`,
`choice_count`, `choice_cursor`, a speaker number) are left out. If `when` can't be read, nothing else is read
either, and `when` is the field that failed.

Every *number*, *flag* and *text* says where it is in one of two ways:

- `"address": "0x…"`: a fixed address;
- `"base": "0x…", "offsets": ["0x8", "0x14"]`: a pointer chain, for anything the game allocates. Start at
  `base`; for each offset, read the 32-bit pointer there and add the offset. So `{base: B, offsets: [0x8]}` is
  "the pointer at B, plus 8".

A *number* has a `type` (`u8` default, `u16`, `u32`, `s8`, `s16`, `s32`). As a *flag* it is true when nonzero, or
when it `equals` a given value.

A reader that finds a key or a named value (a `charset`, a `type`) it doesn't know anywhere in a layout treats
that layout as not there: a newer key can change what the others mean (a text without its `when` would show
what is no longer on screen).

**Which layout:** a reader that knows the executable's SHA-1 picks the layout naming it, else the first one naming
no versions. A reader that can't hash the executable (one that only sees the game's memory) still knows the release
id. If the entry's `versions` list exactly one version for that id (a version without `id` belongs to the entry's
own), the reader takes that version's `exe_sha1` as its own and picks the same way. If the id has several versions,
or none, only a layout naming no versions fits. Taking the only listed version is a guess: an unlisted revision
of the same release would get a layout made for another one. Listing that revision in `versions` ends the guess.

A *text* has:
- a `charset`: `shift_jis`, `euc_jp`, `utf16le`, `utf16be`, `utf8`, or `table` (the entry's `tables`, `unit` and
  `controls`);
- `max_bytes`, which a reader never reads past;
- optionally `terminator` (hex in memory byte order, matched only at character boundaries), `length` (a *number*
  holding the length), and `rows: {count, stride}` for a buffer of rows, each read to its terminator and joined
  with newlines;
- optionally `rows.lengths`, for a game that keeps one length per row: a *number* whose address is the first
  row's length, with the next rows' lengths right after it (a `u16` for row 2 is 4 bytes on);
- optionally `when`, a *flag* that is true while the text is on screen. Many games leave the last text in memory
  after its window closes; while `when` is false the text is empty;
- its own `cleanup`, applied before the entry's.

A length counts bytes unless it says `"counts": "characters"`. Games that write a new line over the old one
without ending it need a length to tell where the new line stops.

### Reading a text

Every client should get the same string from the same bytes, so the steps are fixed:

1. With `when`: read the flag. If it is false the text is the empty string, and nothing else is read. If it
   can't be read, the field fails.
2. Read `max_bytes` bytes at the address (`stride` bytes if that is less; with `rows`, once per row, `stride`
   apart). A length in bytes shortens the read; a length below zero is 0. If a read fails, the whole field fails,
   and so it does if a length can't be read.
3. Split the bytes into characters by the lengths below. Stop at the `terminator` where it stands at the start of
   a character, and drop a last character that doesn't fit in what was read. A length in characters stops the
   split after that many (a `table` code is one character here, whatever it decodes to). `length` applies to
   every row and `rows.lengths` to its own row; where both are given, both hold.
4. Decode each character on its own. One that doesn't decode is one U+FFFD, however many bytes it has.
5. With `rows`: drop empty rows from the end, and join the rest with a newline.
6. Apply the text's `cleanup`, then the entry's.

| Charset | Bytes in a character, from its first byte | Decoded as |
|---|---|---|
| `shift_jis` | 2 for `81`–`9F` and `E0`–`FC`, else 1 | Windows' Shift-JIS (cp932): `8160` is U+FF5E, the user-defined area `F040`–`F9FC` is U+E000–U+E757, `5C` is a backslash. Single bytes `A1`–`DF` are half-width kana |
| `euc_jp` | 3 for `8F`, 2 for `8E` and up, else 1 | JIS X 0208, `8E` + half-width kana, `8F` + JIS X 0212. Vendor rows (NEC's row 13, IBM's extensions) aren't defined yet |
| `utf8` | 4 for `F0` and up, 3 for `E0` and up, 2 for `C0` and up, else 1 | Strict UTF-8: an overlong form or an encoded surrogate doesn't decode |
| `utf16le`, `utf16be` | 4 for a high surrogate followed by a low one when both fit, else 2 | A surrogate on its own doesn't decode |
| `table` | the entry's `unit` | The table's character. An end code, or a code `controls` calls `page` or `clear`, is a blank line; a newline code is a line break; any other control is nothing; a code the table lacks is U+FFFD. Line breaks at the end are dropped |

Any other single byte from `80` up is U+FFFD. Python's `cp932` and `euc_jp` codecs are the reference for the
mappings.

Eight `euc_jp` codes come out differently from decoders that follow Windows (Java's and Android's among them).
A reader fixes them by code before it asks its decoder:

| Code | Character | Code | Character |
|---|---|---|---|
| `A1BD` | U+2015 ― | `A1DD` | U+2212 − |
| `A1C0` | U+FF3C ＼ | `A1F1` | U+00A2 ¢ |
| `A1C1` | U+301C 〜 | `A1F2` | U+00A3 £ |
| `A1C2` | U+2016 ‖ | `A2CC` | U+00AC ¬ |

What a reader derives from the fields:

- `choices`: the text's rows, the first `choice_count` of them (none if it is below zero; all if it can't be
  read). An empty text is no answers.
- What is on screen: the message without its newlines, the first `shown` characters (code points; none if it is
  below zero).
- A `speaker` number is the name `speaker_names` gives it, else the number.
- A field that can't be read is left out; the others are still read.

Reference implementation: `tools/src/yomikata/layout.py` (`read_layout(layout, entry, read)`). To check a layout
against a RAM snapshot before a PR, run `yomikata layout games/<platform>/<id>.json <sha1>.ram.gz`; it prints what
each field reads (the output is script text, so keep it out of the repo).

Clients check themselves against `tools/tests/layout-vectors/`: each file is a layout, some memory, and what
`read_layout` gives (see its README).

## Table (`tables/…/*.tbl`)

Thingy format, UTF-8, hex keys in **memory byte order** (so no separate endianness setting is needed):

```
0100=学
0101=校
/FFFF
*FFFE
```

`/` marks an end-of-string code and `*` a newline code. Values are a character or a few characters; the validator
refuses longer values, because tables map codes, they don't hold text. Pieces shared between games (one developer's
kana order) go in their own file, e.g. `tables/human-kana.tbl`, listed before the game's own table.
