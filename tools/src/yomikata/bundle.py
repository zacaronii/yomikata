"""One file per game: a script with the modules it asks for put into it (docs/script-api.md, "Files").

A script's source may say `require("lib.markup")` for a module in the catalog's `lib/`. A host never loads
anything but the script and its data, so what is run and published is the script with those modules inside it."""

from __future__ import annotations

import re
from pathlib import Path

REQUIRE = re.compile(r"""\brequire\s*\(?\s*(["'])([a-z0-9_.]+)\1\s*\)?""")
PRELUDE = ('local __loaded, __module = {}, {}\n'
           'local function require(name)\n'
           '    local value = __loaded[name]\n'
           '    if value == nil then\n'
           '        local make = __module[name] or error("module " .. name .. " isn\'t in this script", 2)\n'
           '        value = make()\n'
           '        if value == nil then value = true end\n'
           '        __loaded[name] = value\n'
           '    end\n'
           '    return value\n'
           'end\n')


class BundleError(ValueError):
    pass


def root_of(script: Path) -> Path | None:
    """The catalog a script is in: the nearest folder above it that has a lib/."""
    for folder in Path(script).resolve().parents:
        if (folder / "lib").is_dir():
            return folder
    return None


def modules(source: str, root: Path | None, found: dict[str, str] | None = None) -> dict[str, str]:
    """Every module a source asks for, and those they ask for, by name, in the order met."""
    found = {} if found is None else found
    for match in REQUIRE.finditer(source):
        name = match.group(2)
        if name in found:
            continue
        path = None if root is None else root / (name.replace(".", "/") + ".lua")
        if not name.startswith("lib.") or path is None or not path.is_file():
            raise BundleError(f"module {name} isn't in the catalog's lib/")
        found[name] = path.read_text(encoding="utf-8")
        modules(found[name], root, found)
    return found


def bundle(script: Path, root: Path | None = None) -> str:
    """The script as it is run: its header lines first, as a host reads them from the top, then its modules,
    then the rest. A script that asks for none comes back as it is."""
    source = Path(script).read_text(encoding="utf-8")
    wanted = modules(source, root or root_of(script))
    if not wanted:
        return source
    lines = source.splitlines(keepends=True)
    head = 0
    while head < len(lines) and (not lines[head].strip() or lines[head].lstrip().startswith("--")):
        head += 1
    parts = ["".join(lines[:head]), PRELUDE]
    for name, text in wanted.items():
        parts.append(f'__module["{name}"] = function()\n{text.rstrip()}\nend\n')
    parts.append("".join(lines[head:]))
    return "".join(parts)
