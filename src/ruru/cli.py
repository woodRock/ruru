"""The `ruru` command line: the primary interface, for Jesse and agents alike.

The agent contract (README, docs/json.md): no prompts, no stdin unless asked for (`-F -`), colour
only on a TTY without NO_COLOR, `--json` on every read command, `-q` for keys only, warnings and
progress on stderr, and the exit codes in `ruru.errors`.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from ruru import __version__, feeds, http, model, ops
from ruru import projects as projmod
from ruru.bibtex import ENCODINGS, parse_bibtex, to_bibtex
from ruru.config import config_path, resolve_library, secret, write_user_config
from ruru.errors import LOCKED, NO_MATCH, OK, PARTIAL, USAGE, NothingMatched, RuruError, UsageError
from ruru.fsutil import atomic_write
from ruru.inbox import DECISIONS, Inbox, Item, UnknownItem, read_seen
from ruru.library import Library
from ruru.model import Paper
from ruru.output import emit_json, item_json, item_line, paper_json, paper_line, style, warn
from ruru.resolve import Resolver, apply_metadata, changes, openalex, parse_identifier
from ruru.search import search

# The exit codes are re-exported: tests and scripts refer to cli.OK etc.
__all__ = ["LOCKED", "NO_MATCH", "OK", "PARTIAL", "USAGE", "build_parser", "main"]

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


# --- helpers -------------------------------------------------------------------------------------

def _open(args: argparse.Namespace) -> Library:
    auto_commit = False if getattr(args, "no_commit", False) else None
    return Library.open(resolve_library(args.library), auto_commit=auto_commit)


def _rel(lib: Library, key: str) -> str:
    return str((lib.paper_dir(key) / "paper.md").relative_to(lib.root))


def client_for(lib: Library) -> http.Client:
    return http.Client(email=lib.settings.contact_email, openalex_key=secret("openalex_api_key"),
                       cache_dir=lib.root / ".ruru" / "http")


def _exit_code(failures: list[RuruError], attempted: int) -> int:
    """All failed for one reason → that reason's code; anything else mixed → partial failure."""
    if not failures:
        return OK
    codes = {f.code for f in failures}
    return codes.pop() if len(failures) == attempted and len(codes) == 1 else PARTIAL


def _json(lib: Library, p: Paper, full: bool = False) -> dict[str, object]:
    return paper_json(p, path=_rel(lib, p.key), has_pdf=lib.has_pdf(p.key), full=full)


def _tag(t: str) -> str:
    return "-".join(t.strip().lower().split())


def _date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO date (YYYY-MM-DD): {s!r}") from None


def _print_papers(lib: Library, papers: list[Paper], args: argparse.Namespace,
                  scores: list[int] | None = None) -> None:
    if args.json:
        rows = [_json(lib, p) for p in papers]
        if scores is not None:
            for row, score in zip(rows, scores):
                row["score"] = score
        emit_json(rows)
    elif args.quiet:
        for p in papers:
            print(p.key)
    else:
        for p in papers:
            print(paper_line(p))


# --- commands ------------------------------------------------------------------------------------

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
    lib = _open(args)
    tags = [_tag(t) for t in args.tag or []]
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
        with client_for(lib) as client:  # network first, outside the lock
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
    return _exit_code(failures, len(args.ident) if args.ident else len(entries))


def cmd_list(args: argparse.Namespace) -> int:
    lib = _open(args)
    papers = [
        p for p in lib.papers()
        if all(_tag(t) in p.tags for t in args.tag or [])
        and (not args.project or args.project in p.projects)
        and (not args.status or p.status == args.status)
        and (not args.since or (p.added is not None and p.added >= args.since))
    ]
    papers.sort(key=lambda p: (p.added or date.min, p.key), reverse=True)
    _print_papers(lib, papers, args)
    if not papers:
        if not args.json:
            print("no papers matched", file=sys.stderr)
        return NO_MATCH
    return OK


def _show_item(lib: Library, item: Item, args: argparse.Namespace) -> int:
    if args.bib:
        raise UsageError(f"{item.id} is still in the inbox: `ruru keep {item.id}` first")
    if args.json:
        emit_json(item_json(item, lib.root))
    elif args.quiet:
        print(item.id)
    else:
        p = item.paper
        ids = ", ".join(f"{k}: {v}" for k, v in p.ids.items() if v)
        lines = [style(p.title, "bold"), ", ".join(p.authors), " · ".join(str(x) for x in (p.year, p.venue) if x),
                 f"inbox: {item.id}   from {p.source}   fetched {item.fetched}", ids, p.url or ""]
        if item.suggestion:
            s = item.suggestion
            lines.append(style(f"suggested: {s.get('action')} ({s.get('by')}): {s.get('reason')}", "yellow"))
        if p.abstract:
            lines += ["", p.abstract]
        print("\n".join(line for line in lines if line))
    return OK


def cmd_show(args: argparse.Namespace) -> int:
    lib = _open(args)
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
        emit_json(_json(lib, p, full=True))
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
        lines.append(f"{_rel(lib, p.key)}" + ("   (pdf)" if lib.has_pdf(p.key) else ""))
        if p.abstract:
            lines += ["", p.abstract]
        if p.notes:
            lines += ["", style("Notes", "bold"), p.notes]
        if p.agent_notes:
            lines += ["", style("Agent notes", "bold"), p.agent_notes]
        print("\n".join(line for line in lines if line is not None))
    return OK


def cmd_search(args: argparse.Namespace) -> int:
    lib = _open(args)
    hits = search(lib.papers(), " ".join(args.query))
    _print_papers(lib, [p for _, p in hits], args, scores=[s for s, _ in hits])
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
    add = [_tag(i[1:]) for i in items if len(i) > 1 and i[0] == "+"]
    remove = [_tag(i[1:]) for i in items if len(i) > 1 and i[0] == "-"]
    args.no_commit = args.no_commit or no_commit
    for p in ops.tag(_open(args), keys, add, remove):
        print(p.key if quiet else f"{p.key}: {' '.join(p.tags) or '(no tags)'}")
    return OK


def cmd_status(args: argparse.Namespace) -> int:
    for p in ops.set_status(_open(args), args.keys, args.status):
        print(p.key if args.quiet else f"{p.key}: {p.status}")
    return OK


def edit_text(text: str) -> str:
    """Open `text` in $VISUAL/$EDITOR (vi) and return what was saved. Used by `note` and the TUI."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text + "\n" if text else "")
        path = f.name
    try:
        if subprocess.run([*shlex.split(editor), path]).returncode != 0:
            raise UsageError(f"editor {editor!r} exited with an error; notes unchanged")
        return Path(path).read_text(encoding="utf-8")
    finally:
        os.unlink(path)


def cmd_note(args: argparse.Namespace) -> int:
    lib = _open(args)
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
    if not ops.set_notes(lib, p.key, edit_text(p.notes)):
        print("notes unchanged", file=sys.stderr)
    return OK


def _brief(value: object) -> str:
    text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return text if len(text) <= 60 else text[:57] + "..."


def cmd_refresh(args: argparse.Namespace) -> int:
    lib = _open(args)
    papers = [lib.get(k) for k in args.keys] if args.keys else list(lib.papers())
    results: list[dict[str, object]] = []
    fresh: dict[str, Paper] = {}
    failures: list[RuruError] = []
    with client_for(lib) as client:  # network first, outside the lock
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
    return _exit_code(failures, len(papers))


opener = "open" if sys.platform == "darwin" else "xdg-open"


def cmd_pdf(args: argparse.Namespace) -> int:
    lib = _open(args)
    p = lib.get(args.key)
    path = lib.paper_dir(p.key) / "paper.pdf"
    if args.fetch and (args.force or not path.exists()):
        with client_for(lib) as client:
            ops.fetch_pdf(lib, client, p.key, force=args.force)
    if not path.exists():
        raise NothingMatched(f"{p.key}: no PDF; try `ruru pdf {p.key} --fetch`")
    if args.open:
        subprocess.run([opener, str(path)], check=False)
    print(path)
    return OK


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
    lib = _open(args)
    selected = _feed_list(lib, args.feed or [])
    if not selected:
        raise UsageError("no feeds yet: run `ruru feeds wizard` or `ruru feeds add …`")
    with client_for(lib) as client:
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
    lib = _open(args)
    items = Inbox(lib.root).items()
    if args.feed:
        items = [i for i in items if i.paper.source == f"feed:{args.feed}"]
    if args.suggested:
        want = None if args.suggested == "none" else args.suggested
        items = [i for i in items if (i.suggestion or {}).get("action") == want]
    if args.number:
        items = items[:args.number]
    if args.json:
        emit_json([item_json(i, lib.root) for i in items])
    elif args.quiet:
        for i in items:
            print(i.id)
    else:
        for i in items:
            print(item_line(i))
    if not items:
        if not args.json:
            print("inbox is empty", file=sys.stderr)
        return NO_MATCH
    return OK


def _triage(args: argparse.Namespace, decision: str) -> int:
    lib = _open(args)
    tags = [_tag(t) for t in getattr(args, "tag", None) or []]
    projects = getattr(args, "project", None) or []
    for item, key in ops.triage(lib, args.ids, decision, tags=tags, projects=projects):
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


def cmd_suggest(args: argparse.Namespace) -> int:
    lib = _open(args)
    item = ops.suggest(lib, args.id, args.action, args.reason, args.by)
    print(item.id if args.quiet else f"{item.id}: suggested {args.action}")
    return OK


def cmd_feeds_list(args: argparse.Namespace) -> int:
    lib = _open(args)
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
    lib = _open(args)
    feed = feeds.Feed(name=args.name, kind=args.kind, title=args.title, query=args.query, filter=args.filter,
                      search=args.search, issn=args.issn, url=args.url)
    ops.add_feed(lib, feed)
    print(feed.name if args.quiet else f"added feed {feed.name}: {feed.describe()}")
    return OK


def cmd_feeds_remove(args: argparse.Namespace) -> int:
    lib = _open(args)
    ops.remove_feed(lib, args.name)
    print(args.name if args.quiet else f"removed feed {args.name}")
    return OK


def cmd_feeds_wizard(args: argparse.Namespace) -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise UsageError("`ruru feeds wizard` is interactive and needs a terminal; use `ruru feeds add …`")
    from ruru.wizard import Wizard

    lib = _open(args)
    with client_for(lib) as client:
        Wizard(client, feeds.load(lib.settings.feeds), lambda f: ops.add_feed(lib, f)).run()
    return OK


def cmd_find(args: argparse.Namespace) -> int:
    lib = _open(args)
    with client_for(lib) as client:
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


def cmd_project_list(args: argparse.Namespace) -> int:
    lib = _open(args)
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
    lib = _open(args)
    ops.add_project(lib, args.name, path=args.path, bib=args.bib, tex=args.tex or [], encoding=args.bib_encoding)
    print(args.name if args.quiet else f"added project {args.name}")
    return OK


def cmd_project_remove(args: argparse.Namespace) -> int:
    lib = _open(args)
    ops.remove_project(lib, args.name)
    print(args.name if args.quiet else f"removed project {args.name} (its papers keep the tag; files untouched)")
    return OK


def cmd_cite(args: argparse.Namespace) -> int:
    lib = _open(args)
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


def _keys_line(label: str, keys: list[str], hint: str = "") -> str:
    line = f"{style(label, 'bold')} ({len(keys)}): {', '.join(keys)}"
    return line + (f"\n    {style(hint, 'dim')}" if hint else "")


def cmd_check(args: argparse.Namespace) -> int:
    lib = _open(args)
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
            print(_keys_line("missing from the bib, in the library", fixable, "ruru check --fix adds them"))
        if unknown:
            print(_keys_line("missing from the bib and the library", unknown,
                             "ruru add DOI|arXiv id, then ruru check --fix"))
        if report.unused:
            print(_keys_line("in the managed block, never cited", report.unused, "ruru check --prune drops them"))
        if report.kept_unused:
            print(_keys_line("kept for this project, not cited", report.kept_unused))
        if report.stale:
            print(_keys_line("out of date with the library", report.stale, "ruru check --fix regenerates them"))
        for a, b in report.duplicates:
            print(f"{style('duplicate', 'bold')}: {a} and {b} are the same paper")
        print(style("no issues", "green") if not report.issues else f"{report.issues} issue(s)")
    return NO_MATCH if report.issues else OK


def cmd_export(args: argparse.Namespace) -> int:
    from ruru.export import export_bib, export_document

    lib = _open(args)
    if args.format == "json":
        text = json.dumps(export_document(lib, notes=not args.no_notes), ensure_ascii=False, indent=2) + "\n"
    elif args.format == "bib":
        papers = [p for p in lib.papers()
                  if all(_tag(t) in p.tags for t in args.tag or [])
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


def cmd_rekey(args: argparse.Namespace) -> int:
    lib = _open(args)
    new, cited_in = ops.rekey(lib, args.old, args.new)
    print(new if args.quiet else f"{args.old} -> {new}")
    for project in cited_in:
        warn(f"{project}: replace \\cite{{{args.old}}} with \\cite{{{new}}} in its .tex, "
             f"then `ruru check {project} --fix --prune`")
    return OK


def cmd_tui(args: argparse.Namespace) -> int:
    try:
        tui = importlib.import_module("ruru.tui")  # lazy: the core never imports Textual
    except ImportError:
        print("ruru tui needs the optional extra: pip install 'ruru[tui]'", file=sys.stderr)
        return USAGE
    return int(tui.run(args))


# --- parser --------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
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

    parser = _Parser(
        prog="ruru",
        description="A local, plain-text literature library with a feed inbox.",
        formatter_class=_Formatter,
        epilog=_examples(
            "ruru init ~/papers --email you@example.org",
            "ruru add --bib refs.bib -t gc-ms",
            "ruru search 'retention time' --json",
            "ruru COMMAND -h               exact flags for one command",
        ),
    )
    parser.add_argument("-V", "--version", action="version", version=f"ruru {__version__}")
    parser.add_argument("-L", "--library", metavar="PATH", help="library to use (default: from config)")
    parser.add_argument("--no-commit", action="store_true", help="don't git-commit changes")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)

    def command(name: str, func: Handler, help: str, examples: Sequence[str],
                parents: Sequence[argparse.ArgumentParser] = ()) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help, description=help, formatter_class=_Formatter,
                           epilog=_examples(*examples), parents=[common, *parents])  # type: ignore[list-item]
        p.set_defaults(func=func)
        return p

    p = command("init", cmd_init, "create a library (git init, ruru.toml, .gitignore)",
                ["ruru init ~/papers --email you@example.org", "ruru init --bib-encoding utf8"])
    p.add_argument("path", nargs="?", help="where to create it (default: the configured library)")
    p.add_argument("--email", help="contact email for the OpenAlex and Crossref polite pools")
    p.add_argument("--bib-encoding", choices=ENCODINGS, default="ascii",
                   help="ascii (classic BibTeX, default) or utf8 (biber/biblatex)")

    p = command("add", cmd_add, "add papers by DOI, arXiv id, URL or title; from BibTeX; or by hand",
                ["ruru add 10.1186/s13321-021-00544-w -t gc-ms",
                 "ruru add 2103.13807 https://doi.org/10.1038/nature14539",
                 "ruru add \"Deep learning\" --year 2015          (exact title; never guessed)",
                 "ruru add --bib refs.bib -t gc-ms",
                 "ruru add --title 'Joint alignment' --author 'Jesse Wood' --year 2026 --type article",
                 "pbpaste | ruru add --bib - -q"],
                [write, quiet])
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
    p.add_argument("-t", "--tag", action="append", metavar="TAG", help="repeatable")
    p.add_argument("-p", "--project", action="append", metavar="PROJECT", help="repeatable")

    p = command("list", cmd_list, "list papers, newest first",
                ["ruru list -s to-read", "ruru list -t gc-ms -t alignment --json",
                 "ruru list --since 2026-10-01 -q"],
                [read])
    p.add_argument("-t", "--tag", action="append", metavar="TAG", help="must have this tag (repeatable)")
    p.add_argument("-p", "--project", metavar="PROJECT")
    p.add_argument("-s", "--status", choices=model.STATUSES)
    p.add_argument("--since", type=_date, metavar="DATE", help="added on or after DATE (YYYY-MM-DD)")

    p = command("show", cmd_show, "show one paper: metadata, abstract and notes",
                ["ruru show wood2026joint", "ruru show wood2026joint --json", "ruru show wood2026joint --bib"],
                [read])
    p.add_argument("key")
    p.add_argument("--bib", action="store_true", help="print the BibTeX entry")
    p.add_argument("--bib-encoding", choices=ENCODINGS, help="override ruru.toml for --bib")

    p = command("search", cmd_search, "full-text search over metadata and notes (every word must match)",
                ["ruru search retention time alignment", "ruru search 'Nguyen' --json", "ruru search gc-ms -q"],
                [read])
    p.add_argument("query", nargs="+")

    p = command("tag", cmd_tag, "add (+TAG) and remove (-TAG) tags",
                ["ruru tag wood2026joint +gc-ms -todo", "ruru tag key1 key2 +survey -q"],
                [write])
    p.add_argument("items", nargs=argparse.REMAINDER, metavar="KEY… +TAG… -TAG…")

    p = command("status", cmd_status, "set reading status",
                ["ruru status wood2026joint reading", "ruru status key1 key2 read"],
                [write, quiet])
    p.add_argument("keys", nargs="+", metavar="KEY")
    p.add_argument("status", choices=model.STATUSES)

    p = command("note", cmd_note, "edit your Notes in $EDITOR, or append a dated agent note",
                ["ruru note wood2026joint                 (opens $EDITOR)",
                 "ruru note wood2026joint --agent -m 'Summary (abstract only): …'",
                 "ruru note wood2026joint --agent --kind summary -F summary.md",
                 "echo '…' | ruru note wood2026joint --agent -F -"],
                [write, quiet])
    p.add_argument("key")
    p.add_argument("--agent", action="store_true", help="append under '## Agent notes' (never touches Notes)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("-m", "--message", metavar="TEXT")
    g.add_argument("-F", "--file", metavar="FILE", help="read the note from FILE (- for stdin)")
    p.add_argument("--kind", default="note", help="entry label, e.g. summary, critique (default: note)")
    p.add_argument("--by", default=os.environ.get("RURU_AGENT", "agent"),
                   help="attribution (default: $RURU_AGENT or 'agent')")

    p = command("fetch", cmd_fetch, "pull feeds into the inbox (new items only)",
                ["ruru fetch", "ruru fetch -f anal-chem -f arxiv-chem", "ruru fetch && ruru inbox --json",
                 "ruru fetch --since 2026-10-01 -q"],
                [read])
    p.add_argument("-f", "--feed", action="append", metavar="FEED", help="only this feed (repeatable)")
    p.add_argument("--since", type=_date, metavar="DATE",
                   help="look back to DATE instead of each feed's last fetch")

    p = command("inbox", cmd_inbox, "list unseen items, newest first",
                ["ruru inbox", "ruru inbox --json -n 20", "ruru inbox -f anal-chem -q",
                 "ruru inbox --suggested keep", "ruru show arxiv-2610.01234     (one item in full)"],
                [read])
    p.add_argument("-n", "--number", type=int, metavar="N", help="at most N items")
    p.add_argument("-f", "--feed", metavar="FEED", help="only items from this feed")
    p.add_argument("--suggested", choices=[*DECISIONS, "none"], help="only items with this suggestion")

    for name, func, what in (("keep", cmd_keep, "inbox -> library (status to-read)"),
                             ("later", cmd_later, "inbox -> library, marked read later")):
        p = command(name, func, what,
                    [f"ruru {name} arxiv-2610.01234",
                     f"ruru {name} arxiv-2610.01234 doi-10.1021-x -t gc-ms -p joint-align"],
                    [write, quiet])
        p.add_argument("ids", nargs="+", metavar="ID")
        p.add_argument("-t", "--tag", action="append", metavar="TAG", help="repeatable")
        p.add_argument("-p", "--project", action="append", metavar="PROJECT", help="repeatable")

    p = command("skip", cmd_skip, "drop inbox items (remembered in seen.jsonl, never fetched again)",
                ["ruru skip arxiv-2610.01234", "ruru inbox -q --suggested skip | xargs ruru skip"],
                [write, quiet])
    p.add_argument("ids", nargs="+", metavar="ID")

    p = command("suggest", cmd_suggest, "record a recommendation on an inbox item (for agents)",
                ["ruru suggest arxiv-2610.01234 keep --reason 'GC-MS alignment; relevant to joint-align'",
                 "ruru suggest doi-10.1016-x skip --reason 'LC-MS proteomics, off topic' --by claude"],
                [write, quiet])
    p.add_argument("id", metavar="ID")
    p.add_argument("action", choices=DECISIONS)
    p.add_argument("--reason", required=True, help="one line, tied to your projects")
    p.add_argument("--by", default=os.environ.get("RURU_AGENT", "agent"),
                   help="attribution (default: $RURU_AGENT or 'agent')")

    p = command("feeds", cmd_feeds_list, "list, add or remove feeds; or set them up interactively",
                ["ruru feeds", "ruru feeds wizard", "ruru feeds add anal-chem --kind crossref --issn 0003-2700",
                 "ruru feeds remove anal-chem"],
                [read])
    fsub = p.add_subparsers(dest="feeds_command", metavar="ACTION", parser_class=_Parser)

    def feeds_command(name: str, func: Handler, help: str, examples: Sequence[str],
                      parents: Sequence[argparse.ArgumentParser] = ()) -> argparse.ArgumentParser:
        fp = fsub.add_parser(name, help=help, description=help, formatter_class=_Formatter,
                             epilog=_examples(*examples), parents=[common, *parents])  # type: ignore[list-item]
        fp.set_defaults(func=func)
        return fp

    fp = feeds_command("list", cmd_feeds_list, "list feeds and when each was last fetched",
                       ["ruru feeds list", "ruru feeds list --json"], [read])
    fp = feeds_command("add", cmd_feeds_add, "add a feed to ruru.toml",
                       ["ruru feeds add arxiv-chem --kind arxiv --query 'cat:physics.chem-ph AND abs:GC-MS'",
                        "ruru feeds add anal-chem --kind crossref --issn 0003-2700 --title 'Analytical Chemistry'",
                        "ruru feeds add supervisors --kind openalex --filter 'authorships.author.id:A5077569089'",
                        "ruru feeds add rt-alignment --kind openalex --search 'retention time alignment'",
                        "ruru feeds add jat --kind rss --url https://example.org/feed.xml"],
                       [write, quiet])
    fp.add_argument("name", help="lowercase letters, digits and hyphens")
    fp.add_argument("--kind", required=True, choices=feeds.KINDS)
    fp.add_argument("--query", help="arxiv: an arXiv API search_query")
    fp.add_argument("--filter", help="openalex: an OpenAlex works filter")
    fp.add_argument("--search", help="openalex: a phrase matched in titles and abstracts")
    fp.add_argument("--issn", help="crossref: the journal's ISSN")
    fp.add_argument("--url", help="rss: the feed URL")
    fp.add_argument("--title", help="a label, e.g. the journal's name")
    fp = feeds_command("remove", cmd_feeds_remove, "remove a feed from ruru.toml",
                       ["ruru feeds remove anal-chem"], [write, quiet])
    fp.add_argument("name")
    feeds_command("wizard", cmd_feeds_wizard,
                  "interactive setup: find arXiv categories, journals and authors (needs a terminal)",
                  ["ruru feeds wizard"])

    p = command("find", cmd_find, "search OpenAlex for papers not yet in the library",
                ["ruru find retention time alignment gc-ms", "ruru find 'fish oil authentication' -n 20 --json",
                 "ruru find 'joint alignment GC-MS batches' -q | head -1 | xargs ruru add"],
                [read])
    p.add_argument("query", nargs="+")
    p.add_argument("-n", "--number", type=int, default=10, metavar="N", help="results (default 10, max 100)")

    p = command("project", cmd_project_list, "paper repos that `cite` and `check` work on",
                ["ruru project",
                 "ruru project add joint-align --path ~/Desktop/joint-align --bib refs.bib --tex main.tex",
                 "ruru project remove joint-align"],
                [read])
    psub = p.add_subparsers(dest="project_command", metavar="ACTION", parser_class=_Parser)

    def project_command(name: str, func: Handler, help: str, examples: Sequence[str],
                        parents: Sequence[argparse.ArgumentParser] = ()) -> argparse.ArgumentParser:
        pp = psub.add_parser(name, help=help, description=help, formatter_class=_Formatter,
                             epilog=_examples(*examples), parents=[common, *parents])  # type: ignore[list-item]
        pp.set_defaults(func=func)
        return pp

    project_command("list", cmd_project_list, "list projects", ["ruru project list --json"], [read])
    pp = project_command("add", cmd_project_add, "add a paper repo (e.g. a local clone of an Overleaf project)",
                         ["ruru project add joint-align --path ~/Desktop/joint-align --bib references.bib "
                          "--tex main.tex",
                          "ruru project add thesis --path ~/thesis --bib refs.bib --tex thesis.tex "
                          "--bib-encoding utf8"],
                         [write, quiet])
    pp.add_argument("name")
    pp.add_argument("--path", required=True, help="the repo's directory")
    pp.add_argument("--bib", required=True,
                    help="its .bib file, relative to --path (created by `cite` if missing)")
    pp.add_argument("--tex", action="append", metavar="FILE",
                    help="a main .tex file, relative to --path (repeatable)")
    pp.add_argument("--bib-encoding", choices=ENCODINGS, help="override ruru.toml's bib_encoding for this project")
    pp = project_command("remove", cmd_project_remove, "forget a project (its files are not touched)",
                         ["ruru project remove joint-align"], [write, quiet])
    pp.add_argument("name")

    p = command("cite", cmd_cite, "add papers to a project's .bib (inside ruru's managed block)",
                ["ruru cite joint-align wood2026joint guillevic2021automated",
                 "ruru search 'retention time' -q | xargs ruru cite joint-align"],
                [write, quiet])
    p.add_argument("project")
    p.add_argument("keys", nargs="+", metavar="KEY")

    p = command("check", cmd_check,
                "citation health: missing, unused, stale and duplicate entries (exit 2 on issues)",
                ["ruru check joint-align", "ruru check joint-align --fix", "ruru check paper/main.tex --json",
                 "ruru check joint-align --json || echo 'citations need attention'"],
                [read])
    p.add_argument("target", metavar="PROJECT|FILE.tex")
    p.add_argument("--fix", action="store_true",
                   help="cite missing keys the library has; regenerate stale entries")
    p.add_argument("--prune", action="store_true", help="drop managed entries that nothing cites")

    p = command("export", cmd_export, "export the library: bib (a selection) or json (everything)",
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

    p = command("rekey", cmd_rekey, "rename a paper's key (keys never change otherwise)",
                ["ruru rekey smith2020deep smith2020deepnets",
                 "ruru rekey vaswani2025attention     (new key from its current metadata)"],
                [write, quiet])
    p.add_argument("old", metavar="KEY")
    p.add_argument("new", nargs="?", metavar="NEW", help="default: the key its current metadata gives")

    p = command("doctor", cmd_doctor, "check config, settings, git, files and API access (exit 3 on failures)",
                ["ruru doctor", "ruru doctor --offline --json"],
                [read])
    p.add_argument("--offline", action="store_true", help="skip the API checks")

    p = command("refresh", cmd_refresh, "re-resolve metadata (e.g. a preprint since published); keys never change",
                ["ruru refresh", "ruru refresh guillevic2021automated --dry-run", "ruru refresh --json"],
                [write, read])
    p.add_argument("keys", nargs="*", metavar="KEY", help="default: every paper")
    p.add_argument("-n", "--dry-run", action="store_true", help="show changes without writing them")

    p = command("pdf", cmd_pdf, "print, fetch (open access only) or open a paper's PDF",
                ["ruru pdf guillevic2021automated", "ruru pdf guillevic2021automated --fetch --open"],
                [write])
    p.add_argument("key")
    p.add_argument("--fetch", action="store_true", help="download the open-access PDF to paper.pdf")
    p.add_argument("--force", action="store_true", help="with --fetch: download even if paper.pdf exists")
    p.add_argument("--open", action="store_true", help="open it in the system viewer")

    command("tui", cmd_tui, "interactive UI (optional extra: ruru[tui])", ["ruru tui", "ruru -L ~/papers tui"])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Handler | None = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return USAGE
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
