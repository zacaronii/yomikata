# Contributing

Anyone can add or fix an entry. Every step can be repeated with the tools in `tools/`, either installed with pip or
through the Docker image (which includes `chdman` for CHD images).

```sh
docker build -t yomikata-tools tools/
alias yomikata='docker run --rm -v "$PWD:/catalog" -v /path/to/your/games:/games:ro yomikata-tools'
```

## Adding an entry by hand

1. **Identify the release:** `yomikata identify "/games/Some Game (Japan).chd"` prints its platform, id and
   executable hash.
2. Create `games/<platform>/<id>.json` (copy an existing entry). Fill in what you know; `encoding` alone is fine.
3. If the game uses its own table, add `tables/<platform>/<id>.tbl` (and shared pieces under `tables/`).
4. Run `yomikata validate` and `yomikata index`, then open a pull request.

In the pull request, say how you know: tested in play (status `verified`), built with tools (`auto`), or partly
known (`partial`). A few decoded sample lines in the **pull request description** help review; never put script
text in the repository.

## Adding a hook you found

Follow [docs/hook-finder.md](docs/hook-finder.md). Either send the report to an inbox, or add the hook to the
entry yourself: `hooks[]` with `address`, `register`, `charset` and the exe SHA-1 it was checked on in `versions`,
plus a `sources` line saying how many lines matched OCR.

## Rules

- **No game data in the repository:** no script text, fonts, images, executables or disc files. Only mappings,
  hashes, addresses and rules. `yomikata validate` refuses other file types and long table values.
- **Nothing about your own setup:** no paths, hostnames, server names or account details in entries or notes.
- **Credit sources** in `sources` and respect their licences (translation-project tables, published hooks).
- **One release per entry.** If a table fits several printings, list each in `versions`; if it doesn't, make a
  separate entry.

## Automated contributions

Pipelines that sort a library or build tables (see `docs/roadmap.md`) open one pull request per platform pass for
encoding-only entries, and one per game for built tables, with a report (classification, coverage, a few sample
lines in the description, anything flagged).
