"""The ruru mascot: a morepork, drawn once here and rendered three ways.

`ansi()`/`banner()` emit 256-colour escapes for the CLI (colour only when the caller says so),
`rich_text()` builds a Rich renderable for the TUI (Rich is imported lazily: the core never
depends on it), and `python -m ruru.logo --svg FILE` exports the hero for the README.

Moreporks have no ear tufts, a dark facial disc and yellow eyes, hence the round head, the
darker mask behind the eyes and the gold `◉`.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any

Row = Sequence[tuple[str, str]]  # (text, role)

# role -> (xterm-256 colour, bold)
PALETTE: dict[str, tuple[int, bool]] = {
    "body": (130, False),     # mid brown
    "light": (172, False),    # lighter plumage
    "mask": (94, False),      # facial disc, dark brown
    "eye": (220, True),       # gold
    "beak": (250, False),     # grey
    "spot": (223, False),     # pale speckles
    "feet": (214, False),     # orange
    "name": (220, True),
    "tagline": (130, False),
    "muted": (223, False),
}

HERO: list[Row] = [
    [("      ▄▄▄▄▄▄▄▄▄", "body")],
    [("    ▄█", "body"), ("▀       ▀", "mask"), ("█▄", "body")],
    [("   ██", "body"), ("  (", "mask"), ("◉", "eye"), (") (", "mask"), ("◉", "eye"), (")  ", "mask"),
     ("██", "body")],
    [("   ██", "body"), ("     ", "mask"), ("▼", "beak"), ("     ", "mask"), ("██", "body")],
    [("    █", "body"), (" ", "light"), ("░", "spot"), ("▒", "light"), ("░", "spot"), ("▒", "light"),
     ("░", "spot"), ("▒", "light"), ("░", "spot"), ("▒", "light"), ("░", "spot"), (" ", "light"), ("█", "body")],
    [("    ▀█", "body"), (" ", "light"), ("▒", "light"), ("░", "spot"), ("▒", "light"), ("░", "spot"),
     ("▒", "light"), ("░", "spot"), ("▒", "light"), (" ", "light"), ("█▀", "body")],
    [("      ▀▄▄▄▄▄▄▄▀", "body")],
    [("        ", "body"), ("▀▀", "feet"), ("   ", "body"), ("▀▀", "feet")],
]

SMALL: list[Row] = [
    [("   ▄▄▄▄▄▄▄", "body")],
    [("  █", "body"), (" (", "mask"), ("◉", "eye"), (")(", "mask"), ("◉", "eye"), (") ", "mask"), ("█", "body")],
    [("  █", "body"), ("   ", "mask"), ("▼", "beak"), ("   ", "mask"), ("█", "body")],
    [("  ▀", "body"), ("░▒░▒░▒░", "spot"), ("▀", "body")],
    [("    ", "body"), ("▀▀", "feet"), (" ", "body"), ("▀▀", "feet")],
]

MINI: Row = [("(", "body"), ("◉", "eye"), (",", "beak"), ("◉", "eye"), (")", "body")]

WORDMARK: list[Row] = [
    [],
    [("ruru", "name")],
    [("a plain-text literature library", "tagline")],
    [("fetch · triage · cite", "muted")],
    [],
]


def _ansi(text: str, role: str) -> str:
    colour, bold = PALETTE[role]
    return f"\033[{'1;' if bold else ''}38;5;{colour}m{text}\033[0m"


def plain(rows: Sequence[Row]) -> str:
    return "\n".join("".join(text for text, _ in row).rstrip() for row in rows)


def ansi(rows: Sequence[Row], color: bool = True) -> str:
    if not color:
        return plain(rows)
    return "\n".join("".join(_ansi(text, role) for text, role in row) for row in rows)


def _side_by_side(left: Sequence[Row], right: Sequence[Row], gap: int = 4) -> list[Row]:
    width = max(len("".join(t for t, _ in row)) for row in left)
    out: list[Row] = []
    for lrow, rrow in zip(left, right):
        pad = width - len("".join(t for t, _ in lrow)) + gap
        out.append([*lrow, (" " * pad, "body"), *rrow])
    return out


def banner(color: bool = True) -> str:
    """The small owl with the wordmark beside it (for `ruru` run with no command)."""
    return ansi(_side_by_side(SMALL, WORDMARK), color)


def rich_text(rows: Sequence[Row], *lines: str) -> Any:
    """A Rich `Text` of `rows`, followed by any extra `lines` in the muted colour (TUI panels)."""
    from rich.text import Text

    text = Text()
    for row in rows:
        for part, role in row:
            colour, bold = PALETTE[role]
            text.append(part, style=f"{'bold ' if bold else ''}color({colour})")
        text.append("\n")
    for line in lines:
        text.append("\n" + line, style=f"color({PALETTE['muted'][0]})")
    return text


def save_svg(path: str) -> None:
    from rich.console import Console

    console = Console(record=True, width=24, file=open("/dev/null", "w"))  # noqa: SIM115
    console.print(rich_text(HERO))
    console.save_svg(path, title="ruru")


if __name__ == "__main__":  # pragma: no cover - `python -m ruru.logo [--svg FILE]`
    if len(sys.argv) == 3 and sys.argv[1] == "--svg":
        save_svg(sys.argv[2])
    else:
        print(banner(color=sys.stdout.isatty()))
        print(ansi(HERO, color=sys.stdout.isatty()))
