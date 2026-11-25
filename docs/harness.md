# The emulator harness (work in progress)

A game run headless and kept at hand, so that an agent can play to a text, find it in memory, and try a script on
it, without a device and without a person passing snapshots. It is `yomikata replay`'s core runner kept alive as
a session, offered as tools over [MCP](https://modelcontextprotocol.io) on standard input and output.

**Status:** the tools and the server are tested on a made-up machine, and have been run end to end with the
Dolphin core on a GameCube game: loaded, a reader's savestate from the inbox restored, the game's script
tried, lines advanced with presses and read, the layout overlaid on the frame. The same was done with PCSX
ReARMed on a PS1 game, from a reader's savestate, and that game's script written and checked with it.

## Shut in

The harness runs native code from elsewhere (libretro cores) on games, so it is run in a container that can
reach nothing it doesn't need. `tools/scripts/harness.sh` starts it so:

- **no network** at all;
- **read-only:** the library's game folders, a folder of cores, an inbox's snapshots (for readers' savestates);
- **written:** one scratch folder of its own: states, snaps, and whatever a core keeps (its saves go there, never
  to a save of yours);
- nothing else of the machine; no added privileges; as user 99:100; with a cap on processors and memory, at low
  priority; one at a time (the container has a fixed name).

Inside, every path a tool takes is resolved and refused if it leaves its folder, by `..` or through a link.

What the tools return holds game text and pictures. That is for the agent's eyes and the scratch folder: the
catalog's rule stands, nothing of a game goes into the repository.

## Tools

| | |
|---|---|
| `status`, `games` | What is loaded; the library's folders and files |
| `load` | A game in a core, from power-on or from a savestate (one saved here, or a reader's in the inbox) |
| `run` | N frames with buttons and the left stick held. With a script loaded, what it emitted on the way |
| `run_until` | Runs until a place in memory changes or holds a value, the script emits something, or the picture has stood still; at most a given number of frames. Says how far it ran and why it stopped |
| `steps` | Several moves in one call: hold buttons, then wait (frames, or until something as `run_until`), repeat, snap (also every few frames of a wait), take pictures. The pictures come back as one sheet. With a script loaded: stop repeating when it says something (`until_event`) or has said nothing for some presses (`until_quiet`), and steps to do whenever it does (`on`: press down and confirm at each choice) |
| `screenshot` | The picture now |
| `read` | Memory by address, a region by offset; also as text in the game's own codes, through a table and rules as `code_rule` words them |
| `find` | Where a text or bytes stand, in live memory or in snaps. A text is looked for in Shift-JIS, EUC-JP, UTF-16 either way, UTF-8, Shift-JIS as 16-bit little-endian numbers, and the game's own table when one is given; in parts too, when the whole isn't found |
| `text_runs` | The stretches of memory that read as Japanese, longest first: what to hold against the screen before any line is known |
| `find_relative` | A line read off the screen, looked for in codes of the game's own: where the same characters have the same code and different ones different codes. Gives the codes, the start of a table |
| `identify` | Which release a game file is: platform, id, and what names its build |
| `suggest_build_words` | Places that hold the same in snaps from power-on, the title and play, and the Lua lines that check them: a script's build check |
| `on_disc` | Whether a stretch of memory lies on the game's disc as it is, and where: if a place's text or font does, every place can be read from the disc without playing to it |
| `core_options` | The loaded core's options as it declares them, and what was given at load |
| `snap` | Keeps a moment under a name, and writes it to scratch as an inbox-style snapshot for `yomikata run` and `check` |
| `snap_lines` | Says what a snap's screen showed, once read off its picture: its lines, or that it has no text |
| `sheet` | The pictures of several snaps as one image, each under its name |
| `repeat` | Does the same frames or steps again and says whether the game ended up the same: whether the core repeats itself |
| `differs` | How this moment's memory differs from a snap's, also one another harness wrote |
| `changed` | Places that differ between two snaps, the same as the first in some others and as the second in others: a flag, a counter, a cursor. Or places that go as a pattern says over snaps in order: `abab`, or `aAaA` for one value and anything else in turn |
| `grouped` | Places that hold the same in every state of a group and differ from group to group: which map it is, which chapter. States are loaded for a look and the game put back |
| `entered` | Reads a name off a naming screen from a snap after each character: where the name is, each character's code, the rule kana codes follow, lines of a table |
| `refs` | Words that point at an address or just past it, in the console's byte order; over several snaps, which moments each points there in |
| `cursor_find`, `cursor_to` | Where the game keeps a cursor the pad moves, found by moving it; and the cursor moved to a place in those numbers, a frame at a time until it is there |
| `walk_to` | Walks a character on a map to a place in the game's own numbers, one way at a time, the other way round when something is in the way; stops where the map changes, since the numbers start anew there. `near` for numbers finer than a step |
| `explore` | Finds where the character can walk by trying it: pushes each way from where it stands, goes back, and does the same from every place it got to. Gives the places as rows of a map in the game's numbers, with the doors it met, and leaves the game where it was; given a place, puts the game there as it was reached while trying, the pushes going into the record of the way. For ledges, fences and mazes, and for a map too dark to read |
| `map_picture` | The picture on screen under a grid in those numbers, the character's tile marked: a door or a person seen there can be given to `walk_to`. Takes where the game draws the character, for a picture that doesn't keep it in the middle |
| `visit` | Goes round a picture's spots with the cursor, presses at each until the script is quiet, and says what it read there: a point-and-click room in one call |
| `follows` | Places that take given values one after the other over snaps in order: the character being drawn, in a game that keeps no line of text |
| `watch` | Runs the game and lists each change of a place in memory with its frame; of several places, or a stretch of values on end (a queue's front), a row whenever any changes |
| `code_rule` | What arithmetic ties codes to characters, kind by kind, from a few characters and their codes; or that the codes only keep JIS order |
| `vram_picture` | A rectangle of video memory as a picture: frames, textures, a glyph cache (PlayStation); all of it as tiles, 64 across (Super Famicom) |
| `vram_tilemap` | A tilemap out of Super Famicom video memory as tile numbers, a row a line |
| `glyph_pairs` | While you play, pairs each code the game draws with the glyph picture it draws for it, and writes a sheet of the glyphs under their codes to scratch: a font that isn't in memory whole, read as it comes up |
| `find_picture` | Looks in main memory for a glyph cut out of video memory: finds the font and how it is packed |
| `pointed` | The words from an address on, and the text each pointer among them leads to: a stack or a record read as strings |
| `state_save`, `state_load` | States by name, in scratch. A state keeps the way to it beside it |
| `waypoint_save`, `waypoint_go`, `waypoints` | A moment kept with every move that leads to it from power-on, and gone back to: by its state, or by playing the moves again when the core is another build |
| `script_try` | Loads a script on the running game: its load error (another build, a missing region), or its header |
| `compare` | Runs a script over snaps and counts: what it reads against the lines given with `snap_lines`, and, given an entry, against that entry's layout (as `yomikata compare`). Counts hold no text |
| `lua` | One line with the script functions on live memory |
| `poke` | Only with `--allow-poke`: writes bytes into main memory, to reach a moment. Recorded ("Writing memory", below) |
| `overlay` | The picture with the script's layout cells drawn on it |

A script that reads glyphs by their pictures logs those it has no reading for (lib/glyphs.lua); the harness
writes them to `glyphs/<script>.log` in scratch for `yomikata unread` ("A game's own font", below).

The way to a script, with them: `load`, `steps` to a line (a sheet shows where you are), `find` it, `snap` at a
few moments, `changed` and `refs` to find what says it is on screen, write the script, `script_try`, `run_until`
it emits and read what it says, `overlay`. Then the check: `steps` through a stretch of the game with a snap at
each, `sheet` to read the snaps' screens, `snap_lines` for each, and `compare`.

docs/first-script.md walks through all of it, from a game file to a pull request.

Buttons are `a`, `b`, `x`, `y`, `l`, `r`, `l2`, `r2`, `l3`, `r3`, `start`, `select` and the four directions, as a
libretro pad names them (on a PlayStation `a` is circle, `b` cross, `x` triangle, `y` square).

A step is an object: `buttons`, `stick`, `hold` (frames they are held, 4 unless said), then `wait` (frames) or
`until` (what `run_until` takes, with its `limit`), `times` to repeat it, `snap` (true, or a name) and `shot`
(true: its picture goes on the sheet). For example, a press and a wait for the next line, twenty times, a snap
at each: `{"buttons": ["a"], "until": {"limit": 600, "event": "text"}, "times": 20, "snap": true}`.

### Finding where the text is

In the order to try them:

1. `text_runs`, with a line on screen. A game that keeps its script in a standard encoding shows it here, and
   often the buffer it is typed into as well: the address of the stretch that reads as the screen does.
2. `find` with the line as you read it, when you want one line's places only, or only know part of it
   (`pieces`). It finds a text kept as 16-bit cells too (`sjis_le16`).
3. Nothing found: the codes are the game's own. `find_relative` with the line: it needs characters that repeat
   in it, and takes the line's rows with a line break between them. Where the game has other codes among the
   line's, the whole doesn't fit and its rows are tried by twos and alone (`parts`). A window that keeps tiles
   often has a second cell for each character (`stride`: 2) and rows that aren't next to each other: give one
   row.
4. Surer than one line: `lines`, two or more snaps each with what its screen shows at the same place. One
   place must fit them all, and the characters the lines share must have the same code, so lines without a
   repeat will do.
5. A script read where it lies keeps each line somewhere else, so two moments share no place: `apart`, several
   lines of one scene looked for together in one memory, each wherever it is. Two of them must share three
   characters or more. Give kana stretches: a game may keep kanji in two bytes, and a name the player chose
   as a code that stands for it, and either breaks a line that is taken as one code a character.
6. A game that lets the player enter a name tells its codes outright. On the naming screen, snap, enter one
   character, snap, enter another, snap: the places that took a value one after the other are the name, and
   the values are those characters' codes. `entered` does this from the snaps and the characters. A few kana give the rule the rest follow (a row, an offset between
   hiragana and katakana), and with a handful of codes a plain `find` by bytes finds the dialogue. This is what
   found Final Fantasy IX's text, after every search by the line itself had failed.
7. The codes it gives are the start of the game's table. With them as a table, `find` (`table`) looks for
   other lines, and each new line adds characters.
8. A game may hold nothing that says which line is on screen once the line stands: it points at the line only
   while it types. Press, then snap through the typing (`steps` with `wait`, `every` and `snap`), and ask
   `refs` over those snaps for what points into the line: a word that does in some moments only is the one
   that follows it. `pointed` then reads the record or stack that word is in as strings. This is what found
   Double Cast's lines, after seven snaps of standing lines had shown nothing.
   A word found this way may be on a stack: it holds the pointer in this scene and something else in the next.
   Before a script trusts its address, play to another scene (a close-up, another room) and ask `refs` again.
   Policenauts' message pointer moved 0x78 bytes between two objects of one room; what stayed put was the
   game's own copy of the message, which `find_relative` had given as a second place.
   The same goes for a cursor's numbers: `cursor_find` gives where they are in this scene. In Policenauts they
   moved when the room's light changed; when `cursor_to` no longer reaches a place, find them again.
   On a map the character walks, `map_picture` shows the screen under a grid in those numbers: read a door's
   or a person's place off it and give it to `walk_to`, which goes straight and round one thing, not through
   a maze, so give it the corners. It stops where the map changes. It knows that by a jump in the numbers;
   where two maps' numbers lie close (stairs that join floors at one place) give it `map_address`, a byte
   that says which map it is: `grouped` finds it from the states you kept on each map. Dragon Quest V's is
   0x7E00BD. People may stand between tiles: when
   talking brings nothing, step half a tile nearer. And a game may look at the pad only every few frames:
   on Dragon Quest V's deck a press held 3 frames was lost, and one held 8 was not.
   Where `walk_to` gets no nearer and the picture doesn't say why, ask `explore`: it floods the map by
   trying (a push of a few frames from each place it has reached, a state kept in memory for each), so it
   knows ledges, fences and people in the way without being told, and `to` takes the game to a place it
   reached. (It takes the state kept for that place: the same pushes played again from the start ended
   elsewhere after sixty of them on bsnes-mercury, which doesn't go on from a loaded state quite as it
   would have run through it.) Mother 2's
   first night is too dark to read a path off the picture, and its hillside is ledges: 687 places around
   the two houses were found in half a minute (`step` 6; the game's numbers there are tiles of eight pixels,
   at 0x7E46FA across and 0x7E46FC down), and showed at once that the road-block can't be walked round. It
   is breadth first and stops at `most` places: give `within` on an open map, with the character inside it.
   Things learnt on the same game: `cursor_find` takes the first pair that follows the pushes, and next to
   a wall that may be a pair that isn't the place (a camera's, a queue's): find it on open floor, and hold
   the numbers against `map_picture` before walking by them. A button may do one thing with a window up
   and another without: there B takes a message on and, with none up, opens a window that stops the
   walk. Press through a talk with `until_quiet` 1, so that the first press that brings no text is the last.
   A game may keep two sets of numbers for everything that walks: where each is on the map, and where each
   is drawn on screen. Chrono Trigger does (the map's at 0x7E1800 across and 0x7E1880 down, a word for each
   of 32 things, 16 to a pixel; the screen's at 0x7E0A00 and 0x7E0A80), and `cursor_find` gave the screen's,
   which stand still while the picture scrolls. Read both before and after a push to see which thing the pad
   moves and which numbers are which; the others in the list are the people to talk to, so nobody has to be
   found by eye. Give `walk_to` the map's numbers with `near` (a step there is 16, and a walk never lands on
   the very place), and `map_picture` the screen's as well (`screen_x_address`, `screen_y_address`), since the
   picture doesn't keep the character in the middle at a map's edge.
9. When `find_relative` finds nothing it tries the other ways a game may keep a line and gives those that fit
   (`other_ways`): codes of two bytes, the other byte order, kana as far apart as in an order, counting up or
   down. A line with three or four different kana is enough.
10. With a line found, `code_rule` on some of its characters and their codes says the arithmetic for each kind.
   Where kana follow a rule and kanji only keep JIS order, the kanji are the game's font in its own numbering.
   The font is then found from the screen: `vram_picture` shows where the game drew the line, `find_picture`
   on one character's rectangle finds its glyph in main memory, and the glyphs around it are the font.
   `yomikata sorted` checks readings of such a font by its order: a reading that breaks the order is wrong, and
   between two sure readings the order allows few characters, sometimes one. And where `on_disc` says the text
   lies on the disc as in memory, `yomikata disc-text` with the table so far writes the whole script to
   scratch, to read each doubtful kanji in a sentence. This is how Policenauts' 1,800 kanji were read.
11. A point-and-click scene: `cursor_find`, then `visit` with the spots picked off one picture. An object may
   offer more than one command (open its menu and take a picture before deciding a room is exhausted).
   Policenauts' first room was gone round this way, and its videophone's second command found; what brings
   the next scene on was not found in the calls given to it, so who speaks is still not read there.
12. A game may keep no line of text at all: its script is packed in the cartridge and each character is drawn
   as a picture. Every search for the line then comes back empty. But to draw a character the game must have
   its code for a moment. Snap every frame while one line types (`steps` with `every`: 1), and ask `follows`
   for the places whose values go as the characters' pattern (the same letter for the same character). `watch`
   the candidates: the one that is still except while the line types is the code being drawn. Beside it look
   for a queue: `changed` with a pattern of all-different letters over the typing frames, then `read` with
   `snaps` to see a stretch frame by frame. Dragon Quest V unpacks whole messages ahead into such a queue, and
   its script reads them there. The codes are the dialogue font's own (a naming screen's codes are the menu
   font's and don't help), so the table comes from pairing each code with the glyph the game draws for it.
   `glyph_pairs` does that pairing while you play and writes the sheet. When every search is empty, look at
   `vram_picture` first: a message drawn as a picture shows there at once, below the fixed font.

13. Lines that are shown for a time and never typed (captions under a film, a radio call with a voice): a
   game may bring each scene's lines as a list, with when each starts and how long it stays, and keep one word
   that points at the line on screen and is zero between lines. `refs` on the line's place finds the word, and
   `watch` on it through a few lines shows it going from line to line. Before trusting it, hold what it points
   at against the picture, commas and row breaks too: Metal Gear Solid's radio calls come with such a list
   (it follows the voice) and show another text, with its own word, its own punctuation and its own rows. The
   two differ by a comma here and there, which a count of agreeing characters would let through. What says
   which of the two is shown was found with `changed` and a pattern over snaps with the radio up and without.
   That game's lines were found by a kana stretch and an order alone (`other_ways`: two bytes, high first, as
   far apart as in Unicode); a line with kanji found nothing, since every list numbers its own kanji anew and
   brings their pictures with it. A script for such a game knows a kanji by a hash of its picture
   (`text.hash` of the bytes as they lie), and its dictionary is those hashes, read as below.

14. A game may bring each scene's text with a font of that scene's own: only the characters its lines use,
   numbered as they come up. Then no search for a line finds anything, in any order or encoding, and a kana
   stretch and its order find nothing either, since kana have no order there. What is left is the picture:
   Ryuu ga Gotoku 2's lines were found by counting what is drawn (a count of sprites that went up by four a
   character as a subtitle came), then by what differs between snaps with each subtitle up (`changed` with a
   pattern), which led to the rows drawn and from them to the text and the font. Such a game's text is
   read as the next section says, by its glyphs' pictures. Two things held there that may hold elsewhere:
   one word points at the rows drawn now, whatever draws them (a scene's subtitles, a window in play, a
   question's answers, a place's name), so one reader covers them all; and what tells them apart is how
   they are drawn (an answer's colour, a caption drawn twice for its shadow), not a field that names the
   kind. A window's first row was there 54 frames before its speaker's name: a script that says a page the
   moment it sees it says it twice, once without the name.

What it finds may be the script where it lies, the buffer the game types into, or a copy of the screen's
tiles; which one changes as the line changes (`changed`, `run_until` on its first cell) says which to read.

Seen on games that already have scripts: `text_runs` gave both the script and the typed buffer of a game with
16-bit Shift-JIS cells; `find_relative` found a line of a game with its own 16-bit codes from two of its rows,
though not from all three, which have a colour code among them; and with two moments it found a tile window's
copy of the screen (its rows' two halves), not the window's own buffer, which had moved to another slot between
the two.

### Snaps on disk

A snap is written to `snaps/` in scratch as an inbox-style snapshot: `<name>.ram.gz`, a `<name>.<region>.gz`
for each region, `<name>.json` and `<name>.png`. The first snap after a game is loaded is whole. The later
ones are kept as what differs from that first one (`tools/src/yomikata/snapfile.py` has the form): the same
file names, read by everything here that takes snapshots (`run`, `check`, `compare`, `locate`, `where`,
`propose`, the harness's own tools). Measured over thirty snaps of a PlayStation game: 535 KB for the first and
some 60 KB for each of the others, where each was 500 KB before; over ten of a Super Famicom game, 31 KB and
then 1.4 KB.

What follows from it: the later snaps need the first one beside them. Delete or overwrite it and they say so
instead of reading wrongly. `yomikata expand <files>` rewrites snaps as whole ones, for moving a few elsewhere or
for a tool that doesn't know the form. Snapshots a reader uploads to an inbox are never kept this way.

### Waypoints

A state is the quick way back to a moment, and it only loads in the core build that made it. A waypoint is the
lasting one: the game, the core and its options, and every move since power-on, as frames with the buttons and
the stick held; with a state for speed and the picture to check by.

The harness keeps the way as it goes: each `run`, `run_until` and `steps` adds the frames it actually ran, so a
wait "until" is kept as the number of frames it took. `state_save` writes the way beside the state, so a state
loaded later carries on from power-on; a state without one (a reader's, from the inbox) is where the way then
starts, and a waypoint made from there needs that state. A memory write on the way is part of it: replaying
such a waypoint writes again, needs `--allow-poke`, and the waypoint says how many writes it holds.

`waypoint_go` loads the game if it isn't loaded, and takes the state when the core file is the one that made
it (by its SHA-256); otherwise, or with `replay`, it plays the moves. Either way it says whether the picture is
the one kept. A replay that gets there keeps a state of the new build. What is checked is the picture, not
memory: from power-on only PCSX ReARMed ends with the same memory (above), and the picture is what a script's
text is read against.

Replayed in a second harness, from power-on: 11,660 frames with ten presses on PCSX ReARMed, 2,636 frames with
nine presses on bsnes-mercury, and 1,500 frames without a press on Dolphin; each reached its picture. A long
way through a game's random events hasn't been tried on the two cores that don't repeat exactly: if one stops
reaching its picture, make the waypoint from a state taken early instead.

### A game's own font

A game whose codes are its own draws from a font of its own, and nothing in memory says which character a glyph
is. Where memory has a list of the glyphs (where each one's picture is in a texture page) and video memory has
the pictures, `yomikata glyphs` draws them from a snap, in code order, for reading:

```sh
yomikata glyphs snaps/place.ram.gz --list 0x800D7110 --count @0x800D710C \
    --dictionary tables/ps1/SLPS-02000.glyphs --sheet /scratch/work/unread.png
```

It prints each glyph's number, a fingerprint of its picture and its character where the dictionary has it, and
writes a sheet of the glyphs it hasn't: the first of each fingerprint only, so a character is read once however
many fonts and places it turns up in. Sheets are game data and stay in scratch; the catalog keeps the readings
(`tables/<platform>/<id>.glyphs`, fingerprint=character) and the tables made from them.

Reading a glyph by itself goes wrong a few times in a hundred at this size. What catches it is the game's own
text: decode the place's messages with the readings and read the sentences. A wrong kanji shows at once in a
sentence; a glyph that stays in doubt is left out, and comes out as U+FFFD, rather than guessed.

Three things the tools do to catch a wrong reading, none of them sure by itself:

- beside each unread glyph, `glyphs` names the three pictures already read that look most like it (`like:`), so
  that a glyph is told from what it could be taken for, not read alone;
- a character the dictionary gives to two pictures is said (`glyphs.twice`): a font draws a character one way,
  so one of the two readings is wrong;
- the read-through: `yomikata run --dump --unconfirmed <file>` prints a place's texts that hold a kanji not
  yet confirmed in a sentence, each such kanji in 【 】, shortest first. The file lists the characters someone
  has read in a sentence and found right (`tables/<platform>/<id>.read`); it grows as places are read through.

Before reading a font place by place, ask `on_disc` whether the place's text and font lie on the disc as they
do in memory. Where they do, a small script over the disc image finds every place at once
(`tools/scripts/ff9_places.py` is one) and nothing has to be played to or written.

Another reader helps, as a flag more than as a judge. On Final Fantasy IX, manga-ocr (the model
`kha-white/manga-ocr-base`, from Hugging Face; run in a scratch folder, not part of the tools) was given each
glyph enlarged eight times with a white margin. It agreed with two readings in three; nearly all the rest were
its own mistakes at thirteen pixels, but the list of disagreements, each read in a sentence, held the wrong
readings: a reading it agrees with has been read two ways, and one it doesn't goes to the read-through.

The order of reading a font, then:

1. your own reading of each glyph, off a sheet (`yomikata glyphs`, or the harness's `glyph_pairs` for a font
   that only shows as the game draws);
2. `yomikata glyph-read` for the second reader's readings beside yours (run it from a scratch environment that
   has manga-ocr; it isn't part of the tools): what agrees has been read two ways;
3. where the font stands in a known order, `yomikata sorted` on the readings: what breaks the order is wrong,
   and the order names the few characters a doubtful glyph can be;
4. every reading the two readers differ on, and every one the order doubts, read in a sentence of the game's
   text: `glyph-read --text` prints one for each, from a dump (`yomikata disc-text`, or `run --dump`), or
   read it in play. A reading none of this settles is left out of the table.

Where a game brings its kanji as pictures with each scene (Metal Gear Solid: 36 bytes a picture, after the
scene's lines), there is no font to read once: let the script, in a copy for trying, log each picture it has no
reading for as it plays, draw those on a sheet, and read them with the lines they came up in beside you. Its
first 188 pictures were read so, over four scenes; the second reader agreed on 155, and the 33 others
were its own mistakes, each read in its sentence.

`yomikata unread` is that way made into a tool, for any size of picture. A script asks `lib/glyphs.lua` for
each picture's character; a picture the dictionary hasn't got is logged once (`glyph <hash> <width>x<height>x
<bits> <hex>`), and so is each row of text that held one (`glyph-line`, the hashes in order). The harness keeps
those lines in scratch (`glyphs/<script>.log`) and leaves them out of a call's events. Then:

```sh
yomikata unread glyphs/SLPM-66602.log --dictionary games/ps2/SLPM-66602.glyphs \
    --sheet work/unread.png --second work/second.tsv
yomikata unread glyphs/SLPM-66602.log --dictionary games/ps2/SLPM-66602.glyphs \
    --second work/second.tsv --row "12=…" "13=…" --add 5E64=諸
```

The first draws the unread pictures (`unread.png`) and, numbered, the rows they stand in (`unread-rows.png`),
and has the second reader read each picture alone and each row whole (run it from the environment that has
manga-ocr; without it the sheets are drawn all the same). The second writes readings into the dictionary: a
row read whole by its number, each picture taking the character that stands there, or one picture by the start
of its hash. It refuses a row read to another length than it has pictures and a reading that differs from the
dictionary's, and lists the new readings the second reader read otherwise: read those rows again. Reading
whole rows is quicker than glyph by glyph and surer, since a sentence carries its kanji.

Ryuu ga Gotoku 2's 602 glyphs (24 by 24, two bits a pixel) were read so, from the 269 rows of half an hour's
play. The second reader agreed on 469 of the 536 it was asked about; the 67 others were its own mistakes, 18 of
them hangul, which it doesn't read.

On Dragon Quest V's first 75 glyphs the second reader agreed on 51; the 24 others were all its own mistakes,
each already read in a sentence. On 136, it agreed on 96.

On Final Fantasy IX: the fixed font's 461 kanji were read from five sheets; the second place's own font had 218
glyphs, of which the dictionary from the first place already had 88, and its 130 new ones were one sheet.
The four discs have 64 places and 1,007 distinct pictures between them. Of the 236 that the first disc hadn't
shown, a first reading by eye was wrong for 31: rarer kanji, read alone. Sentences and the other reader put
them right, and found ten wrong among the 1,200 read before.

### The record of calls

Every call of a tool goes into `record/calls.jsonl` in scratch, one line each: when, an id for this run of the
harness, the game loaded, the tool, its arguments in short (a long text as its start and its length, a list as
how many it holds), whether it failed, and how long it took. `yomikata calls <scratch>` sums the record by
game. It is how a script's cost is known: the number of calls from the first to the comparison that met the bar.

### Writing memory

Off unless the harness is started with `--allow-poke`; then there is one more tool, `poke`: bytes at an address
of main memory, with a sentence saying why.

It is for REACHING a moment the game is slow to get to: a chapter, a flag, a position, a timer. Then the game
produces its text itself, and that text is what a script is checked on. It is never for writing the text, or a
buffer a script reads: that proves a script reads what was written, not what the game shows. Where there is
no other way to show something (a choice nobody could reach in play), such a write is a made-up test and is
marked `synthetic`.

So that this can be told afterwards:

- every write goes into `record/memory-writes.jsonl` in scratch: when, the game, the frame, the address, the
  bytes before and after, why, and whether it was marked synthetic;
- a snap taken after a write says how many came before it (`memory_writes`), `compare` counts such snaps apart,
  and a state saved after one is noted in the record;
- a pull request lists every check that relied on a write and what was written. A count with written snaps in
  it is quoted as such.

The count starts again when a game is loaded. A state saved after a write carries it: a file beside the state
(`<name>.writes.json`) says how many, and loading that state, in this session or a later one, sets the count, so
what is snapped from there is marked too. Loading a state from before the writes clears it.

### A script that reads a line only as it appears

Some games keep nothing that says which line is on screen once it is drawn; a script for one reads the line
in the frames it is being typed. Run over a still snap it reads nothing, so `compare` can't check it that
way. A snap taken while a script is loaded keeps what the script said since the snap before (`said`), and
`compare` with `live` counts that against the snap's lines instead: step to each line with `until` on the
script's event, wait until the line stands, snap, and read the picture.

### A call's time

A call that runs the game stops by itself after `--call-seconds` (120 unless said) and says so: `"stopped":
"time"`, with how far it got; the game stands where it stopped and the next call goes on. This is what a slow
core does to a long run, and nothing is lost by it.

A core that hangs inside one frame can't be stopped that way. A call that hasn't come back a minute after its
time is answered with an error that says so, and the harness ends: its container goes, states and snaps in
scratch stay. Start it again and load a state.

## Running it

```sh
tools/scripts/get-core.sh pcsx_rearmed /path/to/cores        # prints the core's SHA-256: note it with what you find
export YOMIKATA_GAMES=… YOMIKATA_CORES=… YOMIKATA_SNAPSHOTS=… YOMIKATA_SCRATCH=…
claude mcp add yomikata-harness -- "$PWD/tools/scripts/harness.sh"
```

The image is `tools/Dockerfile.harness`: the tools image with Mesa's software OpenGL and the libraries the
Dolphin core needs. A core that needs a BIOS looks for it in `system/` under scratch, or in a folder of its own
given as `YOMIKATA_SYSTEM` (mounted read-only): your own console's, never one from elsewhere. PCSX ReARMed plays
most games without one; some show nothing until it has one.

### Does a core repeat itself?

Everything found by replaying rests on the same steps reaching the same moment. `repeat` checks it: it does
the same frames or steps two or more times, from this moment (going back by a state kept in memory) or from
power-on (loading the game anew), and says whether memory, video memory and the picture ended up the same, and
where memory differs. `differs` holds this moment against a snap written by another harness, for a core that
can't be loaded twice in one.

Measured with the cores named, on one machine:

| Core | From a state | From power-on |
|---|---|---|
| PCSX ReARMed | the same: 3 runs of 752 frames with presses | the same: 2 runs of 900 frames |
| bsnes-mercury (balanced) | the same: 3 runs of 634 frames with presses | not the same: after 1500 frames some 16,300 bytes of work RAM differ (`0x7FC001`-`0x7FFFFF`), also between two harnesses; the picture and video memory are the same |
| Dolphin (one emulation thread) | the same: 3 runs of 496 frames with presses | not the same: after 900 frames in two harnesses, 110 of 24 million bytes differ, the console's clock and what derives from it |

So: from a state all three repeat; from power-on only PCSX ReARMed does. On bsnes-mercury, loads made within
the same second matched and loads seconds apart didn't, which fits memory the core fills from the clock at
power-on and the game hasn't written yet; that reading comes from these runs, not from the core's source. A
route kept for these two cores should start from a state made early, not from power-on, or be checked on what
the game shows rather than on all of memory.

Neither core has an option that pins it (`core_options` lists what a core declares): bsnes-mercury declares
seven, none about memory at power-on or randomness; Dolphin's 113 have a CPU clock rate but no setting for the
console's clock. So there is nothing to give at `load` for this.

Notes on cores:

- A savestate loads only in the core build that made it. Note the core's SHA-256 with every state kept.

PCSX ReARMed (a PlayStation game):

- It gives out no video memory, but its savestate holds it. The harness takes it from there: `read` with
  `region: "vram"`, a snap's `vram` part and a script's `mem.region("vram")` have the console's 1024x512 words.
  It costs a savestate each frame it is asked for, so a script that reads it every frame runs slowly here.

LRPS2 (`pcsx2_libretro.so`, a PlayStation 2 game; `tools/scripts/get-core.sh pcsx2`):

- It needs the console's BIOS: your own, as the one file of `pcsx2/bios/` under the system folder, and named
  to the core with the option `pcsx2_bios` at `load` (the file's name). Without the option it finds the file and
  still refuses to start.
- Given no context it can use, it draws with its own software renderer, and fast: on one machine (8 processors
  allowed) about 500 frames a second through a game's boot and 250 to 300 in a film and in a scene drawn by the
  game. Nothing of the harness had to change for it.
- Memory is the console's 32 MB, by address from 0. A savestate is 50 MB.
- A game's build line is its serial and the CRC the emulator gives it; `identify` works both out of the disc
  (`build`), and the core's own log names the same CRC. Identifying a DVD image unpacks it first: a minute or two.
- It can't write memory cards unless `pcsx2/memcards/` under the system folder is the harness user's to write.
- From a state it has repeated itself as far as a script's events show: two runs of 17,900 frames through five
  scenes gave the same 53 pages on the same frames. Its memory after such runs hasn't been compared, nor has a
  run from power-on.
- A press may be lost on a frame the game doesn't look at the pad: hold a button 6 frames. The left stick
  walks; in a game that types its messages, a press while one types only finishes it, so step with a press
  `until` the script's next text rather than a press a page.

Dolphin (`dolphin_libretro.so`, a GameCube or Wii game):

- It has no renderer that works without a context: the core sets its video side up only when a frontend's
  context comes up, and without one the first frame crashes. The harness gives it OpenGL drawn by the CPU (Mesa's
  llvmpipe), so no graphics card is used. Measured with 8 processors allowed: 45 frames a second in a scene of
  the game, over 100 while it starts.
- Give it the option `dolphin_main_cpu_thread=disabled` (one emulation thread). With it, two runs of 900 frames
  from power-on ended with 117 of memory's 24 million bytes different, all in the console's clock and what
  derives from it: close to repeatable, not exactly.
- A savestate made by the same build on an Android device (arm64) loads here (x86-64) and goes on. It must not be
  loaded in the core's first frames, or the game stands still: the harness runs 300 frames first.
- It can't be loaded twice in one harness: the second load never comes back. The harness refuses it and says
  to start again.
- It needs `/dev/shm` larger than Docker's default (the script gives 2 GB), and libcurl, libEGL and libusb
  (in the harness image).
- Memory is given by address (the 24 MB from `0x80000000`), in the console's byte order.
- Once, a session stopped answering with one processor busy and nothing coming back, from its first call; the
  same calls ran in seconds when made again. The cause isn't known. The harness now gives such a call up by
  itself ("A call's time", above): seen working on a second load of this core, given up after its time.
