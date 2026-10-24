"""Paper repos: `project`, `cite` and `check`."""

from __future__ import annotations

import argparse
import sys

from ruru import ops
from ruru import projects as projmod
from ruru.bibtex import ENCODINGS
from ruru.cmdkit import Builder, keys_line, open_library
from ruru.errors import NO_MATCH, OK
from ruru.output import emit_json, style, warn


def cmd_project_list(args: argparse.Namespace) -> int:
    lib = open_library(args)
    configured = projmod.load(lib.settings.projects)
    if args.json:
        emit_json([{"name": n, **p.to_dict(lib.settings.projects[n].get("path"))} for n, p in configured.items()])
    elif args.quiet:
        for name in configured:
            print(name)
    else:
        for name, p in configured.items():
            tex = ", ".join(p.tex) or "no tex files"
            print(f"{style(f'{name:<20}', 'bold')} {p.path}  bib: {p.bib}  tex: {tex}")
    if not configured:
        if not args.json:
            print("no projects yet: `ruru project add NAME --path … --bib … --tex …`", file=sys.stderr)
        return NO_MATCH
    return OK


def cmd_project_add(args: argparse.Namespace) -> int:
    lib = open_library(args)
    ops.add_project(lib, args.name, path=args.path, bib=args.bib, tex=args.tex or [], encoding=args.bib_encoding)
    print(args.name if args.quiet else f"added project {args.name}")
    return OK


def cmd_project_remove(args: argparse.Namespace) -> int:
    lib = open_library(args)
    ops.remove_project(lib, args.name)
    print(args.name if args.quiet else f"removed project {args.name} (its papers keep the tag; files untouched)")
    return OK


def cmd_cite(args: argparse.Namespace) -> int:
    lib = open_library(args)
    result = ops.cite(lib, args.project, args.keys)
    project = ops.get_project(lib, args.project)
    for k in result.foreign:
        warn(f"{k} is in the managed block but not in the library; left as it was")
    if args.quiet:
        for k in args.keys:
            print(k)
    else:
        added = ", ".join(result.added) or "nothing new"
        what = "updated" if result.changed else "unchanged"
        print(f"{project.bib_path}: {what} (added: {added})")
    return OK


def cmd_check(args: argparse.Namespace) -> int:
    lib = open_library(args)
    report, fixed = ops.check(lib, args.target, fix=args.fix, prune=args.prune)
    for w in report.warnings:
        warn(w)
    if args.json:
        emit_json(report.to_json() | {"fixed": fixed})
    else:
        name = report.project or args.target
        print(f"{name}: {report.cited} cited key{'s' * (report.cited != 1)} in {len(report.tex)} tex file"
              f"{'s' * (len(report.tex) != 1)}; bib: {report.bib or '-'}")
        for what, keys in fixed.items():
            if keys:
                print(f"fixed: {what} {', '.join(keys)}")
        fixable = [m["key"] for m in report.missing if m["in_library"]]
        unknown = [m["key"] for m in report.missing if not m["in_library"]]
        if fixable:
            print(keys_line("missing from the bib, in the library", fixable, "ruru check --fix adds them"))
        if unknown:
            print(keys_line("missing from the bib and the library", unknown,
                             "ruru add DOI|arXiv id, then ruru check --fix"))
        if report.unused:
            print(keys_line("in the managed block, never cited", report.unused, "ruru check --prune drops them"))
        if report.kept_unused:
            print(keys_line("kept for this project, not cited", report.kept_unused))
        if report.stale:
            print(keys_line("out of date with the library", report.stale, "ruru check --fix regenerates them"))
        for a, b in report.duplicates:
            print(f"{style('duplicate', 'bold')}: {a} and {b} are the same paper")
        print(style("no issues", "green") if not report.issues else f"{report.issues} issue(s)")
    return NO_MATCH if report.issues else OK


def register(b: Builder) -> None:
    p = b.command("project", cmd_project_list, "paper repos that `cite` and `check` work on",
                ["ruru project",
                 "ruru project add joint-align --path ~/Desktop/joint-align --bib refs.bib --tex main.tex",
                 "ruru project remove joint-align"],
                [b.read])
    grp = b.group(p, "project_command")

    grp.command("list", cmd_project_list, "list projects", ["ruru project list --json"], [b.read])
    pp = grp.command("add", cmd_project_add, "add a paper repo (e.g. a local clone of an Overleaf project)",
                         ["ruru project add joint-align --path ~/Desktop/joint-align --bib references.bib "
                          "--tex main.tex",
                          "ruru project add thesis --path ~/thesis --bib refs.bib --tex thesis.tex "
                          "--bib-encoding utf8"],
                         [b.write, b.quiet])
    pp.add_argument("name")
    pp.add_argument("--path", required=True, help="the repo's directory")
    pp.add_argument("--bib", required=True,
                    help="its .bib file, relative to --path (created by `cite` if missing)")
    pp.add_argument("--tex", action="append", metavar="FILE",
                    help="a main .tex file, relative to --path (repeatable)")
    pp.add_argument("--bib-encoding", choices=ENCODINGS, help="override ruru.toml's bib_encoding for this project")
    pp = grp.command("remove", cmd_project_remove, "forget a project (its files are not touched)",
                         ["ruru project remove joint-align"], [b.write, b.quiet])
    pp.add_argument("name")

    p = b.command("cite", cmd_cite, "add papers to a project's .bib (inside ruru's managed block)",
                ["ruru cite joint-align wood2026joint guillevic2021automated",
                 "ruru search 'retention time' -q | xargs ruru cite joint-align"],
                [b.write, b.quiet])
    p.add_argument("project")
    p.add_argument("keys", nargs="+", metavar="KEY")

    p = b.command("check", cmd_check,
                "citation health: missing, unused, stale and duplicate entries (exit 2 on issues)",
                ["ruru check joint-align", "ruru check joint-align --fix", "ruru check paper/main.tex --json",
                 "ruru check joint-align --json || echo 'citations need attention'"],
                [b.read])
    p.add_argument("target", metavar="PROJECT|FILE.tex")
    p.add_argument("--fix", action="store_true",
                   help="cite missing keys the library has; regenerate stale entries")
    p.add_argument("--prune", action="store_true", help="drop managed entries that nothing cites")
