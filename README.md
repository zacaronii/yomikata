# Yomikata 読み方

**How to read each game's text.** A catalog of per-release reading rules for Japanese games: which encoding the
script uses, the character table when the game uses its own, control codes, cleanup rules and (for some platforms)
code hooks. Readers that pull text out of emulator memory, such as the Genkan launcher, use it to turn bytes into
text instead of falling back to OCR.

- One JSON entry per **release** (`games/<platform>/<id>.json`), keyed by the disc serial or cartridge code, with
  the hash of the boot executable it was made for. A table made for one printing can be wrong for another.
- Character tables in the ROM-hacking **Thingy** format (`tables/`), so tables from translation projects work as
  they are.
- `index.json`: every id → entry path and hash. Clients fetch it first, then only the entries they need.
- **No game data:** no script text, fonts, images or executables. Only mappings, hashes, addresses and rules.

| Document | |
|---|---|
| [docs/format.md](docs/format.md) | The entry and table formats, field by field |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Adding or fixing an entry, by hand or with the tools |
| [schema/entry.schema.json](schema/entry.schema.json) | JSON Schema every entry is checked against |
| [tools/](tools/) | `yomikata` command line: identify images, validate, build the index (Docker image included) |
| [docs/inbox.md](docs/inbox.md) | The inbox readers upload memory samples and hook reports to (`yomikata serve`) |
| [docs/hook-finder.md](docs/hook-finder.md) | How a reader app finds and checks a hook during play, and the report format |
| [docs/replay.md](docs/replay.md) | Running a game headless, from a reader's savestate or from power-on, and finding a layout in what it records (`yomikata replay`, `find`, `contrast`) |

## Using the catalog (clients)

1. Fetch `index.json` and cache it. Look up the running game's id (see "Identity" in the format docs).
2. Fetch `games/<platform>/<id>.json` (its `sha256` in the index tells you when to refetch) and any `tables` it lists.
3. If the running executable's SHA-1 isn't in the entry's `versions`, warn that the entry was made for another
   release rather than show garbage.
4. Decode: standard encodings directly; `table` entries by applying the tables in order with the entry's `unit`
   and `controls`; then apply `cleanup` in order.

## Status

Early. Entries so far are hand-made from testing in play; the plan for sorting a whole library automatically and
building tables from game fonts is in [docs/roadmap.md](docs/roadmap.md).

## Licences

Everything here, tools and catalog data (entries and tables), is under the GNU General Public License v3.0 or
later (`LICENSE`). Entries that adapt someone else's work credit it in `sources`: MIT-licensed hooks and tables can
be adapted into it as they are, and work under licences that conflict with the GPL doesn't belong here.
Third-party notices (copyright lines and licence texts for adapted work) are in [NOTICE.md](NOTICE.md).
