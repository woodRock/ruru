"""Small, comment-preserving edits to ruru.toml: append or remove one table, set one top-level list.

ruru.toml is Jesse's file (comments and all), so it is never rewritten wholesale: new tables are
appended, and removal deletes exactly one table's lines. Every result is re-parsed by the caller.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any

from ruru.config import toml_str


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(format_value(v) for v in value) + "]"
    return toml_str(str(value))


def format_table(header: str, values: dict[str, Any]) -> str:
    lines = [header] + [f"{k} = {format_value(v)}" for k, v in values.items() if v is not None]
    return "\n".join(lines) + "\n"


def append_table(text: str, header: str, values: dict[str, Any]) -> str:
    """`header` is the full header line: '[[feeds]]' or '[projects.joint-align]'."""
    return text.rstrip("\n") + "\n\n" + format_table(header, values)


_HEADER = re.compile(r"^\s*\[")


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) line ranges of each table, header included."""
    starts = [i for i, line in enumerate(lines) if _HEADER.match(line)]
    return [(s, starts[n + 1] if n + 1 < len(starts) else len(lines)) for n, s in enumerate(starts)]


def remove_table(text: str, header: str, name: str | None = None) -> str:
    """Remove the table with this header (for '[[feeds]]', the one whose `name` is `name`)."""
    lines = text.splitlines(keepends=True)
    for start, end in _blocks(lines):
        if lines[start].strip() != header:
            continue
        if name is not None:
            body = tomllib.loads("".join(lines[start + 1:end]))
            if body.get("name") != name:
                continue
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1  # keep the blank lines that separate the next table
        while start > 0 and not lines[start - 1].strip():
            start -= 1
        return "".join(lines[:start] + lines[end:]).rstrip("\n") + "\n"
    raise KeyError(name or header)


def set_list(text: str, key: str, values: list[str], comment: str) -> str:
    """Set the top-level `key = [...]`: its line is replaced where it is, or added (after `comment`)
    before the first table."""
    line = f"{key} = {format_value(values)}\n"
    lines = text.splitlines(keepends=True)
    first_table = next((i for i, x in enumerate(lines) if _HEADER.match(x)), len(lines))
    for i, x in enumerate(lines[:first_table]):
        if re.match(rf"^{re.escape(key)}\s*=", x):
            return "".join(lines[:i] + [line] + lines[i + 1:])
    before = "".join(lines[:first_table]).rstrip("\n")
    after = "".join(lines[first_table:])
    block = f"# {comment}\n{line}"
    return (before + "\n\n" if before else "") + block + ("\n" + after if after else "")


def remove_key(text: str, key: str, comment: str | None = None) -> str:
    """Remove the top-level `key = …` line (and the `# comment` line right above it, if given)."""
    lines = text.splitlines(keepends=True)
    first_table = next((i for i, x in enumerate(lines) if _HEADER.match(x)), len(lines))
    for i, x in enumerate(lines[:first_table]):
        if re.match(rf"^{re.escape(key)}\s*=", x):
            start = i - 1 if comment and i > 0 and lines[i - 1].strip() == f"# {comment}" else i
            end = i + 1
            if start > 0 and not lines[start - 1].strip() and end < len(lines) and not lines[end].strip():
                end += 1  # don't leave two blank lines behind
            return "".join(lines[:start] + lines[end:])
    return text
