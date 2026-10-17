"""The `ruru` command line: the primary interface, for Jesse and agents alike.

The agent contract (README, docs/json.md): no prompts, no stdin unless asked for (`-F -`), colour
only on a TTY without NO_COLOR, `--json` on every read command, `-q` for keys only, warnings and
progress on stderr, and the exit codes below.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence

from ruru import __version__

# Exit codes (§5 of the design brief; a public API, like the --json schema).
OK = 0
USAGE = 1  # usage error, or an unknown key or ID
NO_MATCH = 2  # nothing matched (an empty search, `check` found issues)
PARTIAL = 3  # some feeds or resolvers failed
LOCKED = 4  # the library lock is held by another ruru process

Handler = Callable[[argparse.Namespace], int]


class _Formatter(argparse.RawDescriptionHelpFormatter):
    """Keeps the examples in each epilog laid out as written."""


def _examples(*lines: str) -> str:
    return "examples:\n" + "\n".join(f"  {line}" for line in lines)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        # argparse exits 2 on usage errors; 2 means "nothing matched" here.
        self.print_usage(sys.stderr)
        self.exit(USAGE, f"{self.prog}: error: {message}\n")


def cmd_tui(args: argparse.Namespace) -> int:
    try:
        tui = importlib.import_module("ruru.tui")  # lazy: the core never imports Textual
    except ImportError:
        print("ruru tui needs the optional extra: pip install 'ruru[tui]'", file=sys.stderr)
        return USAGE
    return tui.run(args)  # pragma: no cover - lands in M6


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="ruru",
        description="A local, plain-text literature library with a feed inbox.",
        formatter_class=_Formatter,
        epilog=_examples(
            "ruru fetch && ruru inbox --json",
            "ruru search 'retention time' --json",
            "ruru COMMAND -h               exact flags for one command",
        ),
    )
    parser.add_argument("-V", "--version", action="version", version=f"ruru {__version__}")
    parser.add_argument("-L", "--library", metavar="PATH", help="library to use (default: from config)")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)

    p = sub.add_parser(
        "tui",
        help="interactive UI (optional extra: ruru[tui])",
        formatter_class=_Formatter,
        epilog=_examples("ruru tui", "ruru -L ~/papers tui"),
    )
    p.set_defaults(func=cmd_tui)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Handler | None = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return USAGE
    return func(args)
