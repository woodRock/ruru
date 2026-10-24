"""`ruru review`: syntheses of related reading-list papers."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from datetime import date
from pathlib import Path

from ruru import ops
from ruru.cmdkit import Builder, open_library
from ruru.errors import NO_MATCH, OK
from ruru.output import emit_json, style
from ruru.reviews import SORTS as REVIEW_SORTS
from ruru.reviews import STATUSES as REVIEW_STATUSES
from ruru.reviews import Review, Reviews


def review_json(r: Review, root: Path, body: bool = False) -> dict[str, object]:
    fm = r.front_matter()
    out: dict[str, object] = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in fm.items()}
    out["path"] = str(r.path.relative_to(root)) if r.path else None
    if body:
        out["body"] = r.body
    return out


def cmd_review_list(args: argparse.Namespace) -> int:
    lib = open_library(args)
    found = [r for r in Reviews(lib.root).all(args.sort) if not args.status or r.status == args.status]
    if args.json:
        emit_json([review_json(r, lib.root) for r in found])
    elif args.quiet:
        for r in found:
            print(r.id)
    else:
        for r in found:
            mark = style("●", "yellow") if r.status == "new" else " "
            line = f"{r.id:<50} {len(r.papers):>2} papers  {r.title}"
            print(f"{mark} {style(line, 'bold') if r.status == 'new' else line}")
    if not found:
        if not args.json:
            print("no reviews yet: see `ruru review add -h`", file=sys.stderr)
        return NO_MATCH
    return OK


def cmd_review_show(args: argparse.Namespace) -> int:
    lib = open_library(args)
    r = Reviews(lib.root).get(args.id)
    if args.json:
        emit_json(review_json(r, lib.root, body=True))
    else:
        print(f"# {r.title}\n\n{r.body}")
    return OK


def cmd_review_add(args: argparse.Namespace) -> int:
    lib = open_library(args)
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    r = ops.add_review(lib, text, by=args.by, replace=args.replace)
    print(r.id if args.quiet else f"filed review {r.id} ({len(r.papers)} papers): `ruru review open {r.id}`")
    return OK


def cmd_review_open(args: argparse.Namespace) -> int:
    lib = open_library(args)
    path = ops.render_review_file(lib, args.id)
    if not args.no_open:
        webbrowser.open(path.as_uri())
    print(path)
    return OK


def _review_triage(args: argparse.Namespace, decision: str) -> int:
    lib = open_library(args)
    for r in ops.triage_reviews(lib, args.ids, decision):
        print(r.id if args.quiet else f"{decision}: {r.id}")
    return OK


def cmd_review_keep(args: argparse.Namespace) -> int:
    return _review_triage(args, "keep")


def cmd_review_discard(args: argparse.Namespace) -> int:
    return _review_triage(args, "discard")


def register(b: Builder) -> None:
    p = b.command("review", cmd_review_list, "syntheses of related reading-list papers, triaged like an inbox",
                ["ruru review", "ruru review add draft.md --by claude",
                 "ruru review open 2026-10-18-robust-alignment", "ruru review keep 2026-10-18-robust-alignment"],
                [b.read])
    p.add_argument("-s", "--status", choices=REVIEW_STATUSES)
    p.add_argument("--sort", choices=REVIEW_SORTS, default=REVIEW_SORTS[0],
                   help="after new reviews: created (newest first, default), title, papers (most first)")
    grp = b.group(p, "review_command")

    rp = grp.command("list", cmd_review_list, "list reviews: new ones (marked ●) first, then newest",
                        ["ruru review list -s new", "ruru review list --sort title", "ruru review list --json"],
                        [b.read])
    rp.add_argument("-s", "--status", choices=REVIEW_STATUSES)
    rp.add_argument("--sort", choices=REVIEW_SORTS, default=REVIEW_SORTS[0],
                    help="after new reviews: created (newest first, default), title, papers (most first)")
    rp = grp.command("show", cmd_review_show, "print a review's Markdown",
                        ["ruru review show 2026-10-18-robust-alignment", "ruru review show ID --json"], [b.read])
    rp.add_argument("id")
    rp = grp.command(
        "add", cmd_review_add,
        "file a review (Markdown with front matter); every paper must be on the reading list",
        ["ruru review add draft.md --by claude", "cat draft.md | ruru review add - --by claude",
         "ruru review add draft.md --replace     (rewrite one that is still new)"],
        [b.write, b.quiet])
    rp.add_argument("file", metavar="FILE", help="the review (- for stdin); format in the ruru skill")
    rp.add_argument("--by", default=os.environ.get("RURU_AGENT") or None, help="author (default: $RURU_AGENT)")
    rp.add_argument("--replace", action="store_true", help="overwrite a review with the same id that is still new")
    rp = grp.command("open", cmd_review_open, "render a review as a web page and open it",
                        ["ruru review open 2026-10-18-robust-alignment", "ruru review open ID --no-open"])
    rp.add_argument("id")
    rp.add_argument("--no-open", action="store_true", help="only write the page and print its path")
    for name, func, what in (("keep", cmd_review_keep, "keep reviews (no longer new)"),
                             ("discard", cmd_review_discard, "delete reviews (git history keeps them)")):
        rp = grp.command(name, func, what, [f"ruru review {name} 2026-10-18-robust-alignment"], [b.write, b.quiet])
        rp.add_argument("ids", nargs="+", metavar="ID")
