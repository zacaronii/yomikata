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
| `budget` | How many calls a game has had, by the record, and how many it may have; sets the most. Past it the harness takes no further call for the game |
| `load` | A game in a core, from power-on or from a savestate (one saved here, or a reader's in the inbox). With `state` the one call leaves the game ready at that state: nothing need be run or loaded after |
| `run` | N frames with buttons and the left stick held. With a script loaded, what it emitted on the way |
| `run_until` | Runs until a place in memory changes or holds a value, the script emits something, or the picture has stood still; at most a given number of frames. Says how far it ran and why it stopped |
| `advance_through` | Presses a pattern of buttons over and over while a place in memory says the game is in a mode (a fight, a menu), to get through it between story pages: stops when the mode ends, a watched place changes (the text pointer), at a number of presses, or on time. A picture that stays the same for some presses, or comes again within the last `cycle` presses (6: a menu that takes no button, or one that goes round a few pictures under it, as Chrono Cross's element command does; a blinking cursor, under `still_share` of the pixels, doesn't count; the share is of the game's own screen, without a border around it: a Game Boy game's 160x144 inside a Super Game Boy's 256x224, or else the core's size for it) is backed out of with `back` up to three times, then reported as `stuck`. `buttons` may be a list of presses made in turn, and `first` a few made once before (to set a menu's cursor). `begin` waits so many frames for the mode to begin (a fight's opening plays before its flag is set); a mode that doesn't hold is said to have never begun. Its presses, backs and why it stopped go into the record of calls |
| `steps` | Several moves in one call: hold buttons, then wait (frames, or until something as `run_until`), repeat, snap (also every few frames of a wait), take pictures. The pictures come back as one sheet. With a script loaded: stop repeating when it says something (`until_event`) or has said nothing for some presses (`until_quiet`), and steps to do whenever it does (`on`: press down and confirm at each choice) |
| `screenshot` | The picture now |
| `read` | Memory by address, a region by offset; also as text in the game's own codes, through a table and rules as `code_rule` words them; or as a script with text among its commands (`mixed`: text as text, other bytes as numbers, pointers marked). 4,096 bytes at most: a larger size is cut, and the answer says so (`cut`) |
| `find` | Where a text or bytes stand, in live memory or in snaps. A text is looked for in Shift-JIS, EUC-JP, UTF-16 either way, UTF-8, Shift-JIS as 16-bit little-endian numbers, and the game's own table when one is given; in parts too, when the whole isn't found. A hit with two characters or more of text standing right before it is said to be the middle of a line, with where the line begins (`mid_line`) |
| `text_runs` | The stretches of memory that read as Japanese, longest first: what to hold against the screen before any line is known |
| `find_relative` | A line read off the screen, looked for in codes of the game's own: where the same characters have the same code and different ones different codes. Gives the codes, the start of a table |
| `identify` | Which release a game file is: platform, id, and what names its build |
| `suggest_build_words` | Places that hold the same in snaps from power-on, the title and play, and the Lua lines that check them: a script's build check |
| `on_disc` | Whether a stretch of memory lies on the game's disc as it is, and where: if a place's text or font does, every place can be read from the disc without playing to it |
| `core_options` | The loaded core's options as it declares them, and what was given at load |
| `snap` | Keeps a moment under a name, and writes it to scratch as an inbox-style snapshot for `yomikata run` and `check` |
| `snap_lines` | Says what a snap's screen showed, once read off its picture: its lines, or that it has no text. With `screens`, a whole route's in one call |
| `sheet` | The pictures of several snaps as one image, each under its name; with `crop`, only the part the text is in, and with `number` each numbered: a route's screens read by the dozen |
| `repeat` | Does the same frames or steps again and says whether the game ended up the same: whether the core repeats itself |
| `differs` | How this moment's memory differs from a snap's, also one another harness wrote |
| `changed` | Places that differ between two snaps, the same as the first in some others and as the second in others: a flag, a counter, a cursor. Or places that go as a pattern says over snaps in order: `abab`, or `aAaA` for one value and anything else in turn |
| `grouped` | Places that hold the same in every state of a group and differ from group to group: which map it is, which chapter. States are loaded for a look and the game put back. `values` keeps only places that fit: "flag" (zero in some groups, not in the others) or each group's value; a list past 200 says it was cut |
| `entered` | Reads a name off a naming screen from a snap after each character: where the name is, each character's code, the rule kana codes follow, lines of a table |
| `refs` | Words that point at an address or just past it, in the console's byte order; over several snaps, which moments each points there in, or only those that point there in all and move (`moving`) |
| `cursor_find`, `cursor_to` | Where the game keeps a cursor the pad moves, found by moving it; and the cursor moved to a place in those numbers, a frame at a time until it is there |
| `walk_to` | Walks a character on a map to a place in the game's own numbers, one way at a time, the other way round when something is in the way; stops where the map changes, since the numbers start anew there. `near` for numbers finer than a step |
| `advancing` | Whole numbers that only go up, a little a frame, over snaps taken while a line types, and stand afterwards: a pointer walking through a script, a count of characters drawn. For a game whose text no search finds. Numbers at any address, odd ones too |
| `steady` | Numbers that go up or down by the same amount from each snap to the next: a hand going round, a bar filling, a timer. What `press_at` watches |
| `press_at` | Presses a button when a number in memory comes to a value, several in turn: a press in time (a ring's hand over its mark, a bar that fills). With `tries`, lists of values are tried from this moment, the game put back after each, and what the game held afterwards is given for each |
| `place_find` | Where a game of three dimensions keeps the character's place: floats that stand while the pad is let go, move with a push and come back with the push the other way, grouped three on end |
| `walk_3d` | Walks the character of a game of three dimensions to a place on the ground with the left stick: learns from two pushes which way the stick moves the two numbers `place_find` gave (the camera may be turned), pushes toward the place, learning again from its own pushes as the camera turns, and stops when near, when a push no longer brings it nearer (a wall, a talk begun), or at the limit |
| `explore` | Finds where the character can walk by trying it: pushes each way from where it stands, goes back, and does the same from every place it got to. Gives the places as rows of a map in the game's numbers, with the doors it met, and leaves the game where it was; given a place, puts the game there as it was reached while trying, the pushes going into the record of the way. For ledges, fences and mazes, and for a map too dark to read. With `fight`, presses through the fights its steps bring and maps on |
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

The check's own cost: `sheet` with `crop` and `number` puts two dozen screens on one picture, and `snap_lines`
with `screens` takes what you read off them in one call (a snap's name to its lines, or to false for a
screen without text). Fate/stay night's comparison of 36 screens was 39 calls with a `snap_lines` for each
screen, and is 4 with `screens`: of that game's 136 calls to its bar, 35 were this.

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
4. A game whose script lies packed (compressed, or in codes of its own with dictionary words) may unpack each
   page whole into a buffer before its window shows it. Snap three pages or more, two snaps or more while each
   stands and one just before each opens: `page_buffer` gives the stretches the pages differ in that held still
   while each stood, a buffer kept twice as two stretches that alternate, and when each page was written. A
   stretch that also moves while a page stands (a tile copy, text scrolled through) is ranked down, one that never
   stands left out. It gives bytes only: `find_relative` on its address, or a table, reads them. Final Fantasy V's
   page buffer (4 rows of 16 cells, `0x7E19D3`) is such a one.
5. Surer than one line: `lines`, two or more snaps each with what its screen shows at the same place. One
   place must fit them all, and the characters the lines share must have the same code, so lines without a
   repeat will do.
6. A script read where it lies keeps each line somewhere else, so two moments share no place: `apart`, several
   lines of one scene looked for together in one memory, each wherever it is. Two of them must share three
   characters or more. Give kana stretches: a game may keep kanji in two bytes, and a name the player chose
   as a code that stands for it, and either breaks a line that is taken as one code a character.
7. A game that lets the player enter a name tells its codes outright. On the naming screen, snap, enter one
   character, snap, enter another, snap: the places that took a value one after the other are the name, and
   the values are those characters' codes. `entered` does this from the snaps and the characters. A few kana give the rule the rest follow (a row, an offset between
   hiragana and katakana), and with a handful of codes a plain `find` by bytes finds the dialogue. This is what
   found Final Fantasy IX's text, after every search by the line itself had failed.
8. The codes it gives are the start of the game's table. With them as a table, `find` (`table`) looks for
   other lines, and each new line adds characters.
9. A game may hold nothing that says which line is on screen once the line stands: it points at the line only
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
   A game of three dimensions keeps the place as numbers with a point, and `cursor_find`, which looks for
   whole ones, comes back with the pad's own state or nothing. `place_find` looks for floats instead and
   groups them three on end; `explore` then takes two of a group's addresses with `grid` (how many of the
   game's units make a cell) and, for a passage aslant of the pad's ways, `diagonals`. On Shadow Hearts the
   place is two floats side by side (0x00AB5F30 and the next; the game's floor is flat), found in one call.
   What that game taught besides: its hero turns before he walks, so a push of six frames goes nowhere and
   `explore` saw a strip of twenty places; with `step` 24 it saw the carriage. Try one long push by hand
   and look at the picture before believing a small map. And an explored map is of no use where walking
   brings fights at random: there one long push at a time, with presses between to get through a fight,
   went through four carriages in two sessions.
   A console without floats keeps the place as whole numbers, many units to a step, and with the camera
   turned every push moves both, which `cursor_find` can't follow. `place_find` with `kind` s32 (or s16, the
   default on PlayStation and Super Famicom; leaping numbers are left out, and a group of two that move
   and a height that doesn't comes first) finds them, and `explore` takes them with `grid`, `whole` and
   their `size`. Xenogears: x at 0x80112AC4 and z at 0x80112ACC, about 32 units a push of 20 frames, cells
   of 16; a house's whole floor was 529 places in 82 seconds, which fits because a kept state is packed.
   Read a place off an explored map with its `ruler` (a mark at every fifth column, numbered), not by
   counting characters: on Xenogears a passage was taken for nine columns east of where it was, and a walk
   "to" it found nothing. The map itself is the same each time from one state.
   With `to`, `explore` stops trying as soon as it is there, and tries the places nearest to it first: a walk
   across a room is a few seconds, and across Mother 2's hill (93 pushes) under a minute, where trying evenly
   ran out of time. A long `step` (16) with four ways covers ground fastest. What it can't see: a door whose
   change of map takes longer than a push (that game's house doors), and a way that leads away from the place
   before it leads to it costs the whole map first: walk such a way in legs. A start where no push moves the
   character (a message left open takes the pad: Dragon Quest I & II after a search) gives one place and says so
   (`still`); `close` with the button that closes the window presses it first, once, and the press stays in the
   record. Before it presses, a longer push is tried: a game may count a step only once it is over (Pocket
   Monsters Gin, about 26 frames after the press), and then a push of 16 frames sees nothing move with no window
   open at all. `still` says so and names the `step` to give (28 did there), and `close` presses nothing.
   Where walking brings fights at random, a push into tall grass may start one, and then that try ends there
   (Gin's first route came out smaller than it is). `fight` takes the fight's mode as `advance_through` does
   (address, equals or differs, buttons, hold, wait, most) and `begin`, the frames a fight's opening plays
   before its flag is set: a fight a step brings is pressed through where it began, and the map goes on from
   there. The answer counts the fights and lists each (`fought`: where, presses, why it stopped); a walk with
   `to` keeps their presses in the record of the way. A `begin` is waited after every new place's step, so
   a flag set at once (found with `grouped` against a snap of the opening) maps fastest.
   The words a place is read from may hold only on some maps: in Dragon Quest I & II they did in the first room
   and read 0xF8 and 0 after the stairs. After a change of map, push once by hand and read them before trusting
   a walk; `cursor_find` again where they don't move.
   A game may keep two sets of numbers for everything that walks: where each is on the map, and where each
   is drawn on screen. Chrono Trigger does (the map's at 0x7E1800 across and 0x7E1880 down, a word for each
   of 32 things, 16 to a pixel; the screen's at 0x7E0A00 and 0x7E0A80), and `cursor_find` gave the screen's,
   which stand still while the picture scrolls. Read both before and after a push to see which thing the pad
   moves and which numbers are which; the others in the list are the people to talk to, so nobody has to be
   found by eye. Give `walk_to` the map's numbers with `near` (a step there is 16, and a walk never lands on
   the very place), and `map_picture` the screen's as well (`screen_x_address`, `screen_y_address`), since the
   picture doesn't keep the character in the middle at a map's edge.
   Find the place in the first room you walk, before any walking by hand: on Mother (Famicom) 35 calls went
   on walks by eye about one house. There `cursor_find` gave where the hero is drawn (0x3B and 0x8E, a byte
   each), which hardly moves while the picture scrolls with him, and `explore` on them saw six places. The map's
   numbers came from one `steps` call of eight pushes (right, right, down, down, left, left, up, up; 16 frames
   each) with a snap after each, read outside the harness for the words that follow the pushes: x at 0x18 and
   y at 0x1A, little-endian, 64 a push. `explore` on them (`size` 2, `grid` 64 with `whole`, `step` 16) gave the
   upstairs hall, 43 places, in one call. A Famicom keeps no numbers with a point, so `grid` there needs
   `whole` (explore says so). To tell the rooms apart, hold snaps kept in each room against each other (or
   `grouped` with a state in each): in Mother none of the bytes that stand within a room and differ between
   them is a room's number; the rooms lie apart in one space of numbers (the hall's y is about 55 x 256, the
   sister's room's 251 x 256), so a door is a jump in the numbers, where `walk_to` stops, and no `map_address`
   is needed.
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

15. The easy case, for the measure of it: a game that loads a scene's script whole in a standard encoding and
   copies the line it shows to a fixed place. `text_runs` with a line up shows the script at once; the line
   is found twice more by a plain search of a snap, and the copy that changes with the screen (snaps a line
   apart) is the one to read. Ever17 is such a game, and its script met the bar in 25 runs of the harness and
   fourteen minutes (323 calls, half of them the screens' lines given to `snap_lines`, twice). What it still taught: a place that looks like the shown line may be the backlog, a line
   behind (hold it against the picture a line later, not only now); a speaker joined to a line ("name「…」")
   is better cut by something the game keeps than by the bracket (there a word holds the name's length; a
   count of lines with a name against words that are zero without one, over the route's snaps, found it in
   one call); and a visual novel may play itself: Ever17 starts in its auto mode, so the route needs no
   presses at all, only a wait `until` the script's next text.

16. A game may keep no line but its script's own: the story is a program, text among its commands, and
   nothing is copied anywhere as it is shown. `find` then gives one place for a line, in the program, and a
   search for cells that repeat as the line does finds no drawn copy. What moves is the program's place:
   snap a few lines apart and ask `refs` for the words that point into the program in every snap and at
   another place in some (`snaps`, a `span` over the stretch, `moving`). On six snaps of that game it gives
   two words, the one that is at a new place in all six first; it took the place of six snaps fetched and
   a pass over them written by hand. `watch` on such a word through a press shows what it does: Kamaitachi no Yoru 2's walks through a
   piece of text a character a frame and stands after it while the game waits. A script for such a game
   gathers the pieces the pointer passes and says them when it stands; pieces the program jumps over are
   never passed, which no reading of the program where it lies would know. Read the program around a line
   before writing anything (`read` with `mixed`: text as text, every other byte as <XX>, and `marks` for
   where the pointers stand): the single bytes between pieces
   are the game's rows, readings and names. And a name the player gave may be typed from a buffer of its
   own (the pointer leaves the program for it), so play past a naming screen before trusting a script.

17. A page may be in memory whole while the screen shows only its beginning: the game stops at a mark in it
   for a button and goes on. Reading the page then says too much too early. Look for what says how far the
   game has come: snap a few presses apart, two of them inside one page, and ask `refs` with `moving` for
   words that point into the page's stretch. Higurashi Matsuri keeps the page twice, and a word points just
   past the last mark reached in the copy the game reads; its script says each part of a page when that word
   has passed it. Two things that cost calls there: I searched for the middle of a line and took where it
   was found for where the page begins (search for a line's first words, or read the bytes before what
   `find` gives); and the marks are single ASCII letters, which a Shift-JIS character's second byte can
   also be, so a page is walked two bytes at a time and never searched for a letter. That game met the bar
   in 137 calls and 14 runs: `find`, `read` over snaps and with `mixed`, and `refs … moving` were one
   session of six calls where Kamaitachi no Yoru 2's same finding had been three sessions and two scripts
   written by hand.

18. A page that fills with lines (a novel that covers the picture): look for the page's own string before
   anything finer. `find` on the newest line and on the page's first line, in one call each, sorts the
   copies: a place that has both, the first before the newest, is the page; one that has only the newest
   is a line's own buffer. Fate/stay night keeps the page as one string made ready for drawing, and its
   script says what that string gains. The line's own buffer looked simpler and was wrong: a sentence
   that is several lines of the game's script went through it in one frame, and only its last line was
   ever seen there (a replay lost). Hold the first try against sentences that wrap. That game met the bar
   in 136 calls and 14 runs; the finding was two sessions of 15 calls, with `read … mixed` on six places
   at once. `refs … moving` found nothing there and wasn't needed, and `find`'s `mid_line` said "1
   character before" for every hit, a mark's last byte taken for text: it now wants two characters.

19. A cartridge game may never copy its text into work RAM: it draws each character from the script in the
   cartridge. Every search of memory then comes back empty, in any encoding and by any pattern, and `follows`
   finds no place that holds the character being drawn. What there is, is a pointer: snap every frame while
   a line types (`steps` with `every`), and ask `changed` for what takes a new value on each of those frames
   and stands afterwards (a pattern of different letters, then the same letter). Live A Live's is 16 bits
   at 0x7E01C6 with a count of characters beside it, and the bank a few bytes before; `read` at that
   address in the cartridge (0xD0…) showed the line, and the script reads it there. The codes were then a
   matter of a rule and a table: the font's kana stand in video memory in code order (`vram_picture`), a
   debug copy of the script printed every code it had no character for, and the kanji were read off three
   sheets of windows beside those codes. 77 kanji and a dozen signs took two sessions of sheets, 11 calls;
   Ryuu ga Gotoku 2's 602 glyphs, each a picture to be hashed, had taken some 150 calls of its 540.
   A script that reads the cartridge needs a host that gives it: it must say so and read nothing where
   the read comes back empty, never fail.

20. A window kept as tiles may not be in memory as a window: the game may keep only a list of writes to video
   memory (where on the tile map, how many cells, the cells), a row at a time. Shin Megami Tensei does,
   at 0x7E0102, and its voiced kana are two cells: the kana, and its mark in the row above, which is the
   next write in the list. Three things from that game. A search for the row with its spaces as characters
   found the cells in a snap fetched and searched by hand, where `find_relative` had refused it as "too
   little to go by" (twice a の and two spaces: the tool wants more than it needs, and that is a fault to
   mend). A buffer that looks like "the row being written" may be a list with a length in front of each
   entry: read the words before it. And what blinks rewrites: a script that says a row whenever the list
   changes says it at every blink of the waiting mark, so remember the row last said at each place.
   Its bar came at 177 calls in 24 runs; `find`'s `not_found` and the dumps cost 77 of them before the
   list was understood, a second naming screen that wanted another name than the first a few more.

What it finds may be the script where it lies, the buffer the game types into, or a copy of the screen's
tiles; which one changes as the line changes (`changed`, `run_until` on its first cell) says which to read.

Seen on games that already have scripts: `text_runs` gave both the script and the typed buffer of a game with
16-bit Shift-JIS cells; `find_relative` found a line of a game with its own 16-bit codes from two of its rows,
though not from all three, which have a colour code among them; and with two moments it found a tile window's
copy of the screen (its rows' two halves), not the window's own buffer, which had moved to another slot between
the two.

### A press in time

A game may ask for presses in time to win a fight, and no script is checked on a game one can't get through.
`press_at` makes them through the pad, from a number the game keeps: nothing is written.

1. Find the number. Start the thing that asks for the presses and let it run without pressing, with a snap
   every few frames (`steps` with `wait` and `snap`), and ask `steady` for what goes up or down by the same
   amount from snap to snap: the hand or the bar is among the first it gives. On Shadow Hearts' ring it is
   a float in degrees. It is on the heap: 0x012AFB24 in one fight and 0x00E6B4F4 in the next, so `steady`
   is asked again in each fight (one call) until what points at the ring is known.
2. Find the values, by what the game does after a press. `press_at` with `tries`, one value in each, and
   the number itself read some frames later (`wrap` for one that goes round): on that ring the hand goes on after a press in time and stands
   after one that wasn't, so 21 tries in one call showed the first mark (45 to 120 degrees). Then the
   second with the first fixed (`tries` of two values each: 175 to 235), and so on.
3. Where the last press ends the thing either way, nothing in the number says whether it was in time: look
   at the picture after pressing for good. Four runs found the third mark there.
4. `press_at` with `at` then makes the presses for good: three on that ring, the ring broken as the game
   shows for a full success, where pressing blindly had missed.

A number that goes round (an angle) may start past its mark: give `wrap` (360), and a value behind where the
number is now is waited for the next time round. Without it such a value is taken as already passed.

A fight has turns, and a press made while the other side moves is lost. On that game every other try at an
attack found no ring turning (`pressed` false, at once): harmless, and twice as many tries as attacks got
through its first two fights. What says "the menu is up" would make it exact, and wasn't looked for.

Marks belong to what they were found on (a weapon, a level of the game): find them again when the game
changes what it asks.

### No text in memory at all

When `find` gives nothing for a line just read off the screen it says so (`not_found`) with what to try.
`find_relative` is next. If that is empty too, don't search further: the game may keep no line and draw from
its cartridge or from a script it never copies. Snap every frame of one line's typing (`steps` with `wait`,
`every`: 1 and `snap`), and ask `advancing` with those snaps and one or two from after as `rest`. It gives
the whole numbers that only went up, a little a frame, and then stood. On Live A Live's fourteen snaps it
gives two: the count of characters drawn, and beside it the 16-bit pointer into the cartridge's script, which was away for two of the snaps (a row of dots is kept elsewhere) and is
found all the same. That is three calls where the search by hand had been about a hundred. Every offset into
the unit is looked at: Dragon Quest I & II keeps its pointer at an odd address (0xC80F), which a look at even
ones only had missed.

`advancing` reads the snaps one at a time and keeps only what the walk needs, so many snaps of a large memory
are fine: forty of a Dreamcast's 16 MB take about 160 MB, where holding them all took more than the
harness's container has (it died of it). It takes 400 snaps at most, and refuses, before reading any, a
memory too big to walk in 1 GB at the `unit` asked: then give `within` a stretch, or a larger `unit`.

When nothing advances it says so (`nothing_advances`): the game may put a window down whole, as tiles, and
then the thing to find is the window. Give `find_relative` a row as the screen has it, its spaces as plain
spaces, with `spaces`: true: each space is then a cell, and all of them the same tile, which is a repeat like
any other. A place that fits is the tile map, or the list of writes it is filled from: on Shin Megami Tensei a
row with one kana twice and two spaces, refused before as too little, gives the list and the map and nothing
else but the same two a byte off. Without `spaces` a row that finds nothing, or is too little, is tried with them.

Before concluding nothing is there, try one row with `spaces`, and look at `padded`. A window of fixed width
pads each row with blanks, so a row break stands for as many cells as the row is short of the width, and two
rows with a break are found nowhere. When they aren't, `find_relative` tries them padded to each width from
the longest row's and gives the widths that fit. Kaeru no Tame ni Kane wa Naru keeps its page so, two rows of
18 cells numbered by the page itself (0x54 a blank): the two rows with a break found nothing, padded to 18 they
found it. It was found in the end from a word on the stack in snaps taken every frame from a page's start.

A console may have more memory than its main one, and a game may keep its own state there. A Game Boy
Advance has 256 KB on the board (main memory here: what `find`, `find_relative` and the rest look through,
and what a snap's `.ram.gz` is) and 32 KB in the CPU, which each snap keeps beside it as the region `iwram`.
`advancing` looks through such a region too. Gyakuten Saiban: `find_relative` gave the script's text in board
memory by the order of its kana (16-bit codes), nothing in board memory changed while a line typed, and the
word that walks the script is in the CPU's memory (0x03003A74). It moves once for each character and the
game types one every few frames, so over snaps two frames apart it had moved in a few only: `least` 3 finds
such a one, where the default wants it to have moved in half the snaps. Make sure the snaps are of a line
typing: a press that changes the scene instead gives snaps of nothing. When a search needs a rule the tools
haven't got, the snaps can be taken once, densely (`every`), and studied outside the harness at no calls:
they are files in scratch. The whole of that game, from the first load to the bar, was 68 calls.

A window that scrolls keeps earlier rows on screen, and a script that says each line once says only what the
window gained. Counted from play against a screen's whole window such a script looks half wrong (Mother 2:
20 of 44) when it is right (43 of 44 by the rows gained). `compare` with `scrolls` does the sum: give each
screen all its rows with `snap_lines`, as for any other game, and each is held to the rows after those the
screen before it in its scene left standing. Mother 3 (two rows, scrolling) was checked both ways, by rows
worked out by hand and by `scrolls` on whole windows, and they agree.

What a live `compare` counts is what the script said when the snap was taken: after a fix to the script or its
table, the old snaps still hold the old script's sayings. `reread` with `live` runs the script as it now is over
each snap's memory instead, every snap the same way (those taken with no script loaded too), and the answer, the
bar and the record (`reread` in the call's outcome) count how many were re-read. It is a check of a fix before
the calls of a fresh run, not a way to the bar: the bar is never met with it, since it counts what was said in
play (when a page is said, whether it repeats), which a still snap can't show. It suits a script that reads a
whole page from where it stands; one that says a line only as it appears reads nothing from a still snap.

A snap keeps memory, not the cartridge, and a script whose build check reads the cartridge's header (a Super
Famicom's at `0xC0FFC0`) refused every snap under `compare` before it was given one. `compare` now gives it the
loaded game's cartridge, over snaps of that game only, as `docs/script-api.md` says ("Over a snap").

### Snaps on disk

A snap is written to `snaps/` in scratch as an inbox-style snapshot: `<name>.ram.gz`, a `<name>.<region>.gz`
for each region, `<name>.json` and `<name>.png`. The first snap after a game is loaded is whole. The later
ones are kept as what differs from that first one (`tools/src/yomikata/snapfile.py` has the form): the same
file names, read by everything here that takes snapshots (`run`, `check`, `compare`, `locate`, `where`,
`propose`, the harness's own tools). Measured over thirty snaps of a PlayStation game: 535 KB for the first and
some 60 KB for each of the others, where each was 500 KB before; over ten of a Super Famicom game, 31 KB and
then 1.4 KB.

What follows from it: the later snaps need the first one beside them. Delete it and they say so instead of
reading wrongly. Taking a snap under a name that others are kept as differing from (the same prefix for two
stretches of one run, or a name from an earlier run) makes those others whole files first, and the answer
says so (`written_over`): nothing is lost, only disk. Before this it cost Mother 2 two scenes' snaps. `yomikata expand <files>` rewrites snaps as whole ones, for moving a few elsewhere or
for a tool that doesn't know the form. Snapshots a reader uploads to an inbox are never kept this way.

Snaps of every game share the one folder, and a snap's `.json` says which game took it. A name another game's
snap holds is refused, with an error that says to choose a prefix: a route's snaps written over by another
game's can't be compared again. (Gyakuten Saiban 2's route, prefixes `ra` and `rb`, wrote over 15 of Ryuu ga
Gotoku 2's that way.) A snap from before the game was written beside it is told by its console only. In
`steps` the refusal comes at the first such snap, with the game played up to it: give each game's snaps a
prefix of their own from the start (`g2-`, `m3-`).

### Walking in three dimensions

In a game of three dimensions the stick moves the character the way the camera faces, and the camera turns, so
pushes chosen by eye run into walls or set off a scene again. `place_find` with `stick` gives the floats that
followed the pushes, three on end: the two that moved are the place on the ground, the one that stood is the
height (several copies of one place are usual: the character, the camera's target). `walk_3d` takes the two
and a place in them: two pushes teach it which way the stick moves them, and it steers there, refitting the way
from its last pushes as the camera turns, and leaving out pushes that didn't move (a wall). It stops and says so
when it gets no nearer. A talk or a scene that begins on the way stops it too; the script's events are counted.

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

A font that lies in the cartridge as it is, with a code a glyph's number in it, is read whole and once, not a
kanji at a time in play: `yomikata rom-font`. It costs no call of the harness (it reads the game's file):

```sh
yomikata rom-font game.zip --find snaps/a-line.png --box 50,118,140,136     # where, and in which form
yomikata rom-font game.zip --at 0x1D312C --first 80 --count 256 --sheet /scratch/work/font/0080.png
yomikata rom-font game.zip --at 0x1D312C --first 80 --count 1352 \
    --read rows.txt --known games/gba/AGB-ASBJ.tbl --tbl new.tbl                # the rows read, held against play
```

`--find` cuts 8x8 pieces out of a picture of the game's text and looks for each in the cartridge as tiles of
4 bits in any colour and as a bit a pixel; a font shows as pieces found once each, near each other. A glyph
narrower than its row and drawn a few pixels into it has no piece that begins on a byte: the busiest pieces are
also looked for a bit at a time in rows of 16 and 24 bits (`rows of 16 bits`, with the `bit` each begins at).
Live A Live's kanji are so: 16x12, `--form rows1 --width 16 --height 12`, found only this way.

The Super Famicom keeps tiles as planes: `--find` also looks for its 8x8 tiles of 2 and 4 bits a pixel in any
one colour (`Super Famicom tiles of 2 bits, colour 1`), and for 12-pixel glyphs of a bit a pixel whose rows are
packed with nothing between them (`packed rows of 12 bits`). Draw them with `--form snes2` or `snes4` (a
glyph's 8x8 tiles in reading order: `--width 16 --height 16` is four) and `--form bits1 --width 12 --height 12`.

A game that keeps no code of what it shows, only the pixels of its window, is read off those pixels with the font
the cartridge holds. `--glyphs` writes the font as a dictionary of pictures, each glyph `--chars` gives a
character (a line a glyph: its code as on the sheet, `=`, the character), hashed as `lib/cells.lua` cuts a cell:
trimmed to its ink for a proportional font (`--fixed` keeps a glyph whole), `--cut X0:X1` for a font that keeps
two pictures of each glyph side by side, and a `# widths` line. The script reads its window as tiles
(`cells.picture`), cuts a row (`cells.fixed`, or `cells.proportional`, which takes the widest picture the
dictionary has at each glyph's first column with ink) and asks the dictionary (`lib/glyphs.lua`); a cell it
hasn't got is logged for `yomikata unread`, so kanji not given by `--chars` are read off the logged pictures:

```sh
yomikata rom-font game.zip --at 0x781D5 --count 1500 --form rows1 --width 24 --height 12 \
    --glyphs games/sfc/SHVC-AMXJ.glyphs --chars font-chars.txt --cut 0:12
```

A disc is read as its data track's sectors end to end (a .chd, .cue or .iso, or a raw track), so a place on it is
sector x 2048 + offset. Look for the font in a memory snap first (`rom-font snap.ram --find picture.png`: a
PlayStation game loads its font into memory as the disc has it), then `--locate snap.ram@ADDRESS` says where
stretches of memory from there lie on the disc and which start they agree on. Codes one byte below a lead byte
and two bytes after it (`--lead F8`: 00 to F7, then F800, F801, ...) are numbered so on the sheets, in `--first`,
in the rows read and in the table. Xenogears: the font is 16x11 a bit a pixel, 22 bytes a glyph, from sector
239438 + 1712 of the first disc, codes 10 to FE11; `--find` in a snap, `--locate` on the disc, sheets drawn from it.
Its last glyphs, and some slots in the middle of its JIS order, hold kanji the order doesn't predict (a slot the
game had no use for, given to one it had): `yomikata sorted` passes over them, and a sentence has to settle each. It gives
the neighbourhood, not the start: draw a stretch as a sheet, and move `--at` until a known character stands
whole and at its code (Gyakuten Saiban's glyphs begin 12 bytes off a tile's edge; a glyph drawn at every
4-byte offset shows which is right). Then a sheet for every 256 codes, each read a row a line (`rows.txt`: the
row's first code and its characters, `?` for one not read; a blank glyph takes no character). `--read` holds
the rows against the table read in play: every code both have, how many agree, those that don't, and any
character given to two glyphs. That is the check there is without a second reader: the same glyph read
twice, once in a sentence and once alone. Gyakuten Saiban: 1,351 characters from six sheets, no harness
calls; the 276 read in play all agree; the other 1,075 are read once. A font packed in the cartridge, or one
whose codes aren't glyph numbers, wants its own way in (`yomikata font` from video memory, `glyphs`).

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

A row may carry text among its pictures: what a script knows by code (kana, a fixed font) is logged as `=`
and the characters, and drawn on the rows' sheet with a font given as `--font`, greyer than the pictures. A
reading of such a row must have those characters where they stand, which catches a row read one place off.
`--most` takes only the first so many unread pictures, `--each` says how many rows are drawn for each (2
unless said), and a picture is labelled with as much of its hash as tells it from every other.

Where the text lies on the disc as it is loaded, nothing has to be played to: a script over the disc writes
the same lines a script in play would have logged. `tools/scripts/mgs_lines.py` does it for Metal Gear Solid:
1,439 distinct pictures of kanji in the lists and calls of its two discs, the most used first, each with the
two shortest lines it stands in. From that log the 250 most used after the 188 of the lines played were read,
which took the dictionary from 43 to 83 in 100 of all uses. Three things that reading taught. At twelve
pixels a kanji read in its sentence from a small sheet went wrong five times in 250 (a neighbour in a
compound taken for it, a likely word read into the row): what caught them was a second sheet, each picture
drawn large beside the character it was read as, and the dictionary's own notice that a character had been
given to two pictures. The second reader agrees on about half at this size and was no help as a judge. And
a reading is best given by its hash, a character at a time: nothing of the game's text is then typed anywhere.

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

Every call of a tool goes into `record/calls.jsonl` in scratch: when, an id for this run of the harness, the
game loaded, the tool, its arguments in short (a long text as its start and its length, a list as how many it
holds), whether it failed, and how long it took. `yomikata calls <scratch>` sums the record by game. It is how a
script's cost is known: the number of calls from the first to the comparison that met the bar.

A call is written as it begins (`ok` still null) and again with its answer, the two lines sharing a `call` id,
and read as one. So a call whose process dies in it (out of memory, or a core the harness gives up on) is in
the record all the same, as a call that failed (`died`), and counts against the budget. A call counts itself
from its start: `budget`'s own answer includes it.

A comparison by scenes leaves its outcome with its call (whether the bar was met, on how many screens and
scenes: counts only), and `yomikata calls <scratch> --trend` prints, for each game in the order they were
begun, the calls, the runs of the harness and the minutes on the clock from the game's first call to the
first comparison that met its bar, and the calls made after. It is a Markdown table: it goes into a game's
pull request, so that what the tools gained from one game can be seen in what the next one cost. A record
from before outcomes were kept has the last comparison by scenes instead, and says the screens aren't in it;
a game without one yet is counted as far as it has got. The minutes are the clock's, pauses and all: they
say how long a script took to make, and `tool seconds` how much of that the game was running.

A budget is kept by the harness, not by whoever is counting. Every answer of every tool ends with the game's
`calls_so_far`, read from the record (so it is the number `yomikata calls` gives, over every run of the
harness, and a run's own load and state_load are in it: a run is three or four calls before it does anything
unless `load` is given its `state`). `budget` with `calls` sets the most a game may have in all
(`record/budgets.json`); the answers then carry `budget` and `left`, and at the budget the harness runs
nothing more for that game and says so. A refused call isn't in the record. Set it when a game is begun,
to what was agreed, and raise it only when whoever set it has said so. Two games went past theirs before
this: Shadow Hearts (284 calls for about 150) and Xenogears (420 for 300), both by counting runs.

**Playing outside the harness is playing.** A game run anywhere but the harness, to explore it or find a
route, is part of what its script cost, and goes into the same record. `yomikata play <plan.json>` runs the
harness's own tools from a plan, with nobody at the other end: a JSON list of `[tool, arguments]` as the
harness takes them, the first a `load`, e.g. a `steps` of a few hundred presses with snaps and pictures. What
the tools answer is printed a line each, and their pictures go into `--out`. Each call of the plan is a line
in the record (`offline_play`, with the tool it was and the frames run so far), so it costs what the same call
costs in the harness, and the run is one run. A plan whose calls don't fit in what is left of the game's budget
isn't run at all. A plan can't set a budget, nor `compare`: those stay the harness's.
Run it in the harness's container, as the harness's user, with the same mounts (the record is appended a line
at a time with one write, so a harness and a play writing at once never mix their lines). Tengai Makyou II's
route was found by 59 runs of a core outside the harness before this existed (about 600,000 frames); its
entry counts them beside its calls, by hand.

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

When the calls are over (the input ends) the game is unloaded and the core ended, as a frontend does. A core
that doesn't come back from that within 5 seconds is left: the harness says so on standard error and leaves
anyway, after putting what it wrote (the record, states, snaps) on the disk. flycast's end kept the harness
until its timeout before this. `yomikata play` ends the same way.

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

### Saturn

mednafen_saturn describes no memory map, and of the libretro memories gives the work RAM (2 MB: the low 1 MB,
then the high 1 MB, as 16-bit words in the host's order) and the cartridge's save RAM, nothing else. The
harness reads the work RAM at the console's addresses and big-endian (script-api.md); `find` and the other
tools take 2- and 4-byte numbers big-endian on a Saturn. VDP1's and VDP2's memory can't be read: a game that
draws its text as VDP1 sprites from glyphs it puts in VDP1's memory, and keeps no codes in work RAM, can't be
read by a script here (Sakura Taisen, parked for that: its line is in no encoding in work RAM or on the disc,
the counters that go up as a page types are the sprite list's, and its font was found neither in RAM nor on
the disc). yabasanshiro doesn't load in the tools image or the harness image: it wants `libGL.so.1`, which
neither has.

### N64

mupen64plus_next describes no memory map; of the libretro memories it gives the RDRAM (8 MB, as 32-bit words
in the host's order, and only once the game has run a frame) and the save memory. The harness reads the RDRAM
at the console's addresses, 0x80000000 on and 0xA0000000 past the cache, big-endian (script-api.md). It
needs the harness image with `libGL.so.1` (#170).

**Its renderer.** The harness loads mupen64plus_next with `mupen64plus-rdp-plugin` set to `angrylion` and
`mupen64plus-angrylion-sync` to `High`, unless `load` is given others: GLideN64, the core's own choice, draws with OpenGL and here (Mesa, no window) gives a black
picture on many frames once a game has used the framebuffer for an effect, until the core is loaded again. On
Doubutsu no Mori that began at its name keyboard (whose background GLideN64 drew garbled): 19 of 50 pages snapped
from power-on had black pictures, though their memory was right; loaded from a state, every frame was drawn; with
angrylion, which draws on the processor, none of the 54 was black. Copying each frame GLideN64 presents aside at
once didn't help: the frames it presents are black. angrylion gives the picture at 640x240. With its thread sync at
Low (the core's own choice) its picture fell some frames behind memory, so a page snapped as it stood showed half
typed; at High it keeps up. The game's frames were the same under both renderers: a route found with one replays
with the other. Measured again with angrylion at High, Doubutsu no Mori's 50 pages all agreed, and none of the
54 pictures was black.

**Buttons.** On this core the N64's A is the pad's `b` (the pad's `a` isn't it), Start is `start`, and the stick
is the pad's left stick (`stick` in `steps`); the other buttons weren't needed and aren't checked. A game's keyboard may be a dial rather than a grid: Doubutsu no Mori's
name and village keyboards choose a row of kana with the stick, and the N64's A (`b`) puts the kana the stick
points at; Start ends the name. Holding the stick up and pressing `b` gave a kana each time, which was enough to
get past both.

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

- A core may say where its memory is with a map (libretro's memory descriptors). The harness reads by it as
  libretro defines it: an address belongs to the first descriptor it agrees with in the `select` bits, the
  `disconnect` bits are taken out of it, and what is past the memory's length wraps. So a Super Famicom
  cartridge reads as the game sees it, LoROM too (bank n's 0x8000-0xFFFF is the file's n-th 32 KB, and banks
  from 0x80 on are the same again), and a read stops at a bank's end. Before this only a descriptor's first
  stretch read true. Checked on Dragon Quest V (LoROM) against the file at six addresses.

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
