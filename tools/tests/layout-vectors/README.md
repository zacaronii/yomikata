# Layout conformance vectors

A reader of `layouts` (docs/format.md, "Layouts") can check itself here against the reference reader,
`tools/src/yomikata/layout.py`. Each file is one made-up case: a layout, some memory, and what `read_layout` gives.
Nothing in them comes from a game.

| Key | |
|---|---|
| `name` | The file's name without `.json` |
| `checks` | What the case is there for |
| `entry` | The parts of an entry a layout reads: `tables` (paths), `unit`, `controls`, `cleanup` |
| `tables` | For each path in `entry.tables`, the table file's text |
| `layout` | One item of an entry's `layouts` |
| `memory` | Blocks of memory: `address` and the bytes as `hex`. A read has to lie inside one block; any other read fails |
| `expected` | What the reader gives: each field the layout names and could read, `on_screen` when both `message` and `shown` were read, and `errors` for the fields it couldn't read |

How to compare:

- Every key of `expected` must be there with the same value, and no others. `speaker` is a name (a string) or a
  number.
- For `errors`, only which fields failed counts. The messages are each reader's own.
- `\uXXXX` escapes are ordinary JSON; U+FFFD and private-use characters are written that way because they don't
  show otherwise.

Not covered: choosing between an entry's layouts by version (`pick`; the rule is in docs/format.md under "Which
layout", its cases in `test_layout.py`), passing over a layout with a key the reader doesn't know (same place),
and reading the entry's table files from disk.

The files are written by `tools/tests/layout_vectors.py`, where each expected value is written by hand and checked
against the reference reader. To change or add a case, edit it there and run it; a test fails if the files and the
script disagree.
