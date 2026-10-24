"""Commands on the library's papers: init, add, list, show, search, tag, status, rm, note, rekey,
refresh, enrich, export."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ruru import desktop, model, ops
from ruru import index as indexmod
from ruru.bibtex import ENCODINGS, parse_bibtex, to_bibtex
from ruru.cmdkit import Builder, exit_code, iso_date, norm_tag, open_library, paper_row, print_papers, rel_path
from ruru.config import config_path, resolve_library, write_user_config
from ruru.errors import NO_MATCH, OK, NothingMatched, RuruError, UsageError
from ruru.fsutil import atomic_write
from ruru.inbox import Inbox, Item, UnknownItem, read_seen
from ruru.library import Library
from ruru.model import Paper
from ruru.output import emit_json, item_json, rank_fields, style, warn
from ruru.resolve import Resolver, apply_metadata, changes, parse_identifier
from ruru.search import SORTS, filed_times, in_project, needs_filed, search, sort_papers


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser() if args.path else resolve_library(args.library)
    lib = Library.init(root, contact_email=args.email or "", bib_encoding=args.bib_encoding)
    print(f"initialised ruru library at {lib.root}", file=sys.stderr)
    if not config_path().exists():
        path = write_user_config(lib.root.resolve())
        print(f"set it as the default library in {path}", file=sys.stderr)
    if not args.email:
        warn("set contact_email in ruru.toml before fetching (OpenAlex and Crossref polite pools)")
    return OK


def _manual_paper(args: argparse.Namespace) -> Paper:
    if not args.title:
        raise UsageError("give a DOI, arXiv id, URL or \"TITLE\"; or --bib FILE; or --title … by hand")
    p = Paper(
        title=args.title, authors=args.author or [], year=args.year, venue=args.venue, type=args.type,
        url=args.url, abstract=args.abstract, source="add",
    )
    p.ids["doi"] = model.norm_id("doi", args.doi)
    p.ids["arxiv"] = model.norm_id("arxiv", args.arxiv)
    return p


def cmd_add(args: argparse.Namespace) -> int:
    lib = open_library(args)
    tags = [norm_tag(t) for t in args.tag or []]
    projects = args.project or []
    lib.check_projects(projects)
    failures: list[RuruError] = []
    if args.ident and (args.bib or args.title):
        raise UsageError("give identifiers, --bib or --title, not a mix")
    if args.bib:
        text = sys.stdin.read() if args.bib == "-" else Path(args.bib).read_text(encoding="utf-8")
        entries = parse_bibtex(text)
        if not entries:
            raise UsageError(f"no BibTeX entries in {args.bib}")
    elif args.ident:
        entries = []
        with ops.client_for(lib) as client:  # network first, outside the lock
            resolver = Resolver(client)
            for text in args.ident:
                try:
                    ident = parse_identifier(text)
                    existing = None
                    if ident.kind != "title":  # a known id needs no network
                        probe = Paper()
                        probe.ids[ident.kind] = ident.value
                        existing = lib.find_duplicate(probe)
                    if existing:
                        warn(f"already in the library as {existing.key}")
                        if args.quiet:
                            print(existing.key)
                        continue
                    entries.append(("", resolver.resolve(text, year=args.year)))
                except RuruError as e:
                    warn(f"{text}: {e}")
                    failures.append(e)
    else:
        entries = [("", _manual_paper(args))]
    added: list[str] = []
    labelled: list[str] = []
    with lib.transaction() as tx:
        for orig_key, paper in entries:
            paper.tags = sorted(set(paper.tags) | set(tags))
            paper.projects = sorted(set(paper.projects) | set(projects))
            try:
                paper, new = lib.add(tx, paper, key=orig_key if args.keep_key else None)
            except RuruError as e:  # e.g. --keep-key and the key belongs to another paper: skip just this one
                warn(f"{orig_key or paper.title}: {e}; not added")
                failures.append(e)
                continue
            if new:
                added.append(paper.key)
                print(paper.key if args.quiet else f"added {paper.key}: {paper.title} ({paper.year or 'n.d.'})")
                continue
            alias = f" (this file calls it {orig_key})" if orig_key and orig_key != paper.key else ""
            warn(f"already in the library as {paper.key}{alias}")
            if not (set(tags) <= set(paper.tags) and set(projects) <= set(paper.projects)):
                paper.tags = sorted(set(paper.tags) | set(tags))  # the labels asked for still apply
                paper.projects = sorted(set(paper.projects) | set(projects))
                tx.save(paper)
                labelled.append(paper.key)
            if args.quiet:
                print(paper.key)
        details = f" (tags: {', '.join(tags)})" if tags else ""
        details += f" (projects: {', '.join(projects)})" if projects else ""
        what = " ".join(added) if len(added) <= 5 else f"{len(added)} papers"
        if labelled and not added:
            what = f"labels on {len(labelled)} existing paper(s)"
        elif labelled:
            what += f" + labels on {len(labelled)} existing"
        tx.message = f"ruru: add {what}{details}"
    if args.bib and added and not args.no_enrich:
        _enrich_in_background(lib, added, no_commit=getattr(args, "no_commit", False))
    return exit_code(failures, len(args.ident) if args.ident else len(entries))


def _enrich_in_background(lib: Library, keys: list[str], *, no_commit: bool) -> None:
    """A BibTeX import stays offline and fast; abstracts and ids are looked up afterwards by a
    one-off `ruru enrich` that exits when done (not a daemon) and commits on its own."""
    argv = [sys.executable, "-m", "ruru", "enrich", "-L", str(lib.root), *keys]
    log = lib.root / ".ruru" / "enrich.log"
    desktop.spawn(argv + (["--no-commit"] if no_commit else []), log)
    print(f"ruru: looking up abstracts for {len(keys)} paper(s) in the background "
          f"(log: {log}; `ruru add --no-enrich` to skip)", file=sys.stderr)


def cmd_list(args: argparse.Namespace) -> int:
    lib = open_library(args)
    papers = [
        p for p in lib.papers()
        if all(norm_tag(t) in p.tags for t in args.tag or [])
        and (not args.project or in_project(p, args.project))
        and (not args.status or p.status == args.status)
        and (not args.since or (p.added is not None and p.added >= args.since))
    ]
    filed = filed_times(lib.root) if needs_filed(args.sort) else lambda p: 0.0
    papers = sort_papers(papers, args.sort, reverse=args.reverse, filed=filed)
    print_papers(lib, papers, args)
    if not papers:
        if not args.json:
            print("no papers matched", file=sys.stderr)
        return NO_MATCH
    return OK


def _show_item(lib: Library, item: Item, args: argparse.Namespace) -> int:
    if args.bib:
        raise UsageError(f"{item.id} is still in the inbox: `ruru keep {item.id}` first")
    _, scores, dup = ops.ranked_inbox(lib, "date", wanted=not args.quiet)
    rel = (scores or {}).get(item.id)
    if args.json:
        emit_json(item_json(item, lib.root, extra=rank_fields(rel, dup.get(item.id))))
    elif args.quiet:
        print(item.id)
    else:
        p = item.paper
        ids = ", ".join(f"{k}: {v}" for k, v in p.ids.items() if v)
        lines = [style(p.title, "bold"), ", ".join(p.authors), " · ".join(str(x) for x in (p.year, p.venue) if x),
                 f"inbox: {item.id}   from {p.source}   fetched {item.fetched}", ids, p.url or ""]
        if rel:
            like = ", ".join(f"{k} ({s:.2f})" for k, s in rel.similar)
            lines.append(f"relevance: {rel.score:.2f}" + (f"   like: {like}" if like else ""))
        if dup.get(item.id):
            lines.append(style(f"possible duplicate of {dup[item.id]}", "yellow"))
        if item.suggestion:
            s = item.suggestion
            lines.append(style(f"suggested: {s.get('action')} ({s.get('by')}): {s.get('reason')}", "yellow"))
        if p.abstract:
            lines += ["", p.abstract]
        print("\n".join(line for line in lines if line))
    return OK


def cmd_show(args: argparse.Namespace) -> int:
    lib = open_library(args)
    if not lib.exists(args.key):
        try:
            return _show_item(lib, Inbox(lib.root).get(args.key), args)
        except UnknownItem:
            done = [r for r in read_seen(lib.root) if r.get("id") == args.key]
            if done:
                r = done[-1]
                raise UsageError(f"{args.key} was triaged on {r.get('date')}: {r.get('decision')}"
                                 + (f" -> {r['key']}" if r.get("key") else "")) from None
    p = lib.get(args.key)
    if args.bib:
        encoding = args.bib_encoding or lib.settings.bib_encoding
        sys.stdout.write(to_bibtex(p, encoding))
    elif args.json:
        emit_json(paper_row(lib, p, full=True))
    elif args.quiet:
        print(p.key)
    else:
        ids = ", ".join(f"{k}: {v}" for k, v in p.ids.items() if v)
        lines = [
            style(p.title, "bold"),
            ", ".join(p.authors),
            " · ".join(str(x) for x in (p.year, p.venue) if x),
            f"key: {p.key}   status: {p.status}   added: {p.added or '-'}"
            + (f"   read: {p.read_on}" if p.read_on else ""),
        ]
        if p.tags or p.projects:
            lines.append(f"tags: {' '.join(p.tags) or '-'}   projects: {' '.join(p.projects) or '-'}")
        if ids:
            lines.append(ids)
        if p.url:
            lines.append(p.url)
        lines.append(f"{rel_path(lib, p.key)}" + ("   (pdf)" if lib.has_pdf(p.key) else ""))
        if p.abstract:
            lines += ["", p.abstract]
        if p.notes:
            lines += ["", style("Notes", "bold"), p.notes]
        if p.agent_notes:
            lines += ["", style("Agent notes", "bold"), p.agent_notes]
        print("\n".join(line for line in lines if line is not None))
    return OK


def cmd_search(args: argparse.Namespace) -> int:
    lib = open_library(args)
    query = " ".join(args.query)
    hits = search(lib.papers(), query)
    snippets: list[str | None] | None = None
    if args.fulltext:
        pdf, sync = ops.search_pdfs(lib, query, progress=lambda key: print(
            f"ruru: reading the PDF of {key}", file=sys.stderr))
        indexmod.warn_failures(sync.failed)
        merged = ops.merge_hits(hits, pdf, lib)
        hits = [(score, p) for score, p, _ in merged]
        snippets = [snippet for _, _, snippet in merged]
    print_papers(lib, [p for _, p in hits], args, scores=[s for s, _ in hits], snippets=snippets)
    if not hits:
        if not args.json:
            print("nothing matched", file=sys.stderr)
        return NO_MATCH
    return OK


def cmd_tag(args: argparse.Namespace) -> int:
    items = list(args.items)
    quiet = "-q" in items
    no_commit = "--no-commit" in items
    items = [i for i in items if i not in ("-q", "--no-commit")]
    keys = [i for i in items if not (len(i) > 1 and i[0] in "+-")]
    add = [norm_tag(i[1:]) for i in items if len(i) > 1 and i[0] == "+"]
    remove = [norm_tag(i[1:]) for i in items if len(i) > 1 and i[0] == "-"]
    args.no_commit = args.no_commit or no_commit
    for p in ops.tag(open_library(args), keys, add, remove):
        print(p.key if quiet else f"{p.key}: {' '.join(p.tags) or '(no tags)'}")
    return OK


def cmd_status(args: argparse.Namespace) -> int:
    for p in ops.set_status(open_library(args), args.keys, args.status):
        print(p.key if args.quiet else f"{p.key}: {p.status}")
    return OK


def cmd_rm(args: argparse.Namespace) -> int:
    lib = open_library(args)
    for p in ops.remove(lib, args.keys):
        print(p.key if args.quiet else f"removed {p.key}: {p.title}")
        if p.projects:
            warn(f"{p.key} was in {', '.join(p.projects)}; `ruru check PROJECT` shows where it is still cited")
    return OK


def cmd_note(args: argparse.Namespace) -> int:
    lib = open_library(args)
    p = lib.get(args.key)
    if args.agent:
        if args.message is None and args.file is None:
            raise UsageError("note --agent needs -m TEXT or -F FILE (- for stdin)")
        if args.file == "-":
            text = sys.stdin.read()
        elif args.file is not None:
            text = Path(args.file).read_text(encoding="utf-8")
        else:
            text = args.message
        if not text.strip():
            raise UsageError("empty note")
        heading = f"{model.today().isoformat()} — {args.kind} ({args.by})"
        with lib.transaction(f"ruru: note {p.key} ({args.kind}, {args.by})") as tx:
            p.body = model.append_agent_note(p.body, heading, text)
            tx.save(p)
        print(p.key if args.quiet else f"{p.key}: added agent note '{heading}'")
        return OK
    if args.message is not None or args.file is not None:
        raise UsageError("-m/-F write agent notes: add --agent (the Notes section is edited in $EDITOR)")
    if not sys.stdin.isatty():
        raise UsageError("`ruru note KEY` opens $EDITOR and needs a terminal; "
                         "agents use `ruru note KEY --agent -m …`")
    if not ops.set_notes(lib, p.key, desktop.edit_text(p.notes)):
        print("notes unchanged", file=sys.stderr)
    return OK


def _brief(value: object) -> str:
    text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return text if len(text) <= 60 else text[:57] + "..."


def cmd_refresh(args: argparse.Namespace) -> int:
    lib = open_library(args)
    papers = [lib.get(k) for k in args.keys] if args.keys else list(lib.papers())
    results: list[dict[str, object]] = []
    fresh: dict[str, Paper] = {}
    failures: list[RuruError] = []
    with ops.client_for(lib) as client:  # network first, outside the lock
        resolver = Resolver(client)
        for p in papers:
            try:
                new = resolver.refresh(p)
            except RuruError as e:
                warn(f"{p.key}: {e}")
                failures.append(e)
                results.append({"key": p.key, "changes": {}, "error": str(e)})
                continue
            diff = changes(p, new)
            results.append({"key": p.key, "error": None,
                            "changes": {f: {"old": old, "new": n} for f, (old, n) in diff.items()}})
            if diff:
                fresh[p.key] = new
    if fresh and not args.dry_run:
        what = " ".join(fresh) if len(fresh) <= 5 else f"{len(fresh)} papers"
        with lib.transaction(f"ruru: refresh {what}") as tx:
            for key, new in fresh.items():
                tx.save(apply_metadata(lib.get(key), new))  # re-read: someone may have tagged it meanwhile
    if args.json:
        emit_json(results)
    elif args.quiet:
        for key in fresh:
            print(key)
    else:
        for r in results:
            shown: Any = r["changes"]
            if r["error"]:
                continue
            if not shown:
                print(f"{r['key']}: up to date")
                continue
            print(f"{style(str(r['key']), 'bold')}:{' (dry run)' if args.dry_run else ''}")
            for f, d in shown.items():
                print(f"  {f}: {_brief(d['old'])} -> {_brief(d['new'])}")
    return exit_code(failures, len(papers))


def cmd_enrich(args: argparse.Namespace) -> int:
    lib = open_library(args)
    with ops.client_for(lib) as client:
        results, failures = ops.enrich(lib, client, args.keys, dry_run=args.dry_run)
    if not results:
        if args.json:
            emit_json([])
        else:
            print("every paper has an abstract", file=sys.stderr)
        return OK
    if args.json:
        emit_json([{"key": r.key, "filled": r.filled, "error": r.error} for r in results])
    elif args.quiet:
        for r in results:
            if r.filled:
                print(r.key)
    else:
        for r in results:
            if r.error:
                continue
            fields = ", ".join(r.filled) or "nothing found to fill"
            print(f"{style(r.key, 'bold')}: {fields}{' (dry run)' if args.dry_run and r.filled else ''}")
    return exit_code(failures, len(results))


def cmd_export(args: argparse.Namespace) -> int:
    from ruru.export import export_bib, export_document

    lib = open_library(args)
    if args.format == "json":
        text = json.dumps(export_document(lib, notes=not args.no_notes), ensure_ascii=False, indent=2) + "\n"
    elif args.format == "bib":
        papers = [p for p in lib.papers()
                  if all(norm_tag(t) in p.tags for t in args.tag or [])
                  and (not args.project or args.project in p.projects)
                  and (not args.status or p.status == args.status)]
        if not papers:
            raise NothingMatched("no papers matched")
        text = export_bib(papers, args.bib_encoding or lib.settings.bib_encoding)
    else:  # pragma: no cover - argparse restricts the choices
        raise UsageError(f"unknown format {args.format}")
    if args.output and args.output != "-":
        out = Path(args.output).expanduser()
        atomic_write(out, text)
        print(f"wrote {out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return OK


def cmd_rekey(args: argparse.Namespace) -> int:
    lib = open_library(args)
    new, cited_in = ops.rekey(lib, args.old, args.new)
    print(new if args.quiet else f"{args.old} -> {new}")
    for project in cited_in:
        warn(f"{project}: replace \\cite{{{args.old}}} with \\cite{{{new}}} in its .tex, "
             f"then `ruru check {project} --fix --prune`")
    return OK


def register(b: Builder) -> None:
    p = b.command("init", cmd_init, "create a library (git init, ruru.toml, .gitignore)",
                ["ruru init ~/papers --email you@example.org", "ruru init --bib-encoding utf8"])
    p.add_argument("path", nargs="?", help="where to create it (default: the configured library)")
    p.add_argument("--email", help="contact email for the OpenAlex and Crossref polite pools")
    p.add_argument("--bib-encoding", choices=ENCODINGS, default="ascii",
                   help="ascii (classic BibTeX, default) or utf8 (biber/biblatex)")

    p = b.command("add", cmd_add, "add papers by DOI, arXiv id, URL or title; from BibTeX; or by hand",
                ["ruru add 10.1186/s13321-021-00544-w -t gc-ms",
                 "ruru add 2103.13807 https://doi.org/10.1038/nature14539",
                 "ruru add \"Deep learning\" --year 2015          (exact title; never guessed)",
                 "ruru add --bib refs.bib -t gc-ms",
                 "ruru add --title 'Joint alignment' --author 'Jesse Wood' --year 2026 --type article",
                 "pbpaste | ruru add --bib - -q"],
                [b.write, b.quiet])
    p.add_argument("ident", nargs="*", metavar="ID",
                   help="DOI, arXiv id, OpenAlex id, URL containing one, or a quoted exact title")
    p.add_argument("--bib", metavar="FILE", help="import every entry of a BibTeX file (- for stdin)")
    p.add_argument("--keep-key", action="store_true", help="keep the BibTeX entries' keys instead of new ones")
    p.add_argument("--title")
    p.add_argument("--author", action="append", metavar="NAME", help="repeat for each author, in order")
    p.add_argument("--year", type=int, help="year (by hand), or to pick between works with the same title")
    p.add_argument("--venue")
    p.add_argument("--type", choices=model.TYPES, default="article")
    p.add_argument("--doi")
    p.add_argument("--arxiv")
    p.add_argument("--url")
    p.add_argument("--abstract")
    p.add_argument("--no-enrich", action="store_true",
                   help="with --bib: don't look up abstracts and ids in the background afterwards")
    p.add_argument("-t", "--tag", action="append", metavar="TAG", help="repeatable")
    p.add_argument("-p", "--project", action="append", metavar="PROJECT", help="repeatable")

    p = b.command("list", cmd_list, "list papers, newest first (or --sort)",
                ["ruru list -s to-read", "ruru list -t gc-ms -t alignment --json",
                 "ruru list -p none                  papers in no project",
                 "ruru list --sort year -r           oldest first",
                 "ruru list --since 2026-10-01 -q"],
                [b.read])
    p.add_argument("-t", "--tag", action="append", metavar="TAG", help="must have this tag (repeatable)")
    p.add_argument("-p", "--project", metavar="PROJECT", help="in this project ('none': in no project)")
    p.add_argument("--sort", choices=SORTS, default=SORTS[0],
                   help="added (default) and year: newest first; key, title: A-Z; status: to-read … read")
    p.add_argument("-r", "--reverse", action="store_true", help="reverse the order")
    p.add_argument("-s", "--status", choices=model.STATUSES)
    p.add_argument("--since", type=iso_date, metavar="DATE", help="added on or after DATE (YYYY-MM-DD)")

    p = b.command("show", cmd_show, "show one paper: metadata, abstract and notes",
                ["ruru show wood2026joint", "ruru show wood2026joint --json", "ruru show wood2026joint --bib"],
                [b.read])
    p.add_argument("key")
    p.add_argument("--bib", action="store_true", help="print the BibTeX entry")
    p.add_argument("--bib-encoding", choices=ENCODINGS, help="override ruru.toml for --bib")

    p = b.command("search", cmd_search, "search metadata and notes (every word must match); --fulltext adds PDFs",
                ["ruru search retention time alignment", "ruru search 'Nguyen' --json", "ruru search gc-ms -q",
                 "ruru search --fulltext 'Huber weights'     also inside the PDFs (needs ruru[pdf])"],
                [b.read])
    p.add_argument("query", nargs="+")
    p.add_argument("--fulltext", action="store_true",
                   help="also search the text of the PDFs (new ones are read first, once)")

    p = b.command("rm", cmd_rm, "remove papers from the library (one commit; `git revert` brings them back)",
                ["ruru rm wood2026joint", "ruru rm $(ruru list -t junk -q)"],
                [b.write, b.quiet])
    p.add_argument("keys", nargs="+", metavar="KEY")

    p = b.command("tag", cmd_tag, "add (+TAG) and remove (-TAG) tags",
                ["ruru tag wood2026joint +gc-ms -todo", "ruru tag key1 key2 +survey -q"],
                [b.write])
    p.add_argument("items", nargs=argparse.REMAINDER, metavar="KEY… +TAG… -TAG…")

    p = b.command("status", cmd_status, "set reading status",
                ["ruru status wood2026joint reading", "ruru status key1 key2 read"],
                [b.write, b.quiet])
    p.add_argument("keys", nargs="+", metavar="KEY")
    p.add_argument("status", choices=model.STATUSES)

    p = b.command("note", cmd_note, "edit your Notes in $EDITOR, or append a dated agent note",
                ["ruru note wood2026joint                 (opens $EDITOR)",
                 "ruru note wood2026joint --agent -m 'Summary (abstract only): …'",
                 "ruru note wood2026joint --agent --kind summary -F summary.md",
                 "echo '…' | ruru note wood2026joint --agent -F -"],
                [b.write, b.quiet])
    p.add_argument("key")
    p.add_argument("--agent", action="store_true", help="append under '## Agent notes' (never touches Notes)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("-m", "--message", metavar="TEXT")
    g.add_argument("-F", "--file", metavar="FILE", help="read the note from FILE (- for stdin)")
    p.add_argument("--kind", default="note", help="entry label, e.g. summary, critique (default: note)")
    p.add_argument("--by", default=os.environ.get("RURU_AGENT", "agent"),
                   help="attribution (default: $RURU_AGENT or 'agent')")

    p = b.command("export", cmd_export, "export the library: bib (a selection) or json (everything)",
                ["ruru export json -o ~/papers/exports/library.json",
                 "ruru export bib -p joint-align > joint-align.bib",
                 "ruru export bib -t gc-ms --bib-encoding utf8 -o gcms.bib"])
    p.add_argument("format", choices=["bib", "json"])
    p.add_argument("-o", "--output", metavar="FILE", help="write to FILE instead of stdout")
    p.add_argument("-t", "--tag", action="append", metavar="TAG", help="bib: must have this tag (repeatable)")
    p.add_argument("-p", "--project", metavar="PROJECT", help="bib: only this project's papers")
    p.add_argument("-s", "--status", choices=model.STATUSES, help="bib: only this status")
    p.add_argument("--bib-encoding", choices=ENCODINGS, help="bib: override ruru.toml")
    p.add_argument("--no-notes", action="store_true", help="json: leave out notes and agent notes")

    p = b.command("rekey", cmd_rekey, "rename a paper's key (keys never change otherwise)",
                ["ruru rekey smith2020deep smith2020deepnets",
                 "ruru rekey vaswani2025attention     (new key from its current metadata)"],
                [b.write, b.quiet])
    p.add_argument("old", metavar="KEY")
    p.add_argument("new", nargs="?", metavar="NEW", help="default: the key its current metadata gives")

    p = b.command("refresh", cmd_refresh,
                  "re-resolve metadata (e.g. a preprint since published); keys never change",
                ["ruru refresh", "ruru refresh guillevic2021automated --dry-run", "ruru refresh --json"],
                [b.write, b.read])
    p.add_argument("keys", nargs="*", metavar="KEY", help="default: every paper")
    p.add_argument("-n", "--dry-run", action="store_true", help="show changes without writing them")

    p = b.command("enrich", cmd_enrich,
                "fill empty abstracts, ids and other metadata from Crossref/OpenAlex/arXiv; changes nothing set",
                ["ruru enrich                    every paper without an abstract",
                 "ruru enrich bvls cow --dry-run", "ruru enrich --json"],
                [b.write, b.read])
    p.add_argument("keys", nargs="*", metavar="KEY", help="default: every paper without an abstract")
    p.add_argument("-n", "--dry-run", action="store_true", help="show what would be filled without writing it")
