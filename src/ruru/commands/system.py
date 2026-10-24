"""The tool itself: doctor, the TUI, `schema`, `batch` and `undo`."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ruru import ops, schema
from ruru.cmdkit import Builder, open_library
from ruru.config import resolve_library
from ruru.errors import NO_MATCH, OK, PARTIAL, USAGE, UsageError
from ruru.output import emit_json, style


def cmd_doctor(args: argparse.Namespace) -> int:
    from ruru import doctor

    checks = doctor.run(resolve_library(args.library), offline=args.offline)
    if args.json:
        emit_json([c.to_json() for c in checks])
    else:
        colour = {"ok": "green", "warn": "yellow", "fail": "red"}
        for c in checks:
            print(f"{style(f'{c.status:<5}', colour[c.status])} {c.name:<18} {c.detail}")
    return PARTIAL if any(c.status == "fail" for c in checks) else OK


def cmd_tui(args: argparse.Namespace) -> int:
    try:
        tui = importlib.import_module("ruru.tui")  # lazy: the core never imports Textual
    except ImportError:
        print("ruru tui needs the optional extra: pip install 'ruru[tui]'", file=sys.stderr)
        return USAGE
    return int(tui.run(args))


# --- schema --------------------------------------------------------------------------------------

def cmd_schema(args: argparse.Namespace) -> int:
    name = " ".join(args.name)
    if args.quiet:
        for n in (*schema.COMMANDS, *schema.names()):
            print(n)
        return OK
    if not name:
        out = schema.document()
    elif name in schema.COMMANDS:
        out = schema.for_command(name)
    elif name in schema.DEFS:
        out = schema.definition(name)
    else:
        raise UsageError(f"no schema {name!r}; `ruru schema -q` lists the commands and objects")
    emit_json(out)
    return OK


# --- batch ---------------------------------------------------------------------------------------

# What a batch may run: the commands agents are allowed to run one by one (README, Permissions), so a
# batch is never a way round a deny rule. (command, action): action None is the command with no action,
# which for the commands with actions means their listing.
BATCHABLE: frozenset[tuple[str, str | None]] = frozenset({
    *((c, None) for c in ("list", "show", "search", "find", "inbox", "related", "dupes", "seen", "claims",
                          "doctor", "export", "fetch", "suggest", "note", "tag", "skip", "unskip", "verdict",
                          "schema")),
    ("feeds", None), ("feeds", "list"), ("project", None), ("project", "list"),
    ("review", None), ("review", "list"), ("review", "show"), ("review", "add"),
    ("pdf", "missing"), ("pdf", "add"), ("pdf", "text"),
    ("own", None), ("own", "list"), ("cited", None), ("cited", "list"), ("cited", "bib"),
    ("index", None), ("index", "status"), ("index", "update"), ("index", "rebuild"),
})


def command_of(args: argparse.Namespace) -> tuple[str, str | None]:
    """(command, action) as the parser understood the line: `feeds add` and `feeds --json add` agree."""
    action = next((v for k, v in sorted(vars(args).items()) if k.endswith("_command") and k != "command" and v),
                  None)
    return str(args.command), action


def _exit_from(codes: Sequence[int]) -> int:
    failed = [c for c in codes if c not in (OK, NO_MATCH)]
    if not failed:
        return OK
    return failed[0] if len(failed) == len(codes) and len(set(failed)) == 1 else PARTIAL


def run_line(argv: list[str], root: Path, *, no_commit: bool = False) -> dict[str, Any]:
    """Run one command line in this process, capturing its output."""
    from ruru import cli

    out, err = io.StringIO(), io.StringIO()
    saved_stdin, code = sys.stdin, USAGE
    sys.stdin = io.StringIO("")  # a command that reads stdin gets an empty one, not the batch's
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                if not argv or argv[0].startswith("-"):
                    raise UsageError("start a line with the command (global options like -L are not allowed)")
                args = cli.parse(argv)
                if getattr(args, "func", None) is None:
                    raise UsageError("no command given")
                if (command := command_of(args)) not in BATCHABLE:
                    name = " ".join(x for x in command if x)
                    raise UsageError(f"`ruru {name}` is not available in a batch (run it yourself)")
                if getattr(args, "library", None) is None:
                    args.library = str(root)
                if no_commit:
                    args.no_commit = True
                code = cli.dispatch(args)
            except SystemExit as e:  # argparse: a usage error, or -h
                code = e.code if isinstance(e.code, int) else USAGE
            except UsageError as e:
                print(f"ruru: error: {e}", file=sys.stderr)
    finally:
        sys.stdin = saved_stdin
    text = out.getvalue()
    data: Any = None
    with contextlib.suppress(ValueError):
        data = json.loads(text) if text.strip() else None
    return {"argv": argv, "code": code, "stdout": text, "stderr": err.getvalue(), "data": data}


def cmd_batch(args: argparse.Namespace) -> int:
    lib = open_library(args)
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    results: list[dict[str, Any]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            argv = shlex.split(line)
        except ValueError as e:
            results.append({"argv": [line], "code": USAGE, "stdout": "", "data": None,
                            "stderr": f"ruru: error: line {lineno}: {e}\n"})
            continue
        if argv and argv[0] == "ruru":
            argv = argv[1:]
        results.append(run_line(argv, lib.root, no_commit=not lib.auto_commit))
        if args.stop_on_error and results[-1]["code"] not in (OK, NO_MATCH):
            break
    emit_json(results)
    return _exit_from([r["code"] for r in results])


# --- undo ----------------------------------------------------------------------------------------

def cmd_undo(args: argparse.Namespace) -> int:
    lib = open_library(args)
    found: list[tuple[ops.Commit, str | None]]
    if args.list:
        found = [(c, None) for c in ops.undoable(lib, 10 if args.number is None else args.number)]
    else:
        found = ops.undo(lib, 1 if args.number is None else args.number, dry_run=args.dry_run)
    if args.json:
        emit_json([c.to_json() | {"reverted_as": sha} for c, sha in found])
    elif args.quiet:
        for c, _ in found:
            print(c.sha[:10])
    else:
        verb = "would undo" if args.dry_run or args.list else "undid"
        for c, sha in found:
            print(f"{verb} {c.sha[:10]}  {c.message}" + (f"  (as {sha[:10]})" if sha else ""))
    if not found:
        if not args.json:
            print("nothing to undo: the newest commit is not ruru's", file=sys.stderr)
        return NO_MATCH
    return OK


def register(b: Builder) -> None:
    p = b.command("doctor", cmd_doctor, "check config, settings, git, files and API access (exit 3 on failures)",
                  ["ruru doctor", "ruru doctor --offline --json"],
                  [b.read])
    p.add_argument("--offline", action="store_true", help="skip the API checks")

    p = b.command("schema", cmd_schema, "JSON Schema for what --json prints (all of it, or one command or object)",
                  ["ruru schema", "ruru schema inbox", "ruru schema pdf missing", "ruru schema paper",
                   "ruru schema -q             the names"],
                  [b.quiet])
    p.add_argument("name", nargs="*", metavar="NAME", help="a command (`inbox`) or object (`paper`); default: all")

    p = b.command("batch", cmd_batch,
                  "run many commands in one process (one per line, as JSON results); only agent-safe commands",
                  ["printf 'show wood2026joint --json\\nsearch alignment --json\\n' | ruru batch -F -",
                   "ruru batch -F lookups.txt --stop-on-error"],
                  [b.write])
    p.add_argument("-F", "--file", metavar="FILE", required=True, help="the commands, one per line (- for stdin)")
    p.add_argument("--stop-on-error", action="store_true", help="stop at the first command that fails")

    p = b.command("undo", cmd_undo,
                  "revert ruru's last commit(s) with a new commit (your choice, not an agent's)",
                  ["ruru undo --list", "ruru undo", "ruru undo -n 3 --dry-run"],
                  [b.read])
    p.add_argument("-n", "--number", type=int, metavar="N", help="how many commits (default 1; --list: 10)")
    p.add_argument("--list", action="store_true", help="show what would be undone, newest first")
    p.add_argument("--dry-run", action="store_true", help="say what would be undone without doing it")

    b.command("tui", cmd_tui, "interactive UI (optional extra: ruru[tui])", ["ruru tui", "ruru -L ~/papers tui"])
