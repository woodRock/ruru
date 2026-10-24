"""What every command module shares: the argparse classes, opening the library, JSON rows and the
registration helper that builds the parser."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from ruru.config import resolve_library
from ruru.errors import OK, PARTIAL, USAGE, RuruError
from ruru.library import Library
from ruru.model import Paper
from ruru.output import emit_json, paper_json, paper_line, style

Handler = Callable[[argparse.Namespace], int]


class Formatter(argparse.RawDescriptionHelpFormatter):
    """Keeps the examples in each epilog laid out as written."""


def examples(*lines: str) -> str:
    return "examples:\n" + "\n".join(f"  {line}" for line in lines)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        # argparse exits 2 on usage errors; 2 means "nothing matched" here.
        self.print_usage(sys.stderr)
        self.exit(USAGE, f"{self.prog}: error: {message}\n")


def open_library(args: argparse.Namespace) -> Library:
    auto_commit = False if getattr(args, "no_commit", False) else None
    return Library.open(resolve_library(args.library), auto_commit=auto_commit)


def rel_path(lib: Library, key: str) -> str:
    return str((lib.paper_dir(key) / "paper.md").relative_to(lib.root))


def exit_code(failures: list[RuruError], attempted: int) -> int:
    """All failed for one reason → that reason's code; anything else mixed → partial failure."""
    if not failures:
        return OK
    codes = {f.code for f in failures}
    return codes.pop() if len(failures) == attempted and len(codes) == 1 else PARTIAL


def paper_row(lib: Library, p: Paper, full: bool = False) -> dict[str, object]:
    return paper_json(p, path=rel_path(lib, p.key), has_pdf=lib.has_pdf(p.key), full=full)


def norm_tag(t: str) -> str:
    return "-".join(t.strip().lower().split())


def iso_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO date (YYYY-MM-DD): {s!r}") from None


def print_papers(lib: Library, papers: list[Paper], args: argparse.Namespace,
                 scores: list[int] | None = None, snippets: list[str | None] | None = None) -> None:
    """`scores` (a search) adds `score`; with `snippets` too, `in_pdf` and `snippet` (a PDF search)."""
    if args.json:
        rows = [paper_row(lib, p) for p in papers]
        if scores is not None:
            for i, (row, score) in enumerate(zip(rows, scores)):
                row["score"] = score
                snippet = snippets[i] if snippets is not None else None
                row["in_pdf"] = snippet is not None
                row["snippet"] = snippet
        emit_json(rows)
    elif args.quiet:
        for p in papers:
            print(p.key)
    else:
        for i, p in enumerate(papers):
            print(paper_line(p))
            if snippets is not None and snippets[i]:
                print(style(f"    pdf: …{snippets[i]}…", "dim"))


def keys_line(label: str, keys: list[str], hint: str = "") -> str:
    line = f"{style(label, 'bold')} ({len(keys)}): {', '.join(keys)}"
    return line + (f"\n    {style(hint, 'dim')}" if hint else "")


class Builder:
    """Adds subcommands to a parser. Every subcommand gets `-L`; `write`, `quiet` and `read` are the
    parent parsers for `--no-commit`, `-q` and `--json`. `group` nests a level (`ruru feeds add`)."""

    def __init__(self, sub: Any, common: argparse.ArgumentParser, write: argparse.ArgumentParser,
                 quiet: argparse.ArgumentParser, read: argparse.ArgumentParser) -> None:
        self.sub, self.common, self.write, self.quiet, self.read = sub, common, write, quiet, read

    @classmethod
    def root(cls, parser: argparse.ArgumentParser) -> Builder:
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("-L", "--library", metavar="PATH", default=argparse.SUPPRESS,
                            help="library to use (default: from config)")
        write = argparse.ArgumentParser(add_help=False)
        write.add_argument("--no-commit", action="store_true", default=argparse.SUPPRESS,
                           help="don't git-commit this change")
        quiet = argparse.ArgumentParser(add_help=False)
        quiet.add_argument("-q", "--quiet", action="store_true", help="print only keys")
        read = argparse.ArgumentParser(add_help=False, parents=[quiet])
        read.add_argument("--json", action="store_true", help="machine-readable output (docs/json.md)")
        sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=Parser)
        return cls(sub, common, write, quiet, read)

    def command(self, name: str, func: Handler, help: str, examples_: Sequence[str],
                parents: Sequence[argparse.ArgumentParser] = ()) -> argparse.ArgumentParser:
        p = self.sub.add_parser(name, help=help, description=help, formatter_class=Formatter,
                                epilog=examples(*examples_), parents=[self.common, *parents])
        p.set_defaults(func=func)
        return p

    def group(self, parser: argparse.ArgumentParser, dest: str) -> Builder:
        sub = parser.add_subparsers(dest=dest, metavar="ACTION", parser_class=Parser)
        return Builder(sub, self.common, self.write, self.quiet, self.read)
