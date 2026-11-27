# Formats

## Identity

An entry is keyed by **release**, not title: the id the hardware or emulator reports.

| Platform | id | Where it comes from |
|---|---|---|
| PS1, PS2 | `SLPS-01234` | `SYSTEM.CNF`: `BOOT = cdrom:\SLPS_012.34;1` (`BOOT2` on PS2) |
| PSP | `ULJM06119` | `UMD_DATA.BIN` / `PARAM.SFO` `DISC_ID` (what PPSSPP reports) |
| GameCube | `GXXJ01` | first 6 bytes of the disc header; the build is the id, `-` and the disc's version (0x07) as two hex digits (`G8MJ01-00`), and `header_crc32` the CRC-32 of the header's first 0x80 bytes (an RVZ or WIA image keeps a copy of them at 0x58) |
| Wii | `RXXJ01` | first 6 bytes of the disc header, as for GameCube, the build and `header_crc32` too (the Wii's magic 0x5D1C9EA3 is at 0x18, the GameCube's at 0x1C) |
| DS | `AXXJ` | game code in the cartridge header (0x0C) |
| 3DS | `CTR-P-AXXJ` | product code in the NCCH header (0x150) |
| Super Famicom | `SHVC-AXXJ`, else `SFC-1A2B3C4D` | game code in the cartridge header where there is one, else the header's CRC-32 (see below) |
| GBA | `AGB-AXXJ` | game code in the cartridge header (0xAC), with `AGB-` before it; the build is the CRC-32 of the header's first 192 bytes (0x00–0xBF), 8 lowercase hex digits |
| N64 | `NUS-NAFJ` | the 4-character game code in the cartridge header (0x3B: a kind, two letters, a region), with `NUS-` before it, else `N64-` and the header's CRC-32; the build is the id, `-` and the version (0x3F) as two hex digits (`NUS-NAFJ-00`), and `header_crc32` the CRC-32 of the header's first 0x40 bytes in the cartridge's own order (a .v64 or .n64 dump has its bytes swapped in 16- or 32-bit words; they are put back) |
| Game Boy, Game Boy Color | `CGB-AXXJ` / `DMG-AXXJ`, else `GBC-1A2B3C4D` / `GB-1A2B3C4D` | the 4-character game code at 0x13F when the old maker byte (0x14B) is 0x33, with the console's prefix (Color when bit 7 of 0x143 is set), else the header's CRC-32; the build is the CRC-32 of the header (0x100–0x14F) |
| PC Engine HuCard | `PCE-1A2B3C4D` | the CRC-32 of the ROM, without a 512-byte copier header (a size that leaves 512 when divided by 8192), 8 upper-case hex digits; `header_crc32` the same in lower case |
| PC Engine CD | `PCECD-1A2B3C4D` | the CRC-32 of the data track's second sector (2048 bytes: the boot block, "PC Engine CD-ROM SYSTEM" at 0x20), the data track being the cue's first MODE1/MODE2 track from its INDEX 01 |
| Famicom | `FC-1A2B3C4D` | the CRC-32 of the PRG and CHR ROM as No-Intro counts it: without the 16-byte iNES or NES 2.0 header, and without the 512-byte trainer the header may say follows it; a file with no header is taken as PRG and CHR already. `header_crc32` is the same CRC-32 |
| Famicom Disk System | `FDS-01ZEL`, `FDS-01ZEL-D2` | from the disk-info block that starts each side: the maker's code (0x0F, two hex digits) and the game's name code (0x10, four characters; any not `A`–`Z` or `0`–`9` as `-`, spaces at the end left out), with `-D` and the disk's number from 1 for the second disk of a set or later (0x17); the build is the id, `-` and the revision (0x15) as two hex digits, `header_crc32` the CRC-32 of the first side's 56-byte block. A file with the 16-byte fwNES header (`FDS\x1a`) and one without give the same release |
| Mega Drive | `G-5512`, `MD-1A2B3C4D` | from the product code at 0x180 (`GM G-5512   00`): the serial between the kind and the revision, as Sega's discs are named; a header without one gives `MD-` and the CRC-32 of the whole ROM. The build is the id, `-` and the revision (0x18C, two characters), `header_crc32` the ROM's CRC-32. The header at 0x100 begins `SEGA` (`SEGA MEGA DRIVE`, `SEGA GENESIS`); an SMD dump (a 512-byte header, then 16 KB blocks of odd bytes then even) is put back in order first. Images `.md`, `.gen`, `.smd`, and `.bin` where that header is there. A host may say `genesis` or `megadrive` for the platform |
| Saturn | `GS-9037`, `GS-9037-D1` | the product number in the system ID (the data track's first sector, "SEGA SEGASATURN " at 0x00): 10 bytes at 0x20, spaces trimmed; for a disc of a set (device information at 0x38 reading `CD-n/m`, m above 1), `-D` and n after it, since the discs of a set share the number. The build is the id, `-` and the version (6 bytes at 0x2A: `GS-9037-D1-V1.005`); `header_crc32` the CRC-32 of the sector's first 0x100 bytes |
| Dreamcast | `HDR-0152-D1` | the same from IP.BIN (the first data track's first sector, "SEGA SEGAKATANA " at 0x00): the product number at 0x40, the version at 0x4A, the disc as `GD-ROMn/m` in 0x20–0x2F |

`yomikata identify <image>` prints all of this for CHD, cue/bin and ISO images, DS/3DS card images and Super
Famicom images (`.sfc`, `.smc`, `.swc`, `.fig`, also inside a zip).

**Super Famicom:** the header is the 48 bytes the console sees at 0xFFB0–0xFFDF. In the file:

1. If the file's size leaves 512 when divided by 0x8000, the first 512 bytes are a copier's header: drop them.
2. The header is at 0x7FB0 or at 0xFFB0. Give each place 2 points if its checksum (two bytes at 0x2E of the 48,
   low byte first) and its complement (at 0x2C) add up to 0xFFFF bit for bit (one XOR the other is 0xFFFF), and
   1 point if bit 0 of the map byte (at 0x25) fits the place: 0 at 0x7FB0, 1 at 0xFFB0. The place with more points
   has the header; with the same points, 0x7FB0. A place the file is too short for is left out.
3. `header_crc32` is the CRC-32 of those 48 bytes, as eight lowercase hex digits.
4. If the maker byte (at 0x2A) is 0x33 and the four bytes at 0x02, without the spaces at their end, are two to four
   characters from `A`–`Z` and `0`–`9`, they are the game code and the id is `SHVC-` and the code, whatever the
   region (`SHVC-AXXJ`; early cartridges have two letters). Otherwise the id is `SFC-` and the same CRC-32 as
   eight uppercase hex digits. The CRC-32 takes in the cartridge's own checksum, so it differs between games and
   between revisions of one game.

Clients check themselves against `tools/tests/identify-vectors/sfc.json` (made-up headers).

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
| `tuning` | `{"<libretro core id>": {"step", "measured_on"?}}`: the step a player app should start this game at in that core (see Tuning) |
| `wii_controller` | `"nunchuk"`, `"classic"` or `"sideways"`: the Wii controller the game is played with (see Wii controller) |
| `sources` | Where it came from, with credit |
| `notes` | Short; no script text |

An entry that only says `"encoding": "shift_jis"` is still useful: readers can skip guessing.

## Wii controller

`wii_controller`, like `tuning`, has nothing to do with text. A Wii game is played with the Wiimote and Nunchuk
(`"nunchuk"`, which also plays a game that wants the Wiimote alone), the Classic Controller (`"classic"`), or the
Wiimote held sideways (`"sideways"`). A player app that maps a pad onto a Wii controller sets that one up for the
game; without the field it takes `"nunchuk"`. Only Wii entries use it.

## Tuning

`tuning` has nothing to do with text. Some games need a core set faster or plainer than its default to run at
speed on a handheld, and others can afford better: a player finds the setting once, and the entry keeps it so
that the app starts the game there next time, on any device.

```json
"tuning": {"dolphin": {"step": "balanced", "measured_on": "Some Handheld 2"}}
```

The key is the core's id as the player app names it: lower case, the libretro core's name without `_libretro`
(`dolphin`, `ppsspp`, `melonds`, `bsnes_mercury`, `pcsx_rearmed`, `mgba`). `step` is one of the steps the
player app defines for that core, from its fastest to its best-looking: the names are the app's own, a free
string here, and the catalog doesn't check them against any list.

A step or a core id the player doesn't know isn't an error: it is ignored, and the game starts at the
player's default. A player's steps can be renamed between its versions, so an entry may outlive the step it
names.

`measured_on` is optional: the model of the device the step was found on, exactly as Android reports it
(`Build.MODEL`), and nothing of its owner. A player takes a risky step (one that could make a game unplayable
on a slower device) only on a device whose model is the same; on any other it takes the nearest safe step.

An entry may have `tuning` and nothing else beyond its identity: it needs no encoding work.

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

**No entry in the catalog has layouts any more.** Each game that had them is read by a script
(docs/script-api.md), and the fifteen entries' `layouts` were taken out once the reader app stopped using them;
their other fields stay. An entry's notes may still speak of "the layout": it read the same memory its script
reads now. The format is kept here because the tools still read it (`yomikata layout`, `check`, `propose`,
and `compare`, which sets a script beside a layout), for working out a new game before its script is written.

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
| `versions` | `exe_sha1` values (`header_crc32` for a cartridge) the layout was confirmed on; empty = every listed version. A reader picks the layout naming its version, else one naming none (see below) |
| `endian` | Of pointers and numbers: `little` (default) or `big` (GameCube, Wii) |
| `poll` | How often reading is enough: `"frame"`, or a rate in Hz |
| `message` | *text*: what the message window shows |
| `speaker` | *text* (the name), or *number* with `speaker_names` (`{"3": "苗木"}`, names only) for name plates that are pictures |
| `shown` | *number*: how many characters of the message are on screen |
| `waiting`, `choosing` | *flag*: the line is complete and waits for a press; a choice is up |
| `choices`, `choice_count`, `choice_cursor` | *text* with one answer per row; how many rows count; which answer (from 0) the cursor is on |
| `when` | *flag*: the addresses hold what the layout says (see below) |
| `tables` | `{by, sets}`: table files that follow the entry's, chosen by a *number* (see below) |
| `notes` | Short; no script text |

Some games use the same memory for something else outside their script: a movie's frames, the title screen. A
layout's own `when` is true only while its addresses mean what it says. While it is false a reader reads
nothing else: every *text* is empty, `waiting` and `choosing` are false, and the numbers (`shown`,
`choice_count`, `choice_cursor`, a speaker number) are left out. If `when` can't be read, nothing else is read
either, and `when` is the field that failed.

Some games number their characters anew for each chapter, so no one table fits the whole game. A layout's own
`tables` names the table files that go with what is loaded now: `by` is a *number*, and `sets` gives, for each
value it can have (written in decimal), the files to apply after the entry's `tables`:

```json
"tables": {"by": {"address": "0x80101000", "type": "u32"},
           "sets": {"36": ["tables/ps1/SLPS-00000-one.tbl"], "94": ["tables/ps1/SLPS-00000-two.tbl"]}}
```

Such a layout decodes its `table` texts with the entry's tables and then the set's. When the number has no set,
or can't be read, there is no table: every `table` text fails and is left out, rather than being read with the
wrong characters. The other fields are read as always. `by` can be anything that differs between the chapters;
a count the game keeps of something in the loaded script will do.

Every *number*, *flag* and *text* says where it is in one of two ways:

- `"address": "0x…"`: a fixed address;
- `"base": "0x…", "offsets": ["0x8", "0x14"]`: a pointer chain, for anything the game allocates. Start at
  `base`; for each offset, read the 32-bit pointer there and add the offset. So `{base: B, offsets: [0x8]}` is
  "the pointer at B, plus 8".

A *number* has a `type` (`u8` default, `u16`, `u32`, `s8`, `s16`, `s32`). As a *flag* it is true when nonzero, or
when it `equals` a given value. A flag's `mask` keeps only those bits of the value before either test (the bits as
stored, so the result is never below zero): `"mask": 3` is true at 1, 2 and 3 and false at 0, 4 and 8.

A reader that finds a key or a named value (a `charset`, a `type`) it doesn't know anywhere in a layout treats
that layout as not there: a newer key can change what the others mean (a text without its `when` would show
what is no longer on screen).

**Which layout:** a reader that knows the executable's SHA-1 picks the layouts naming it, else those naming no
versions. A reader that can't hash the executable (one that only sees the game's memory) still knows the release
id. If the entry's `versions` list exactly one version for that id (a version without `id` belongs to the entry's
own), the reader takes that version's `exe_sha1` as its own and picks the same way. If the id has several versions,
or none, only a layout naming no versions fits. Taking the only listed version is a guess: an unlisted revision
of the same release would get a layout made for another one. Listing that revision in `versions` ends the guess.
A cartridge has no executable: its version is its `header_crc32`, which a reader works out from the file and uses
the same way.

Usually that leaves one layout. A game that loads a different program for each chapter keeps its cells at other
addresses in each, and gets one layout per program, each with a `when` that is true only while its program is
the one loaded (a word of the program's code that differs between them will do). A reader takes the layouts
left in the entry's order and, each time it reads, reads the first whose `when` is true; one without `when`
is always true, and one whose `when` can't be read is passed over. If none is true it reads the first, which
gives what a false `when` gives.

A *text* has:
- a `charset`: `shift_jis`, `euc_jp`, `utf16le`, `utf16be`, `utf8`, or `table` (the entry's `tables`, `unit` and
  `controls`);
- `max_bytes`, which a reader never reads past;
- optionally `terminator` (hex in memory byte order, matched only at character boundaries), `length` (a *number*
  holding the length), and `rows: {count, stride}` for a buffer of rows, each read to its terminator and joined
  with newlines;
- optionally `rows.lengths`, for a game that keeps one length per row: a *number* whose address is the first
  row's length, with the next rows' lengths right after it (a `u16` for row 2 is 4 bytes on);
- optionally `rows.pointers: true`, for a game that keeps a list of records, each pointing at a text kept
  somewhere else (a choice's answers left where they are in the script): a row is then a 32-bit pointer to its
  text, in the layout's `endian`, and `stride` is how far apart the pointers are. Each text is read to its
  terminator or to `max_bytes`. A pointer of 0 is an empty row. Not together with `rows.lengths`;
- optionally `pages: {break, cursor}`, for a game that keeps a whole stretch of script in one buffer and walks
  through it: `break` is the code between two pages (hex in memory byte order, one byte or more, matched only at
  character boundaries) and `cursor` a *number* holding the address the game is reading at. The text is the page
  the cursor is on. Not together with `rows`;
- optionally `codes`, for the game's own codes that take arguments (a colour, a style, a pause): each key is
  the bytes a code starts with (hex in memory byte order), its value how many bytes the code has in all, those
  included (32 at most). `{"03": 2, "07": 2}` is two codes with one argument byte each. With `table` these are
  the controls that take arguments (`{"FFF9": 4}`: a two-byte control and its two-byte argument); a control
  without one needs no entry here;
- optionally `script: {offset, open}`, for a game that keeps no copy of the text it shows and reads it straight
  out of a script, with instructions between the texts. The text's address is then where the script starts,
  `offset` is a *number* holding how many bytes into the script the game is reading (with `"counts": "codes"`,
  how many codes of `unit` bytes), and `open` is the code a text starts after (hex in memory byte order). The
  text is the page the game is on, as far as it has read it, so it grows while the page types out. Only with
  `table`, and not together with `rows`, `pages`, `terminator` or `length`. With `texts: "rows"` in it, each text
  of the page is a row of its own: for a list the game writes as one text per item, such as a choice's answers.
  With `rest` (1 to 16), a game may stop up to that many codes past a text's end code while the text stays up,
  as one does that reads a choice's jump targets after its answers and waits there.
  Without `open` the script is one text from its start: for a game that copies the stretch it is showing into a
  buffer of its own, pages and all, and counts how far it has typed. `texts` and `rest` need `open`;
- optionally `when`, a *flag* that is true while the text is on screen. Many games leave the last text in memory
  after its window closes; while `when` is false the text is empty;
- its own `cleanup`, applied before the entry's.

A length counts bytes unless it says `"counts": "characters"`. Games that write a new line over the old one
without ending it need a length to tell where the new line stops.

A game that keeps only a pointer to the page it shows gives that pointer twice with `pages`: as the text's
address (`base` with the offset `0x0`) and as the cursor. The text is then the first page from there.

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
   With `codes`: where a character starts with one of the keys (the longest, if several fit), it is a code of
   that many bytes, whatever the charset would make of them. The terminator is looked for first. A code is one
   character here, so an argument byte is never a terminator, a page break or the start of a character.
4. With `pages`: read the cursor (if it can't be read, the field fails) and split what step 3 left into pages
   at each `break` standing at the start of a character. The text is the last page with anything in it that
   starts at or before the cursor. A cursor on a break is still on the page before it, and a cursor outside what
   step 3 left (before the text's address, or past its end) is on the last page with anything in it: a game may
   drop its cursor once the last page is typed out. No such page: the empty string.
5. Decode each character on its own. One that doesn't decode is one U+FFFD, however many bytes it has. A code
   from `codes` is nothing.
6. With `rows`: drop empty rows from the end, and join the rest with a newline.
7. Apply the text's `cleanup`, then the entry's.

With `script`, the steps below take the place of steps 2 to 4. Codes are `unit` bytes. An *end code* is one the
tables mark with `/` or `controls` calls `end`; a *page code* is one `controls` calls `page` or `clear`. The
*position* is the text's address plus `offset`.

1. Read `offset`; if it can't be read, the field fails. If it counts codes, multiply it by `unit`. At zero or
   below the text is the empty string.
   Otherwise read the bytes of script that end at the position, `max_bytes` of them or `offset` if that is
   less, and as many bytes after the position as `open` has. If the read fails, the field fails.
   With `rest`: look at the code just behind the position, then the one before it, and so on: `rest` + 1 codes
   at most, and none that starts before the bytes read. At the first end code among them the looking stops,
   and from here on the position is just past that code, and the bytes after the position are those after it.
   (A text that starts within `rest` codes of an end code is read late, so `rest` isn't for such a script.)
2. An open code *carries the page on* when the code just before it is an end code and the code before that
   isn't a page code: the text after it goes on the same page as the text before it. If the bytes after the
   position are the open code and it doesn't carry the page on, the text is the empty string: the old page is
   over and nothing of the new one has been read.
3. If the code just behind the position is an end code, step back over it. Then walk back a code at a time
   until the bytes just behind are the open code (a text starts here), or the code just behind is an end code,
   or fewer bytes than the open code are left. Where that found a text whose open code carries the page on,
   step back over the open code and the end code before it, and walk on the same way. The page starts at the
   earliest text found. None found: the empty string (the position is among the instructions after a text, or
   the page starts more than `max_bytes` back).
4. The page is what lies between its start and the position, split into codes; a code from `codes` is taken
   whole. Where an end code is followed directly by the open code, both are left out: two texts of the page
   join there, with nothing between them, or with `texts: "rows"` a line break.
5. A page code inside the page starts it anew: what was read before the code is off the screen, and the code
   goes with it. Not so for a page code with nothing after it up to the position but end codes: the page it
   ended is still up, and stays until the game reads on. (A game that closes its window with a code of its
   own gets an empty text from this once it has read past that code, if `controls` calls it `clear`.)

Without `open`, nothing is read after the position in step 1, and steps 2 and 3 are one rule: the page starts at
the script's start. If the bytes read don't reach back to it (`offset` is more than `max_bytes`), the text is
the empty string. Steps 4 and 5 are the same, with no texts to join.

Walking back can't tell an argument from a code. An argument that equals the open code or an end code cuts the
page short there, so a script whose arguments can have those values isn't one for `script`.

| Charset | Bytes in a character, from its first byte | Decoded as |
|---|---|---|
| `shift_jis` | 2 for `81`–`9F` and `E0`–`FC`, else 1 | Windows' Shift-JIS (cp932): `8160` is U+FF5E, the user-defined area `F040`–`F9FC` is U+E000–U+E757, `5C` is a backslash. Single bytes `A1`–`DF` are half-width kana |
| `euc_jp` | 3 for `8F`, 2 for `8E` and up, else 1 | JIS X 0208, `8E` + half-width kana, `8F` + JIS X 0212. Vendor rows (NEC's row 13, IBM's extensions) aren't defined yet |
| `utf8` | 4 for `F0` and up, 3 for `E0` and up, 2 for `C0` and up, else 1 | Strict UTF-8: an overlong form or an encoded surrogate doesn't decode |
| `utf16le`, `utf16be` | 4 for a high surrogate followed by a low one when both fit, else 2 | A surrogate on its own doesn't decode |
| `table` | the entry's `unit` | The table's character. An end code, or a code `controls` calls `page` or `clear`, is a blank line; a newline code is a line break; any other control is nothing; a code the table lacks is U+FFFD. A code `controls` names is that control, also when a table lists it as a character. A code from `codes` is looked for before any of these. Line breaks at the end are dropped |

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

Reference implementation: `tools/src/yomikata/layout.py` (`read_layout`). To check a layout
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
