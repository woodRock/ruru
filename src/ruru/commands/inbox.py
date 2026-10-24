"""Papers arriving: feeds, fetch, the inbox and its triage, find."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from ruru import feeds, model, ops, rank
from ruru.cmdkit import Builder, iso_date, norm_tag, open_library
from ruru.config import INBOX_SORTS
from ruru.errors import NO_MATCH, OK, PARTIAL, UsageError
from ruru.inbox import DECISIONS, read_seen, read_viewed
from ruru.library import Library
from ruru.output import emit_json, item_json, item_line, paper_json, rank_fields, style, warn
from ruru.resolve import openalex


def _feed_list(lib: Library, names: Sequence[str] = ()) -> list[feeds.Feed]:
    configured = feeds.load(lib.settings.feeds)
    if not names:
        return configured
    by_name = {f.name: f for f in configured}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise UsageError(f"unknown feed(s): {', '.join(unknown)} (see `ruru feeds`)")
    return [by_name[n] for n in names]


def cmd_fetch(args: argparse.Namespace) -> int:
    lib = open_library(args)
    selected = _feed_list(lib, args.feed or [])
    if not selected:
        raise UsageError("no feeds yet: run `ruru feeds wizard` or `ruru feeds add …`")
    with ops.client_for(lib) as client:
        results, items = ops.fetch(lib, client, selected, since=args.since)
    for r in results:
        if r.error:
            warn(f"{r.name}: {r.error}")
    if args.json:
        emit_json({"feeds": [vars(r) for r in results], "new": [i.id for i in items]})
    elif args.quiet:
        for item in items:
            print(item.id)
    else:
        for r in results:
            status = style("failed", "red") if r.error else f"{r.new} new, {r.seen} already seen"
            print(f"{r.name:<24} {status}")
        print(f"{len(items)} new item{'s' * (len(items) != 1)}" + ("; see `ruru inbox`" if items else ""))
    return PARTIAL if any(r.error for r in results) else OK


def cmd_inbox(args: argparse.Namespace) -> int:
    lib = open_library(args)
    sort = args.sort or lib.settings.inbox_sort
    items, scores, dup = ops.ranked_inbox(lib, sort, wanted=not args.quiet)
    if sort == "relevance" and scores is None and items and not args.quiet:
        print(f"ruru: ranking needs {rank.MIN_LIBRARY}+ papers in the library; showing newest first",
              file=sys.stderr)
    if args.feed:
        items = [i for i in items if i.paper.source == f"feed:{args.feed}"]
    if args.suggested:
        want = None if args.suggested == "none" else args.suggested
        items = [i for i in items if (i.suggestion or {}).get("action") == want]
    if args.number:
        items = items[:args.number]
    if args.json:
        viewed = read_viewed(lib.root)
        emit_json([item_json(i, lib.root, viewed, rank_fields((scores or {}).get(i.id), dup.get(i.id)))
                   for i in items])
    elif args.quiet:
        for i in items:
            print(i.id)
    else:
        for i in items:
            print(item_line(i, (scores or {}).get(i.id), dup.get(i.id)))
    if not items:
        if not args.json:
            print("inbox is empty", file=sys.stderr)
        return NO_MATCH
    return OK


def _triage(args: argparse.Namespace, decision: str) -> int:
    lib = open_library(args)
    tags = [norm_tag(t) for t in getattr(args, "tag", None) or []]
    projects = getattr(args, "project", None) or []
    reason, by = getattr(args, "reason", None), getattr(args, "by", None)
    for item, key in ops.triage(lib, args.ids, decision, tags=tags, projects=projects, reason=reason, by=by):
        if args.quiet:
            print(key or item.id)
        else:
            print(f"{decision} {item.id}" + (f" -> {key}" if key else ""))
    return OK


def cmd_keep(args: argparse.Namespace) -> int:
    return _triage(args, "keep")


def cmd_later(args: argparse.Namespace) -> int:
    return _triage(args, "later")


def cmd_skip(args: argparse.Namespace) -> int:
    return _triage(args, "skip")


def cmd_unkeep(args: argparse.Namespace) -> int:
    lib = open_library(args)
    for item in ops.unkeep(lib, args.keys):
        print(item.id if args.quiet else f"back in the inbox as {item.id}: {item.paper.title}")
    return OK


def cmd_unskip(args: argparse.Namespace) -> int:
    lib = open_library(args)
    for item in ops.unskip(lib, args.ids):
        print(item.id if args.quiet else f"restored {item.id}: {item.paper.title}")
    return OK


def cmd_seen(args: argparse.Namespace) -> int:
    lib = open_library(args)
    records = [r for r in reversed(read_seen(lib.root))
               if (not args.decision or r.get("decision") == args.decision)
               and (not args.by or r.get("by") == args.by)
               and (not args.since or str(r.get("date", "")) >= args.since.isoformat())]
    if args.number:
        records = records[:args.number]
    if args.json:
        emit_json(records)
    elif args.quiet:
        for r in records:
            print(r.get("id"))
    else:
        for r in records:
            who = f"  ({r['by']}: {r.get('reason', '')})" if r.get("by") else ""
            key = f" -> {r['key']}" if r.get("key") else ""
            print(f"{r.get('date')}  {r.get('decision'):<6} {r.get('id')}{key}  {r.get('title', '')}"
                  + style(who, "dim"))
    if not records:
        if not args.json:
            print("no decisions match", file=sys.stderr)
        return NO_MATCH
    return OK


def cmd_suggest(args: argparse.Namespace) -> int:
    lib = open_library(args)
    item = ops.suggest(lib, args.id, args.action, args.reason, args.by)
    print(item.id if args.quiet else f"{item.id}: suggested {args.action}")
    return OK


def cmd_feeds_list(args: argparse.Namespace) -> int:
    lib = open_library(args)
    configured = feeds.load(lib.settings.feeds)
    state = feeds.load_state(lib.root)
    if args.json:
        emit_json([f.to_dict() | {"last_fetch": (state.get(f.name) or {}).get("last_fetch")} for f in configured])
    elif args.quiet:
        for f in configured:
            print(f.name)
    else:
        for f in configured:
            last = (state.get(f.name) or {}).get("last_fetch") or "never"
            print(f"{style(f'{f.name:<24}', 'bold')} {f.describe()}  {style(f'(fetched: {last})', 'dim')}")
    if not configured:
        if not args.json:
            print("no feeds yet: `ruru feeds wizard` or `ruru feeds add …`", file=sys.stderr)
        return NO_MATCH
    return OK


def cmd_feeds_add(args: argparse.Namespace) -> int:
    lib = open_library(args)
    feed = feeds.Feed(name=args.name, kind=args.kind, title=args.title, query=args.query, filter=args.filter,
                      search=args.search, issn=args.issn, url=args.url)
    ops.add_feed(lib, feed)
    print(feed.name if args.quiet else f"added feed {feed.name}: {feed.describe()}")
    return OK


def cmd_feeds_remove(args: argparse.Namespace) -> int:
    lib = open_library(args)
    ops.remove_feed(lib, args.name)
    print(args.name if args.quiet else f"removed feed {args.name}")
    return OK


def cmd_feeds_wizard(args: argparse.Namespace) -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise UsageError("`ruru feeds wizard` is interactive and needs a terminal; use `ruru feeds add …`")
    from ruru.wizard import Wizard

    lib = open_library(args)
    with ops.client_for(lib) as client:
        Wizard(client, feeds.load(lib.settings.feeds), lambda f: ops.add_feed(lib, f)).run()
    return OK


def cmd_find(args: argparse.Namespace) -> int:
    lib = open_library(args)
    with ops.client_for(lib) as client:
        found = openalex.search(client, " ".join(args.query), per_page=args.number)
    known = list(lib.papers())
    rows = []
    for p in found:
        match = next((k for k in known if model.same_paper(k, p)), None)
        best = (f"doi:{p.ids['doi']}" if p.ids.get("doi") else f"arXiv:{p.ids['arxiv']}" if p.ids.get("arxiv")
                else p.ids.get("openalex") or "")
        rows.append((p, best, match.key if match else None))
    if args.json:
        emit_json([{k: v for k, v in paper_json(p, path="", has_pdf=False).items()
                    if k in model.FIELDS and k not in ("key", "status", "tags", "projects", "added", "read_on",
                                                       "source")}
                   | {"id": best, "in_library": key} for p, best, key in rows])
    elif args.quiet:
        for _, best, _ in rows:
            print(best)
    else:
        for p, best, key in rows:
            where = p.venue or ("arXiv" if p.ids.get("arxiv") else "")
            print(f"{p.year or '----'}  {style(p.title, 'bold')}  {style(where, 'dim')}")
            authors = ", ".join(p.authors[:3]) + (" et al." if len(p.authors) > 3 else "")
            note = style(f"  [in library: {key}]", "green") if key else ""
            print(f"      {authors}  {best}{note}")
    if not rows:
        if not args.json:
            print("nothing found", file=sys.stderr)
        return NO_MATCH
    return OK


def register(b: Builder) -> None:
    p = b.command("fetch", cmd_fetch, "pull feeds into the inbox (new items only)",
                ["ruru fetch", "ruru fetch -f anal-chem -f arxiv-chem", "ruru fetch && ruru inbox --json",
                 "ruru fetch --since 2026-10-01 -q"],
                [b.read])
    p.add_argument("-f", "--feed", action="append", metavar="FEED", help="only this feed (repeatable)")
    p.add_argument("--since", type=iso_date, metavar="DATE",
                   help="look back to DATE instead of each feed's last fetch")

    p = b.command("inbox", cmd_inbox, "list unseen items, closest to your library first (or --sort date)",
                ["ruru inbox", "ruru inbox --sort date           newest fetch first", "ruru inbox --json -n 20",
                 "ruru inbox -f anal-chem -q",
                 "ruru inbox --suggested keep", "ruru show arxiv-2610.01234     (one item in full)"],
                [b.read])
    p.add_argument("-n", "--number", type=int, metavar="N", help="at most N items")
    p.add_argument("--sort", choices=INBOX_SORTS,
                   help="relevance: closest to the library first (needs a few papers); date: newest fetch "
                        "first (default: inbox_sort in ruru.toml, else relevance)")
    p.add_argument("-f", "--feed", metavar="FEED", help="only items from this feed")
    p.add_argument("--suggested", choices=[*DECISIONS, "none"], help="only items with this suggestion")

    for name, func, what in (("keep", cmd_keep, "inbox -> library (status to-read)"),
                             ("later", cmd_later, "inbox -> library, marked read later")):
        p = b.command(name, func, what,
                    [f"ruru {name} arxiv-2610.01234",
                     f"ruru {name} arxiv-2610.01234 doi-10.1021-x -t gc-ms -p joint-align"],
                    [b.write, b.quiet])
        p.add_argument("ids", nargs="+", metavar="ID")
        p.add_argument("-t", "--tag", action="append", metavar="TAG", help="repeatable")
        p.add_argument("-p", "--project", action="append", metavar="PROJECT", help="repeatable")

    p = b.command("skip", cmd_skip, "drop inbox items (remembered in seen.jsonl, never fetched again)",
                ["ruru skip arxiv-2610.01234", "ruru inbox -q --suggested skip | xargs ruru skip",
                 "ruru skip doi-10.1016-x --by claude --reason 'biosensor; off topic'   (an agent)",
                 "ruru unskip doi-10.1016-x                                             (undo)"],
                [b.write, b.quiet])
    p.add_argument("ids", nargs="+", metavar="ID")
    p.add_argument("--reason", help="why (recorded in seen.jsonl; agents should always give one)")
    p.add_argument("--by", default=os.environ.get("RURU_AGENT") or None,
                   help="who decided (default: $RURU_AGENT; unset means you)")

    p = b.command("unskip", cmd_unskip, "undo skips: put items back in the inbox (from git history)",
                ["ruru unskip doi-10.1016-j.foodchem.2026.151560",
                 "ruru seen -d skip --by claude -q | head -5 | xargs ruru unskip"],
                [b.write, b.quiet])
    p.add_argument("ids", nargs="+", metavar="ID")

    p = b.command("unkeep", cmd_unkeep, "move papers from the library back to the inbox, as new",
                ["ruru unkeep guan2026sphere",
                 "ruru list -p none -s to-read -q | xargs ruru unkeep     (refuses papers with notes or a PDF)"],
                [b.write, b.quiet])
    p.add_argument("keys", nargs="+", metavar="KEY")

    p = b.command("seen", cmd_seen, "past triage decisions, newest first (seen.jsonl)",
                ["ruru seen -n 20", "ruru seen -d skip --by claude --since 2026-10-17",
                 "ruru seen --json"],
                [b.read])
    p.add_argument("-d", "--decision", choices=(*DECISIONS, "remove"))
    p.add_argument("--by", metavar="NAME", help="only decisions made by this agent")
    p.add_argument("--since", type=iso_date, metavar="DATE", help="on or after DATE (YYYY-MM-DD)")
    p.add_argument("-n", "--number", type=int, metavar="N", help="at most N")

    p = b.command("suggest", cmd_suggest, "record a recommendation on an inbox item (for agents)",
                ["ruru suggest arxiv-2610.01234 keep --reason 'GC-MS alignment; relevant to joint-align'",
                 "ruru suggest doi-10.1016-x skip --reason 'LC-MS proteomics, off topic' --by claude"],
                [b.write, b.quiet])
    p.add_argument("id", metavar="ID")
    p.add_argument("action", choices=DECISIONS)
    p.add_argument("--reason", required=True, help="one line, tied to your projects")
    p.add_argument("--by", default=os.environ.get("RURU_AGENT", "agent"),
                   help="attribution (default: $RURU_AGENT or 'agent')")

    p = b.command("feeds", cmd_feeds_list, "list, add or remove feeds; or set them up interactively",
                ["ruru feeds", "ruru feeds wizard", "ruru feeds add anal-chem --kind crossref --issn 0003-2700",
                 "ruru feeds remove anal-chem"],
                [b.read])
    grp = b.group(p, "feeds_command")

    fp = grp.command("list", cmd_feeds_list, "list feeds and when each was last fetched",
                       ["ruru feeds list", "ruru feeds list --json"], [b.read])
    fp = grp.command("add", cmd_feeds_add, "add a feed to ruru.toml",
                       ["ruru feeds add arxiv-chem --kind arxiv --query 'cat:physics.chem-ph AND abs:GC-MS'",
                        "ruru feeds add anal-chem --kind crossref --issn 0003-2700 --title 'Analytical Chemistry'",
                        "ruru feeds add supervisors --kind openalex --filter 'authorships.author.id:A5077569089'",
                        "ruru feeds add rt-alignment --kind openalex --search 'retention time alignment'",
                        "ruru feeds add jat --kind rss --url https://example.org/feed.xml"],
                       [b.write, b.quiet])
    fp.add_argument("name", help="lowercase letters, digits and hyphens")
    fp.add_argument("--kind", required=True, choices=feeds.KINDS)
    fp.add_argument("--query", help="arxiv: an arXiv API search_query")
    fp.add_argument("--filter", help="openalex: an OpenAlex works filter")
    fp.add_argument("--search", help="openalex: a phrase matched in titles and abstracts")
    fp.add_argument("--issn", help="crossref: the journal's ISSN")
    fp.add_argument("--url", help="rss: the feed URL")
    fp.add_argument("--title", help="a label, e.g. the journal's name")
    fp = grp.command("remove", cmd_feeds_remove, "remove a feed from ruru.toml",
                       ["ruru feeds remove anal-chem"], [b.write, b.quiet])
    fp.add_argument("name")
    grp.command("wizard", cmd_feeds_wizard,
                  "interactive setup: find arXiv categories, journals and authors (needs a terminal)",
                  ["ruru feeds wizard"])

    p = b.command("find", cmd_find, "search OpenAlex for papers not yet in the library",
                ["ruru find retention time alignment gc-ms", "ruru find 'fish oil authentication' -n 20 --json",
                 "ruru find 'joint alignment GC-MS batches' -q | head -1 | xargs ruru add"],
                [b.read])
    p.add_argument("query", nargs="+")
    p.add_argument("-n", "--number", type=int, default=10, metavar="N", help="results (default 10, max 100)")
