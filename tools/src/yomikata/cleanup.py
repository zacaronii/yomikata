"""Reference implementation of `cleanup` (docs/format.md, "Cleanup"), so every client can check itself against it.

Rules apply in order to one decoded line: a hook's own rules first, then the entry's. `{regex, replace}` replaces
every match; inline `(?m)` / `(?s)` are allowed only at the very start; `$1`..`$9` in `replace` insert a group and
`$$` is a literal `$`. `{remap}` replaces single characters. A line that is empty after cleanup is dropped (None)."""

from __future__ import annotations

import re

FLAGS = re.compile(r"^\(\?([ms]+)\)")


def _compile(pattern: str) -> re.Pattern:
    flags = 0
    m = FLAGS.match(pattern)
    if m:
        flags = (re.M if "m" in m.group(1) else 0) | (re.S if "s" in m.group(1) else 0)
        pattern = pattern[m.end():]
    if "(?" in pattern.replace("(?:", "") or "(?<" in pattern:
        raise ValueError(f"pattern uses syntax outside the portable subset: {pattern!r}")
    return re.compile(pattern, flags)


def _replacement(replace: str):
    def fn(m: re.Match) -> str:
        return re.sub(r"\$(\$|[1-9])", lambda g: "$" if g.group(1) == "$" else (m.group(int(g.group(1))) or ""),
                      replace)
    return fn


def apply(text: str, rules: list[dict]) -> str:
    for rule in rules or []:
        if "remap" in rule:
            text = "".join(rule["remap"].get(c, c) for c in text)
        else:
            text = _compile(rule["regex"]).sub(_replacement(rule["replace"]), text)
    return text


def clean_line(text: str, entry: dict, hook: dict | None = None) -> str | None:
    """The line as a reader should show it, or None when cleanup leaves nothing (the line is dropped)."""
    out = apply(text, (hook or {}).get("cleanup", []))
    out = apply(out, entry.get("cleanup", []))
    return out if out.strip() else None
