"""The `ruru` command line: the primary interface, for Jesse and agents alike.

The agent contract (README, docs/json.md): no prompts, no stdin unless asked for (`-F -`), colour
only on a TTY without NO_COLOR, `--json` on every read command, `-q` for keys only, warnings and
progress on stderr, and the exit codes in `ruru.errors`.
"""

from __future__ import annotations

import argparse
import importlib
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from ruru import __version__, http, model
from ruru.bibtex import ENCODINGS, parse_bibtex, to_bibtex
from ruru.config import config_path, resolve_library, secret, write_user_config
from ruru.errors import LOCKED, NO_MATCH, OK, PARTIAL, USAGE, NothingMatched, RuruError, UsageError
from ruru.fsutil import atomic_write, library_lock
from ruru.library import Library
from ruru.model import Paper
from ruru.output import emit_json, paper_json, paper_line, style, warn
from ruru.resolve import Resolver, apply_metadata, changes, parse_identifier
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


def _client(lib: Library) -> http.Client:
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
        with _client(lib) as client:  # network first, outside the lock
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
    with lib.transaction() as tx:
        for orig_key, paper in entries:
            paper.tags = sorted(set(paper.tags) | set(tags))
            paper.projects = sorted(set(paper.projects) | set(projects))
            paper, new = lib.add(tx, paper, key=orig_key if args.keep_key else None)
            if new:
                added.append(paper.key)
                print(paper.key if args.quiet else f"added {paper.key}: {paper.title} ({paper.year or 'n.d.'})")
            else:
                warn(f"already in the library as {paper.key}")
                if args.quiet:
                    print(paper.key)
        details = f" (tags: {', '.join(tags)})" if tags else ""
        what = " ".join(added) if len(added) <= 5 else f"{len(added)} papers"
        tx.message = f"ruru: add {what}{details}"
    return _exit_code(failures, len(args.ident))


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


def cmd_show(args: argparse.Namespace) -> int:
    lib = _open(args)
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
    if not keys or not (add or remove):
        raise UsageError("usage: ruru tag KEY… +TAG… -TAG…")
    args.no_commit = args.no_commit or no_commit
    lib = _open(args)
    papers = [lib.get(k) for k in keys]  # unknown keys fail before anything is written
    with lib.transaction() as tx:
        for p in papers:
            p.tags = sorted((set(p.tags) | set(add)) - set(remove))
            tx.save(p)
            print(p.key if quiet else f"{p.key}: {' '.join(p.tags) or '(no tags)'}")
        ops = " ".join([f"+{t}" for t in add] + [f"-{t}" for t in remove])
        tx.message = f"ruru: tag {' '.join(keys)} {ops}"
    return OK


def cmd_status(args: argparse.Namespace) -> int:
    lib = _open(args)
    papers = [lib.get(k) for k in args.keys]
    with lib.transaction(f"ruru: status {' '.join(args.keys)} {args.status}") as tx:
        for p in papers:
            lib.set_status(p, args.status)
            tx.save(p)
            print(p.key if args.quiet else f"{p.key}: {p.status}")
    return OK


def _edit(text: str) -> str:
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
    new = _edit(p.notes)
    if new.strip("\n") == p.notes:
        print("notes unchanged", file=sys.stderr)
        return OK
    with lib.transaction(f"ruru: notes {p.key}") as tx:
        p.body = model.set_notes(p.body, new)
        tx.save(p)
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
    with _client(lib) as client:  # network first, outside the lock
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
        with _client(lib) as client:
            url = p.pdf_url
            if not url:
                url = Resolver(client).refresh(p).pdf_url
                if url:
                    with lib.transaction(f"ruru: pdf_url {p.key}") as tx:
                        current = lib.get(p.key)
                        current.pdf_url = url
                        tx.save(current)
            if not url:
                raise NothingMatched(f"{p.key}: no open-access PDF known")
            print(f"fetching {url}", file=sys.stderr)
            response = client.get(url)
            if response.status_code != 200:
                raise http.HttpError(f"HTTP {response.status_code} for {url}")
            if not response.content.startswith(b"%PDF"):
                raise http.HttpError(f"not a PDF: {url} (a landing page or a paywall?)")
            with library_lock(lib.root):
                atomic_write(path, response.content)  # git-ignored: never committed
    if not path.exists():
        raise NothingMatched(f"{p.key}: no PDF; try `ruru pdf {p.key} --fetch`")
    if args.open:
        subprocess.run([opener, str(path)], check=False)
    print(path)
    return OK


def cmd_tui(args: argparse.Namespace) -> int:
    try:
        tui = importlib.import_module("ruru.tui")  # lazy: the core never imports Textual
    except ImportError:
        print("ruru tui needs the optional extra: pip install 'ruru[tui]'", file=sys.stderr)
        return USAGE
    return int(tui.run(args))  # pragma: no cover - lands in M6


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
