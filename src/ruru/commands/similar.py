"""Finding things that resemble each other: `related`, `dupes`, and the `index` they are computed from."""

from __future__ import annotations

import argparse
import sys

from ruru import index as indexmod
from ruru import ops
from ruru.cmdkit import Builder, open_library
from ruru.errors import NO_MATCH, OK, PARTIAL
from ruru.output import emit_json, style


def cmd_related(args: argparse.Namespace) -> int:
    lib = open_library(args)
    _, found = ops.related(lib, args.key, n=args.number, scope=args.scope)
    if args.json:
        emit_json([{"id": r.id, "kind": r.kind, "similarity": round(r.similarity, 3), "title": r.paper.title,
                    "authors": r.paper.authors, "year": r.paper.year, "venue": r.paper.venue,
                    "status": r.paper.status if r.kind == "library" else None} for r in found])
    elif args.quiet:
        for r in found:
            print(r.id)
    else:
        for r in found:
            where = style(f"{r.id:<28}", "bold") if r.kind == "library" else style(f"{'inbox:' + r.id:<28}", "dim")
            print(f"{r.similarity:4.2f}  {where} {r.paper.year or '----'}  {r.paper.title}")
    if not found:
        if not args.json:
            print(f"nothing resembles {args.key}", file=sys.stderr)
        return NO_MATCH
    return OK


def cmd_dupes(args: argparse.Namespace) -> int:
    lib = open_library(args)
    pairs = ops.duplicate_pairs(lib, with_inbox=not args.library_only)
    titles = {p.key: p.title for p in lib.papers()}
    if not args.library_only:
        from ruru.inbox import Inbox
        titles |= {i.id: i.paper.title for i in Inbox(lib.root).items()}
    if args.json:
        emit_json([p.to_json() | {"a_title": titles.get(p.a), "b_title": titles.get(p.b)} for p in pairs])
    elif args.quiet:
        for p in pairs:
            print(p.a, p.b)
    else:
        for p in pairs:
            tag = style("same", "red") if p.same else style("near", "yellow")
            print(f"{p.similarity:4.2f} {tag}  {style(p.a, 'bold')}  ~  {style(p.b, 'bold')}")
            print(f"            {titles.get(p.a, '')}\n            {titles.get(p.b, '')}")
    if not pairs:
        if not args.json:
            print("no near-duplicates", file=sys.stderr)
        return NO_MATCH
    return OK


def _progress(key: str) -> None:
    print(f"ruru: reading the PDF of {key}", file=sys.stderr)


def cmd_index_status(args: argparse.Namespace) -> int:
    lib = open_library(args)
    st = lib.index.status()
    if args.json:
        emit_json(st.to_json())
    elif args.quiet:
        print(st.path)
    else:
        size = f"{st.size / 1024:.0f} KiB" if st.size else "not built yet"
        print(f"{st.path}  ({size})")
        print(f"word counts: {st.papers} papers, {st.inbox} inbox items")
        print(f"PDF text: {st.pdfs} read" + (f", {st.pdfs_failed} failed" if st.pdfs_failed else "")
              + ("" if st.fts else "  (no FTS5 in this SQLite: searches scan)"))
    return OK


def cmd_index_update(args: argparse.Namespace, *, rebuild: bool = False) -> int:
    lib = open_library(args)
    if rebuild:
        lib.index.clear()
    update = ops.update_index(lib, pdfs=args.pdfs, retry=getattr(args, "retry", False), progress=_progress)
    failed = update.pdfs.failed if update.pdfs else {}
    indexmod.warn_failures(failed)
    if args.json:
        emit_json({"papers": update.papers, "inbox": update.inbox,
                   "pdfs": None if update.pdfs is None else {
                       "extracted": update.pdfs.extracted, "failed": failed, "current": update.pdfs.current}})
    elif not args.quiet:
        if update.pdfs:
            done = f"; read {len(update.pdfs.extracted)} PDF(s), {update.pdfs.current} already current"
        else:
            done = " (PDF text is slower: `ruru index update --pdfs`)"
        print(f"indexed {update.papers} papers and {update.inbox} inbox items{done}")
    return PARTIAL if failed else OK


def cmd_index_rebuild(args: argparse.Namespace) -> int:
    return cmd_index_update(args, rebuild=True)


def register(b: Builder) -> None:
    p = b.command("related", cmd_related, "papers closest to one (by title and abstract), best first",
                  ["ruru related guillevic2021automated", "ruru related guillevic2021automated -n 5 --json",
                   "ruru related arxiv-2610.01234 --in all      (an inbox item, against everything)"],
                  [b.read])
    p.add_argument("key", metavar="KEY|ID", help="a library paper or an inbox item")
    p.add_argument("-n", "--number", type=int, default=10, metavar="N", help="at most N (default 10)")
    p.add_argument("--in", dest="scope", choices=("library", "inbox", "all"), default="library",
                   help="where to look (default: the library)")

    p = b.command("dupes", cmd_dupes,
                  "near-duplicates: the same work under different ids (a preprint and its published version)",
                  ["ruru dupes", "ruru dupes --library-only", "ruru dupes --json"],
                  [b.read])
    p.add_argument("--library-only", action="store_true", help="leave the inbox out")

    p = b.command("index", cmd_index_status,
                  "the derived cache behind ranking and PDF search (.ruru/index.sqlite; safe to delete)",
                  ["ruru index", "ruru index update --pdfs", "ruru index rebuild"], [b.read])
    grp = b.group(p, "index_command")
    grp.command("status", cmd_index_status, "what the index holds", ["ruru index status --json"], [b.read])
    p = grp.command("update", cmd_index_update,
                    "recount changed papers and inbox items; --pdfs reads new PDFs too",
                    ["ruru index update", "ruru index update --pdfs", "ruru index update --pdfs --retry"],
                    [b.read])
    p.add_argument("--pdfs", action="store_true", help="also extract the text of new or changed PDFs (slow)")
    p.add_argument("--retry", action="store_true", help="with --pdfs: try PDFs that failed before")
    p = grp.command("rebuild", cmd_index_rebuild, "delete the index and build it again",
                    ["ruru index rebuild", "ruru index rebuild --pdfs"], [b.read])
    p.add_argument("--pdfs", action="store_true", help="also extract the text of every PDF (slow)")
