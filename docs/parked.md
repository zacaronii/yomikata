# Games begun and parked

What was found in a game that was begun and not taken to its bar, so that it can be picked up again without
finding it twice. Addresses and codes only: no lines of the games' text, no names. A state's name is one kept in
the harness's scratch (`states/`), where it may or may not still be.

## Final Fantasy VI (Super Famicom, SFC-0ACC8D36)

Parked at 111 of its 300 calls, resumed and parked again at 153: the story's next long talk needs the party walked
through the first town, and the way on wasn't found (below).

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
- **Resumed (calls 112-153).** A reader works: each frame it reads the pointer, and once it has stood 20 frames just
  past a wait (`16 xx`, or `11` and a byte) it reads the page back to its `12`, then forward, kanji as two bytes,
  `14`/`16` and their byte skipped, rows at `01` (branch `final-fantasy-6`, script and table, not for merging).
  The first page of a message has no `12` before it: the start has to be where the pointer jumped to, or the read
  runs back into the bank before. Offline on the cartridge and live from the title it said the opening's pages.
- **The table so far**: 112 codes, from six pages of the opening held against their pictures (the pictures are
  sharp; the intro's 2nd and 4th pages were taken mid-fade and are dark). Digits are full width on screen
  (`0x53` + n is `０` + n). Dictionary words met: `DB` った, `DC` 幻獣, `DE` して, `DF` 世界, `E3` から, `E5` 魔導,
  `E8` 兵士, `EB` の力, `EC` じゃ.
- **The party's place**: the tile numbers, a byte each, x at `0x7E00AF` and y at `0x7E00B0` (found from one call of
  pushes with a snap after each, read outside the harness; `cursor_find` gave other words). `explore` on them, with
  `fight` on `0x7E0002`, maps a street in one call.
- **The town, from ff-l**: the street is 31 places, x 33-38, rows 1-17. Up at (37, 1) the map changes (state ff-q):
  a way of 13 places, x 25-26, rows 43-52, that ends under a house; up there doesn't move the party. The side way
  west at rows 10-12 ends at x 34 on a bridge, a steam pipe beside it (ff-t); left doesn't move. With `diagonals`
  the maps are the same. What isn't known: whether a guard or an event must come first, or a door that `explore`
  can't see (a change of map takes longer than a push) leads on. **Next**: `map_picture` on both maps and a long
  push at each edge, by eye, before more exploring.
- **States**: ff-p1 after the opening's talk (from the title, 60 presses), ff-q the second map, ff-t on the bridge.

## Final Fantasy V (Super Famicom, SFC-9EA60A02)

Parked for good at 178 calls (a budget of 150, extended to 180 for the bar alone: 28 over the first budget). The
script reads right everywhere it was compared (22 of 22 screens over 3 scenes, live), short of the bar's 30: the
scene after the rescue in the meteor's field wasn't set off. Script, table and entry on branch `final-fantasy-5`
(draft pull request #228).

- **Core**: bsnes_mercury_balanced. HiROM: the build check reads the cartridge's title at `0xC0FFC0`, which the
  harness now gives over snaps too (`compare`, `yomikata run --rom`).
- **The page** is unpacked whole into work RAM at `0x7E19D3`, laid out as the window shows it: 4 rows of 16 cells,
  a byte a cell, the dictionary's words and the names written out. A kanji is its second byte in the cell, with a
  lead byte for each cell at `0x7E1A13` (1 for `0x1E`, 2 for `0x1F`, 0 for a single-byte code). `0xFF` a blank
  cell; a row's text ends at `0x01` or `0x00`; `0x17` ends the page; `0x0C` a pause. The script in the cartridge
  has its own codes (dictionary words, a name inserted with `0x19`, indents): the buffer is the place to read.
- **The window**: `0x7E0BB5` is 5 while the message window shows its page; 0 with none, 1 and 2 as it opens or
  closes. The page is in the buffer before the window opens and stays after it closes (and through a state's
  load), so a page stands only while that byte is 5 (over 6 frames).
- **Windows that close by themselves** (two pages in the opening): said rightly, but a snap taken on the script's
  text comes at the step's end, after they are gone. Snap them in a second run, 2 frames after the frame the first
  run said them, as the bar's opening screens were.
- **The table**: 132 codes. The kana and signs are Final Fantasy VI's single-byte codes, held against this game's
  pages; the rest and every kanji were read in play, each page's cells in a snap beside its picture.
- **The party's place**: x at `0x7E0AD8`, y at `0x7E0AD9`, a byte each, on the world map and on the field maps
  (`explore` with `whole`). An `explore` that sets off a scene in a branch it drops says nothing of it: where a
  scene is wanted, walk there for real.
- **The way**: the first field is entered from the world map at (175, 142) with a push up. The rescue scene starts
  at the field's far east corner (27, 21) after its first two pages at the clearing's top (16, 21). A name entry
  follows a page of it: leave its grid by up to the bar above it, right to the end choice, and the confirm button.
  After the scene's last page the second party member joins, and the field opens east and south (177 places).
- **States**: `f5-a` power-on plus 40 presses (the opening's castle pages are before it); `f5-k` the rescue
  scene's fight; `f5-q` the field after the rescue; `f5-s` the same from the latest run; `f5-u` beside a figure on
  the crater's top-right rim.
- **Resumed (calls 180-189, a budget of 60 more, to 238)**: the 22 screens' snaps re-read under the current script
  with the cartridge given over snaps (#230): 22 of 22, the build check on the header holding (a check only, not
  the bar). From `f5-u`, the figure on the crater's rim was faced from below and from the left and the confirm
  button pressed (state `f5-v` stands right under it): no page. It may be the meteor itself or an object, not a
  person; the scene after the rescue likely starts elsewhere (the world map, or a spot on the field the walks
  didn't step on). Parked again at 189.
- **Next**: look for where the scene after the rescue starts before spending calls: the field's tiles and its
  objects in work RAM (a list of the map's people near the party's place bytes), or leave the field for the
  world map and walk round to the meteor from outside. Then the bar over it and the 22 screens (snaps `f5r2-00`
  to `f5r2-08` and `f5y-*-on01` to `on13`).

## Chrono Cross (PlayStation, SLPS-02364)

Parked at 146 of 300 calls with a reader that works and no bar. The reader and its table are on the branch
`chrono-cross-reader` (unmerged), to resume from.

- **Load**: disc 1's `.chd`, pcsx_rearmed. On a PlayStation pad `a` is circle (confirm) and `b` cross. Naming:
  `left` takes the cursor out of the grid into the side menu, `down` three times is the confirm item.
- **The script lies in memory as it is**, the scene's messages in two copies (one at 0x80038000, one at
  0x8010BC80 in the opening). `find`, `text_runs` and `find_relative` by sameness found nothing (two-byte kanji
  among one-byte kana break it); `find_relative` with `order` gojuon on a kana-only stretch found it at once.
- **No page is put together anywhere.** A message has slots, 0x12 and a number, that the game fills as it types
  with the speaker's own words (endings, the hero's name): strings elsewhere (0x8006xxxx in the opening), each
  ended by 0x00. The game types from the script a character at a time, a slot's string in its place.
- **The reader's place**: READER, 0x80171B8C, is where the game reads next. In a slot it points into the slot's
  string, often past its first characters by the time a frame sees it (so read the string from the 0x00 before
  it), and the word at READER + 8 is where it goes back to in the message, just past the slot. Found offline in
  snaps every frame across a page change: a word pointing into the script that moved every two frames.
- **The script on the branch** follows the typing: each frame, what READER went over since the frame before,
  and in a slot the slot's string; a page is said when READER stands on 0x02 (the button) or 0x00 (the
  message's end). Over 90 per-frame snaps offline (`yomikata run --sequence`) a page came out right in its kana,
  signs and three filled slots.
- **Codes**: 0x01 a new row, 0x02 the button, 0x00 a message's end, 0x75 a space; 0x04, 0x08, 0x10, 0x18 and
  0x12 take a byte (a pause, who speaks, the window, a slot). Kana by one plan, every point checked fitting:
  hiragana in the order of the fifty sounds from 0x76, voiced from 0xA4, half-voiced from 0xB8, small from 0xBD;
  katakana two bytes, the same from 0xF81A (voiced 0xF848, half-voiced 0xF85C, small 0xF861). Signs read in
  play: 0xC6, 0xC7, 0xCA, 0xCC, 0xCD, 0xCE, 0xF5. Kanji are two bytes after 0xF8 to 0xFD, none in the table yet.
- **Fights**: no one byte holds through a fight (0x8000E2C5 and 0x8000E2C7 follow the command menu and drop
  between turns). The field's own data at 0x80037E52 reads 1 on the field and 0 while the fight's code lies
  over it: `advance_through` with `differs` 1, and `begin` for a fight a walk runs into. Two fights went through
  on `a` alone in one call each (84 and 83 presses, no backs). With the command cursor on the element command,
  `a` went round several pictures for 300 presses and the stuck check never fired: a cycle longer than two.
- **Missing**: the kanji (from pages beside their pictures, as Kaeru's); the speaker's name row (the message
  names the speaker by a number, the name is a string in memory among the fill-ins, and no table of names may
  be written into the catalog); and pages: the opening talk is about ten, the rooms walked after it had none,
  and the next talk is further into the opening's dungeon.
- **States** (harness scratch): `cc-a` the naming screen, `cc-b` after the opening talk, `cc-m2` the first
  fight's menu, `cc-u` the furthest room reached.

## Valkyrie Profile (PlayStation, SLPM-86379 / SLPM-86380): studied, not started

Looked at outside the harness only (0 calls); not begun, as a job of Xenogears' or Final Fantasy IX's size.

- **Discs**: two, with the same executable (SHA-1 `33619ac1…`). Disc 1 is the executable (128 KB), `SYSTEM.CNF` and
  one archive, `VALKYRIE.BIN` (640 MB), that holds everything else.
- **Text**: `yomikata scan` over the disc finds 22 Shift-JIS characters in one file, no UTF-16 or UTF-8, no
  single-byte hiragana and no compression it knows: the game's text is in a code of its own, most likely packed
  inside the archive.
- **Next, if begun**: snaps over a page typing to find the text in work RAM (as for Xenogears and Final Fantasy IX),
  then the table from play or from the font in video memory (`yomikata vram`, `font`); a budget with a point to park
  at if the text isn't found in RAM early.

## Shining Force: Kamigami no Isan (Mega Drive, G-5512): checked, not started

Parked at 7 of its 150 calls (and one `advancing` call made without the game loaded, which the record doesn't
count under it): the text is drawn only into the VDP's tiles, which genesis_plus_gx doesn't give out.

- **Core**: genesis_plus_gx, which gives the 64 KB of work RAM (as 16-bit words, put back by the harness) and no
  video memory: every snap is work RAM alone, no `vram` part. The cartridge is given at `0x01000000` (#230).
- **The text** isn't in the cartridge in any standard encoding (`runs`: no Shift-JIS, UTF-16 or UTF-8), and no
  place in work RAM fits a narration row as codes of the game's own (`find_relative` offline, unit 1 and 2, stride
  1 and 2, either byte order, spaces as characters or not, one row or three moments together; rows read off
  full-size pictures).
- **While a line types** (snaps every 2 frames over the intro's line after the 21st press of A every 120 frames
  from power-on), no number walks the script (`advancing`, unit 2 and 4; unit 1 offline): what climbs is the
  palette at `0xFF0000`-`0xFF007F` fading in, in steps of 2. So no pointer, count or buffer of codes follows the
  typing: the line goes straight into tiles.
- **States**: `sf-a` (power-on plus 40 presses: the intro's narration).
- **Open (tools)**: whether genesis_plus_gx gives the VDP's memory out at all: a core option, or a newer build that
  answers `RETRO_MEMORY_VIDEO_RAM` (it keeps the VRAM internally). Genkan's built-in Mega Drive core is also
  genesis_plus_gx, so the answer matters there too. With VRAM, the game would be read off its tiles (lib/cells
  and a dictionary, as Tales of Phantasia is). Not built: to be decided first.
- **What exposes the VDP's memory** (upstream libretro/Genesis-Plus-GX, `libretro/libretro.c` and
  `core/vdp_ctrl.h`, read from source): nothing today. `retro_get_memory_data` answers `RETRO_MEMORY_SAVE_RAM`
  and `RETRO_MEMORY_SYSTEM_RAM` (`work_ram`, 64 KB on a Mega Drive); `RETRO_MEMORY_VIDEO_RAM` falls to the
  default, NULL and 0. `SET_MEMORY_MAPS` is called only for the Mega CD (68K RAM, PRG RAM, word RAM): a Mega Drive
  game gets no map. No core option changes what memory is given. The VDP's memory is `vram[0x10000]` (with
  `cram[0x80]`, `vsram[0x80]`, `sat[0x400]`) in `core/vdp_ctrl.h`, not referred to from `libretro.c`.
- **A small patch would do it** (not built): `case RETRO_MEMORY_VIDEO_RAM: return vram;` in
  `retro_get_memory_data` and `return 0x10000;` in `retro_get_memory_size`. The harness's `Core.video_ram()` is
  generic, so snaps would keep it as `vram` and a script would read it through `mem.region("vram")`. Two caveats:
  the core keeps `vram` in the host's order through its word macros, so on a little-endian host the words are
  likely swapped against the console's and want putting back, as the work RAM's are; and CRAM and VSRAM aren't
  covered (another id or a map entry), though tiles need only VRAM. Genkan's built-in Mega Drive core is the same
  and has the same gap; whether to patch either is the owner's call.

## Tools to build when needed

- **A proportional-font reader from video memory.** For a game that draws its text a character at a time into
  fresh tiles, in a font of varying width, and keeps no page in memory: read the window's pixel rows from video
  memory (lib/gbtiles.lua gives a Game Boy's tiles), cut the glyphs at blank columns, fingerprint each, and look
  them up in a dictionary (the `.glyphs` way, from whole tiles to cut glyphs), saying each unknown glyph with
  its picture so the dictionary is quick to build. Kaeru no Tame ni Kane wa Naru draws so, but keeps its page
  unpacked in work RAM, so it didn't need one. Build it as its own pull request, with synthetic tests, when a
  game does.
