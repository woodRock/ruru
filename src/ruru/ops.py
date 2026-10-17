"""Library operations shared by the CLI and the TUI: fetch, triage, suggestions, feed settings.

Network work happens before any lock is taken; each operation then writes in one transaction.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ruru import feeds as feedmod
from ruru import model
from ruru import projects as projmod
from ruru.bibtex import to_bibtex
from ruru.config import SETTINGS_FILE
from ruru.errors import RuruError, UsageError
from ruru.feeds import Feed
from ruru.fsutil import atomic_write
from ruru.http import Client
from ruru.inbox import DECISIONS, Deduper, Inbox, Item, append_seen, dumps, item_id, read_seen, seen_record
from ruru.keys import base_key, unique_key
from ruru.library import Library
from ruru.projects import BibFile, Project, Report, bib_encoding, duplicate_keys, entry_keys, scan_tex
from ruru.resolve import Resolver, merge


@dataclass
class FeedResult:
    name: str
    new: int = 0
    seen: int = 0
    error: str | None = None


def _summary(names: list[str], limit: int = 5) -> str:
    return " ".join(names) if len(names) <= limit else f"{' '.join(names[:limit])} +{len(names) - limit} more"


def fetch(lib: Library, client: Client, feeds: Sequence[Feed], *, since: date | None = None,
          today: date | None = None) -> tuple[list[FeedResult], list[Item]]:
    """Pull every feed; new items (not in the library, the inbox or seen.jsonl) go to the inbox."""
    today = today or model.today()
    inbox = Inbox(lib.root)
    state = feedmod.load_state(lib.root)
    dedupe = Deduper.everything(list(lib.papers()), inbox.items(), read_seen(lib.root))
    resolver = Resolver(client)
    results: list[FeedResult] = []
    items: list[Item] = []
    for feed in feeds:
        result = FeedResult(feed.name)
        results.append(result)
        try:
            candidates = feedmod.fetch(client, feed, since or feedmod.since_for(feed, state, today))
        except RuruError as e:
            result.error = str(e)
            continue
        for c in candidates:
            if dedupe.seen(c.paper):
                result.seen += 1
                continue
            paper = c.paper
            if c.needs_resolve:
                try:
                    paper = merge(resolver.by_doi(paper.ids["doi"] or ""), paper)
                except RuruError as e:
                    print(f"ruru: warning: {feed.name}: doi:{paper.ids['doi']}: {e} (kept the feed's details)",
                          file=sys.stderr)
            paper.source = f"feed:{feed.name}"
            dedupe.add_paper(paper)
            items.append(Item(item_id(paper, c.link), paper, fetched=today, published=c.published))
            result.new += 1
    if items:
        counts = ", ".join(f"{r.name} {r.new}" for r in results if r.new)
        with lib.transaction(f"ruru: fetch {len(items)} new ({counts})") as tx:
            for item in items:
                if not inbox.exists(item.id):
                    item.path = inbox.path_for(item)
                    tx.write(item.path, dumps(item))
    for r in results:
        if not r.error:
            state[r.name] = {"last_fetch": today.isoformat()}
    atomic_write(feedmod.state_path(lib.root), json.dumps(state, indent=2, sort_keys=True) + "\n")
    return results, items


def triage(lib: Library, ids: Sequence[str], decision: str, *, tags: Sequence[str] = (),
           projects: Sequence[str] = ()) -> list[tuple[Item, str | None]]:
    """keep / later move items into the library (status to-read / later); skip drops them.
    Every decision is recorded in seen.jsonl. Returns (item, key or None)."""
    if decision not in DECISIONS:
        raise UsageError(f"decision must be one of {', '.join(DECISIONS)}")
    lib.check_projects(list(projects))
    inbox = Inbox(lib.root)
    items = [inbox.get(i) for i in ids]  # unknown ids fail before anything is written
    out: list[tuple[Item, str | None]] = []
    with lib.transaction() as tx:
        records = []
        for item in items:
            key = None
            if decision != "skip":
                paper = item.paper
                paper.body = model.DEFAULT_BODY
                paper.tags = sorted(set(paper.tags) | set(tags))
                paper.projects = sorted(set(paper.projects) | set(projects))
                paper.status = "later" if decision == "later" else "to-read"
                paper, new = lib.add(tx, paper)
                if not new and (tags or projects):  # already kept under this key: just add the labels
                    paper.tags = sorted(set(paper.tags) | set(tags))
                    paper.projects = sorted(set(paper.projects) | set(projects))
                    tx.save(paper)
                key = paper.key
            assert item.path is not None
            tx.remove(item.path)
            records.append(seen_record(item, decision, key))
            out.append((item, key))
        tx.write(lib.root / "seen.jsonl", append_seen(lib.root, records))
        labels = f" (tags: {', '.join(tags)})" if tags else ""
        tx.message = f"ruru: {decision} {_summary([k or i.id for i, k in out])}{labels}"
    return out


def suggest(lib: Library, item_id_: str, action: str, reason: str, by: str) -> Item:
    if action not in DECISIONS:
        raise UsageError(f"suggestion must be one of {', '.join(DECISIONS)}")
    if not reason.strip():
        raise UsageError("a suggestion needs a --reason")
    item = Inbox(lib.root).get(item_id_)
    item.suggestion = {"action": action, "reason": reason.strip(), "by": by, "date": model.today()}
    with lib.transaction(f"ruru: suggest {action} {item.id} ({by})") as tx:
        assert item.path is not None
        tx.write(item.path, dumps(item))
    return item


# --- feed settings -------------------------------------------------------------------------------

def _rewrite_settings(lib: Library, text: str, message: str) -> None:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # pragma: no cover - our edits always produce valid TOML
        raise UsageError(f"refusing to write invalid ruru.toml: {e}") from None
    feedmod.load(list(raw.get("feeds", [])))
    projmod.load(dict(raw.get("projects", {})))
    with lib.transaction(message) as tx:
        tx.write(lib.root / SETTINGS_FILE, text)
    lib.settings.feeds = list(raw.get("feeds", []))
    lib.settings.projects = dict(raw.get("projects", {}))


def add_feed(lib: Library, feed: Feed) -> None:
    feed.validate()
    if feed.name in {f.get("name") for f in lib.settings.feeds}:
        raise UsageError(f"there is already a feed named {feed.name!r}")
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    _rewrite_settings(lib, feedmod.add_to_toml(text, feed), f"ruru: feeds add {feed.name}")


def remove_feed(lib: Library, name: str) -> None:
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    _rewrite_settings(lib, feedmod.remove_from_toml(text, name), f"ruru: feeds remove {name}")


# --- projects ------------------------------------------------------------------------------------

def get_project(lib: Library, name: str) -> Project:
    configured = projmod.load(lib.settings.projects)
    if name not in configured:
        raise UsageError(f"unknown project {name!r}; add it with "
                         f"`ruru project add {name} --path … --bib …`")
    return configured[name]


def add_project(lib: Library, name: str, *, path: str, bib: str, tex: Sequence[str] = (),
                encoding: str | None = None) -> Project:
    values = {"path": path, "bib": bib, "tex": list(tex), "bib_encoding": encoding}
    project = Project.from_dict(name, values)
    if name in lib.settings.projects:
        raise UsageError(f"there is already a project named {name!r}")
    for what, p in [("path", project.path), *[("tex file", t) for t in project.tex_paths]]:
        if not p.exists():
            print(f"ruru: warning: {what} {p} does not exist (yet)", file=sys.stderr)
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    _rewrite_settings(lib, projmod.add_to_toml(text, name, values), f"ruru: project add {name}")
    return project


def remove_project(lib: Library, name: str) -> None:
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    _rewrite_settings(lib, projmod.remove_from_toml(text, name), f"ruru: project remove {name}")


@dataclass
class CiteResult:
    added: list[str]
    skipped: list[str]  # already defined outside the managed block
    foreign: list[str]  # in the block but not in the library: left as they were
    changed: bool  # the bib file was rewritten


def cite(lib: Library, name: str, keys: Sequence[str], *, prune: Sequence[str] = ()) -> CiteResult:
    """Put `keys` in the project's managed block, regenerating every entry there from the library."""
    project = get_project(lib, name)
    papers = [lib.get(k) for k in keys]  # unknown keys fail before anything is written
    path = project.bib_path
    if not path.parent.is_dir():
        raise UsageError(f"{path.parent} does not exist; check the project's path and bib")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    bib = BibFile.parse(text)
    outside = set(bib.outside_keys)
    current = bib.managed
    encoding = bib_encoding(project, lib.settings.bib_encoding)
    skipped = [k for k in keys if k in outside]
    for k in skipped:
        print(f"ruru: warning: {k} is already in {path.name} outside the managed block; left there",
              file=sys.stderr)
    entries, foreign = {}, []
    for k in (set(current) | set(keys)) - outside - set(prune):
        if lib.exists(k):
            entries[k] = to_bibtex(lib.get(k), encoding)
        else:
            entries[k] = current[k]
            foreign.append(k)
    new_text = bib.render(entries)
    with lib.transaction(f"ruru: cite {name} {_summary(list(keys))}".rstrip()) as tx:
        if new_text != text:
            atomic_write(path, new_text)  # the paper repo is Jesse's: written, never committed
        for p in papers:
            if name not in p.projects:
                p.projects = sorted({*p.projects, name})
                tx.save(p)
    added = [k for k in keys if k not in current and k not in skipped]
    return CiteResult(added, skipped, sorted(foreign), new_text != text)


def check(lib: Library, target: str, *, fix: bool = False,
          prune: bool = False) -> tuple[Report, dict[str, list[str]]]:
    """Citation health of a project (or one .tex file). With `fix`, cite the missing keys the library
    has and regenerate stale entries; with `prune`, drop managed entries nothing cites."""
    project: Project | None = None
    if target.endswith(".tex") or Path(target).is_file():
        mains = [Path(target).expanduser()]
    else:
        project = get_project(lib, target)
        mains = project.tex_paths
        if not mains:
            raise UsageError(f"project {target!r} lists no tex files; re-add it with --tex")
    if (fix or prune) and project is None:
        raise UsageError("--fix and --prune need a project (they write its managed block)")
    report = _check(lib, project, mains)
    fixed: dict[str, list[str]] = {}
    if fix or prune:
        add = [m["key"] for m in report.missing if m["in_library"]] if fix else []
        drop = report.unused if prune else []
        if add or drop or (fix and report.stale):
            assert project is not None
            cite(lib, project.name, add, prune=drop)
            fixed = {"cited": add, "regenerated": report.stale if fix else [], "pruned": drop}
            report = _check(lib, project, mains)
    return report, fixed


def _check(lib: Library, project: Project | None, mains: list[Path]) -> Report:
    scan = scan_tex(mains)
    bib_path = project.bib_path if project else (scan.bibs[0] if scan.bibs else None)
    report = Report(project.name if project else None, [str(f) for f in scan.files],
                    str(bib_path) if bib_path else None, len(scan.keys))
    report.warnings += [f"tex file not found: {f}" for f in scan.missing_files]
    if bib_path is None:
        report.warnings.append("no bib file: no \\bibliography or \\addbibresource in the tex")
    elif not bib_path.exists():
        report.warnings.append(f"bib file not found: {bib_path}")
    text = bib_path.read_text(encoding="utf-8") if bib_path and bib_path.exists() else ""
    bib = BibFile.parse(text)
    in_bib = set(entry_keys(text))
    managed = bib.managed
    cited = set(scan.keys)
    report.missing = [{"key": k, "in_library": lib.exists(k)} for k in scan.keys if k not in in_bib]
    if not scan.nocite_all:
        report.unused = sorted(k for k in managed if k not in cited)
    if project:
        report.kept_unused = sorted(p.key for p in lib.papers()
                                    if project.name in p.projects and p.key not in cited)
    encoding = bib_encoding(project, lib.settings.bib_encoding)
    report.stale = sorted(k for k, entry in managed.items()
                          if lib.exists(k) and entry.strip() != to_bibtex(lib.get(k), encoding).strip())
    report.duplicates = duplicate_keys(text) if text else []
    return report


# --- rekey ---------------------------------------------------------------------------------------

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:.+-]*$")


def rekey(lib: Library, old: str, new: str | None = None) -> tuple[str, list[str]]:
    """Rename a paper's key (the only way a key ever changes). `new` defaults to the key its
    current metadata would get. Returns (new key, projects whose .tex and .bib still say `old`)."""
    paper = lib.get(old)
    taken = set(lib.keys()) - {old}
    new = new or unique_key(base_key(paper.authors, paper.year, paper.title), taken)
    if not _KEY.match(new):
        raise UsageError(f"{new!r} is not a usable BibTeX key")
    if new == old:
        raise UsageError(f"{old} already has that key")
    if new in taken:
        raise UsageError(f"key {new!r} is already taken")
    old_dir, new_dir = lib.paper_dir(old), lib.paper_dir(new)
    with lib.transaction(f"ruru: rekey {old} -> {new}") as tx:
        paper.key = new
        tx.save(paper)
        for name in ("paper.md", "paper.bib"):
            tx.remove(old_dir / name)
        if (old_dir / "paper.pdf").exists():
            (old_dir / "paper.pdf").rename(new_dir / "paper.pdf")  # git-ignored: just moved
        with contextlib.suppress(OSError):
            old_dir.rmdir()
    return new, list(paper.projects)
