"""The `ruru` command line: the primary interface, for Jesse and agents alike.

The agent contract (README, docs/json.md): no prompts, no stdin unless asked for (`-F -`), colour
only on a TTY without NO_COLOR, `--json` on every read command, `-q` for keys only, warnings and
progress on stderr, and the exit codes in `ruru.errors`.

This module builds the parser and runs a command. The commands themselves live in
`ruru.commands.*` (one module per area, each with a `register(builder)`), shared helpers in
`ruru.cmdkit`, and everything that reaches outside the library (editor, browser, clipboard) in
`ruru.desktop`.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import os
import sys
from collections.abc import Sequence

from ruru import __version__, logo
from ruru.cmdkit import Builder, Formatter, Handler, Parser, examples
from ruru.errors import LOCKED, NO_MATCH, OK, PARTIAL, USAGE, NothingMatched, RuruError
from ruru.output import color_enabled

# The exit codes are re-exported: tests and scripts refer to cli.OK etc.
__all__ = ["LOCKED", "NO_MATCH", "OK", "PARTIAL", "USAGE", "build_parser", "main"]

# Registered in this order, which is the order `ruru -h` lists them in.
COMMAND_MODULES = ("library", "inbox", "similar", "projects", "claims", "reviews", "pdf", "system")


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="ruru",
        description="A local, plain-text literature library with a feed inbox.",
        formatter_class=Formatter,
        epilog=examples(
            "ruru init ~/papers --email you@example.org",
            "ruru add --bib refs.bib -t gc-ms",
            "ruru search 'retention time' --json",
            "ruru COMMAND -h               exact flags for one command",
        ),
    )
    parser.add_argument("-V", "--version", action="version", version=f"ruru {__version__}")
    parser.add_argument("-L", "--library", metavar="PATH", help="library to use (default: from config)")
    parser.add_argument("--no-commit", action="store_true", help="don't git-commit changes")
    builder = Builder.root(parser)
    for name in COMMAND_MODULES:
        importlib.import_module(f"ruru.commands.{name}").register(builder)
    return parser


def parse(argv: Sequence[str], parser: argparse.ArgumentParser | None = None) -> argparse.Namespace:
    """Parse one command line (without the leading `ruru`). Exits on usage errors, as argparse does."""
    from ruru.commands.pdf import PDF_ACTIONS

    parser = parser or build_parser()
    argv = list(argv)
    if len(argv) > 1 and argv[0] == "pdf" and argv[1] not in PDF_ACTIONS and argv[1] not in ("-h", "--help"):
        argv.insert(1, "show")  # `ruru pdf KEY` is short for `ruru pdf show KEY`
    return parser.parse_args(argv)


def dispatch(args: argparse.Namespace) -> int:
    """Run the parsed command, turning its errors into messages on stderr and an exit code."""
    func: Handler = args.func
    try:
        return func(args)
    except NothingMatched as e:
        print(f"ruru: {e}", file=sys.stderr)
        return e.code
    except RuruError as e:
        print(f"ruru: error: {e}", file=sys.stderr)
        return e.code
    except FileNotFoundError as e:
        print(f"ruru: error: {e.strerror}: {e.filename}", file=sys.stderr)
        return USAGE
    except BrokenPipeError:  # `ruru … | head`: the reader left early, which is not an error
        with contextlib.suppress(OSError, ValueError):  # stdout to /dev/null, so exit doesn't flush into the pipe
            devnull = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull, sys.stdout.fileno())
            finally:
                os.close(devnull)
        return OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parse(sys.argv[1:] if argv is None else argv, parser)
    if getattr(args, "func", None) is None:  # bare `ruru`: mascot and help on stderr (stdout is data only)
        print(logo.banner(color=color_enabled(sys.stderr)), file=sys.stderr)
        parser.print_help(sys.stderr)
        return USAGE
    return dispatch(args)
