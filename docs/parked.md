# Games begun and parked

What was found in a game that was begun and not taken to its bar, so that it can be picked up again without
finding it twice. Addresses and codes only: no lines of the games' text, no names. A state's name is one kept in
the harness's scratch (`states/`), where it may or may not still be.

## Final Fantasy VI (Super Famicom, SFC-0ACC8D36)

Parked at 111 of its 300 calls: the story's next long talk needs the party walked through the first town, and
that needs its place on the map found first (for `walk_to`).

- **Core**: bsnes_mercury_balanced. HiROM: a script reads the cartridge at `0xC00000` + its file offset.
- **The text** is never in work RAM. A 24-bit pointer at `0x7E00C9` (low), `0x7E00CA`, `0x7E00CB` (bank) walks the
  script in the cartridge as a page types (found with `advancing` over one page's typing; the opening's talk is
  at bank `0xCD`).
- **A page's codes**: `0x12` a new page (most often followed by `0x00`); `0x16` and a byte a timed wait; `0x11` a
  wait for the button; `0x01` a new row; `0xFF` a space; `0x14` and a byte a command. `0x1C`-`0x1F` and a byte are
  kanji. A single byte is a kana, a sign, or a dictionary entry of two characters or more (`0xE5`, `0xEB`, `0xDB`
  and `0xE8` are such entries). Read forward from a page's start, so that a kanji's second byte isn't taken for a
  command.
- **Codes read in play** (one page aligned with its picture): ま 9D, れ AD, な 93, が 2B, ら A7, に 95, た 7F, し 77,
  と 87, か 6B, い 8D, つ 83, を BB, ア 8A, マ 9C, で 45, ー C5, … C7, 「 D0, digits `0x53` + the digit. Kana that
  stand together in the fifty sounds don't stand together in code, so `find_relative` doesn't fit them.
- **The font** isn't in the cartridge in any form `rom-font` knows (1 bit, rows of 12 or 16, the Super Famicom's
  2- and 4-bit tiles): most likely packed. Video memory holds each page drawn as tiles, and a small menu font,
  neither in code order. So the table has to come from play, with the dictionary's entries from its table in the
  cartridge once their codes are known.
- **Fights**: `0x7E0002` is `0xFF` out of a fight and `0x00` in one (`advance_through` with `differs` `0xFF`). The
  fight's command cursor wraps; with it on the item command, `a` opens an empty list (a `first` of `up` sets it).
- **States**: `ff-a` the title; `ff-c` after the opening's talk; `ff-f` in town before a fight; `ff-l` in town
  after it; `ff-m` walked north into the town's edge.
- **Next**: find the party's place on the map (compare snaps a step apart), then `walk_to` between the story's
  beats, `advance_through` through the fights, and the table from the pages on the way.

## Tools to build when needed

- **A proportional-font reader from video memory.** For a game that draws its text a character at a time into
  fresh tiles, in a font of varying width, and keeps no page in memory: read the window's pixel rows from video
  memory (lib/gbtiles.lua gives a Game Boy's tiles), cut the glyphs at blank columns, fingerprint each, and look
  them up in a dictionary (the `.glyphs` way, from whole tiles to cut glyphs), saying each unknown glyph with
  its picture so the dictionary is quick to build. Kaeru no Tame ni Kane wa Naru draws so, but keeps its page
  unpacked in work RAM, so it didn't need one. Build it as its own pull request, with synthetic tests, when a
  game does.

