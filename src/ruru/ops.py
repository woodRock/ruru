"""Library operations shared by the CLI and the TUI: fetch, triage, suggestions, feed settings,
enriching imported papers.

Network work happens before any lock is taken; each operation then writes in one transaction.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ruru import feeds as feedmod
from ruru import gitops, model
from ruru import projects as projmod
from ruru.bibtex import to_bibtex
from ruru.config import SETTINGS_FILE
from ruru.errors import NothingMatched, RuruError, UnknownKey, UsageError
from ruru.feeds import Feed
from ruru.fsutil import atomic_write, library_lock
from ruru.http import Client, HttpError
from ruru.inbox import (
    DECISIONS,
    Deduper,
    Inbox,
    Item,
    append_seen,
    dumps,
    item_id,
    read_seen,
    read_viewed,
    seen_record,
    write_viewed,
)
from ruru.inbox import loads as load_item
from ruru.keys import base_key, unique_key
from ruru.library import Library
from ruru.projects import BibFile, Project, Report, bib_encoding, duplicate_keys, entry_keys, scan_tex
from ruru.resolve import Resolver, changes, fill_gaps, merge
from ruru.reviews import Review, Reviews, problems
from ruru.reviews import dumps as reviews_dumps
from ruru.reviews import loads as load_review
from ruru.reviews import slug as reviews_slug
from ruru.text import norm_title


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
    starts: dict[str, str] = {}
    for feed in feeds:
        result = FeedResult(feed.name)
        results.append(result)
        window = feedmod.since_for(feed, state, today)
        starts[feed.name] = (state.get(feed.name) or {}).get("start") or (since or window).isoformat()
        try:
            candidates = feedmod.fetch(client, feed, since or window)
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
            state[r.name] = {"last_fetch": today.isoformat(), "start": starts[r.name]}
    atomic_write(feedmod.state_path(lib.root), json.dumps(state, indent=2, sort_keys=True) + "\n")
    return results, items


def triage(lib: Library, ids: Sequence[str], decision: str, *, tags: Sequence[str] = (),
           projects: Sequence[str] = (), reason: str | None = None,
           by: str | None = None) -> list[tuple[Item, str | None]]:
    """keep / later move items into the library: keep as read (the Library), later as `later` (the
    reading list); skip drops them.
    Every decision is recorded in seen.jsonl, with `by` and `reason` when an agent decided (an
    agent applying its own suggestion inherits the suggestion's reason). Returns (item, key or None)."""
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
                lib.set_status(paper, "read" if decision == "keep" else "later")
                paper, new = lib.add(tx, paper)
                if not new and (tags or projects):  # already kept under this key: just add the labels
                    paper.tags = sorted(set(paper.tags) | set(tags))
                    paper.projects = sorted(set(paper.projects) | set(projects))
                    tx.save(paper)
                key = paper.key
            assert item.path is not None
            tx.remove(item.path)
            why = reason
            if by and not why and item.suggestion and item.suggestion.get("action") == decision:
                why = str(item.suggestion.get("reason") or "") or None
            records.append(seen_record(item, decision, key, reason=why, by=by))
            out.append((item, key))
        tx.write(lib.root / "seen.jsonl", append_seen(lib.root, records))
        labels = f" (tags: {', '.join(tags)})" if tags else ""
        who = f" (by {by})" if by else ""
        tx.message = f"ruru: {decision} {_summary([k or i.id for i, k in out])}{labels}{who}"
    return out


def unskip(lib: Library, ids: Sequence[str]) -> list[Item]:
    """Undo skips: restore each item's inbox file from the commit that removed it, and drop its
    skip record from seen.jsonl so `fetch` no longer filters it out."""
    if not gitops.is_repo(lib.root):
        raise UsageError("unskip restores items from git history, and this library is not a git repository")
    inbox = Inbox(lib.root)
    last = {r.get("id"): r for r in read_seen(lib.root)}
    restored: list[tuple[str, str]] = []
    for wanted in ids:
        if inbox.exists(wanted):
            raise UsageError(f"{wanted} is already in the inbox")
        record = last.get(wanted)
        if record and record.get("decision") != "skip":
            raise UsageError(f"{wanted} was not skipped: {record.get('decision')}"
                             + (f" -> {record['key']}" if record.get("key") else ""))
        found = gitops.deleted_file(lib.root, f"inbox/*/{wanted}.md")
        if not found:
            raise UsageError(f"no skipped inbox item {wanted!r} in the library's history")
        restored.append(found)
    with lib.transaction(f"ruru: unskip {_summary(list(ids))}") as tx:
        for path, text in restored:
            tx.write(lib.root / path, text)
        undo = set(ids)
        kept = [r for r in read_seen(lib.root) if not (r.get("id") in undo and r.get("decision") == "skip")]
        tx.write(lib.root / "seen.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept))
    write_viewed(lib.root, read_viewed(lib.root) - set(ids))  # back as new
    return [inbox.get(i) for i in ids]


def unkeep(lib: Library, keys: Sequence[str]) -> list[Item]:
    """Move papers back to the inbox, as new (the reverse of keep/later), in one commit. Refuses
    papers with what an inbox item can't hold or that something depends on: notes, a PDF, a project.
    Tags and metadata go with the item; the keep/later record leaves seen.jsonl."""
    papers = [lib.get(k) for k in dict.fromkeys(keys)]  # unknown keys fail before anything is written
    if not papers:
        raise UsageError("give at least one key")
    for p in papers:
        held = [what for what, has in (("notes", p.notes.strip() or p.agent_notes.strip()),
                                        ("a PDF", lib.has_pdf(p.key)),
                                        (f"project {', '.join(p.projects)}", p.projects)) if has]
        if held:
            raise UsageError(f"{p.key} has {' and '.join(held)}, so it stays in the library")
    inbox = Inbox(lib.root)
    items = []
    for p in papers:
        iid = item_id(p, p.url or "")
        if inbox.exists(iid):
            raise UsageError(f"{p.key} is already in the inbox as {iid}")
        old = gitops.deleted_file(lib.root, f"inbox/*/{iid}.md") if gitops.is_repo(lib.root) else None
        published = load_item(old[1]).published if old else None  # from when it was first fetched
        p.body, p.status = "", "to-read"
        items.append(Item(iid, p, fetched=model.today(), published=published))
    removed = {p.key for p in papers}
    with lib.transaction(f"ruru: unkeep {_summary(sorted(removed))}") as tx:
        for p, item in zip(papers, items):
            d = lib.paper_dir(p.key)
            tx.remove(d / "paper.bib")
            tx.remove(d / "paper.md")
            item.path = inbox.path_for(item)
            tx.write(item.path, dumps(item))
        kept = [r for r in read_seen(lib.root) if not (r.get("key") in removed and r.get("decision") != "remove")]
        tx.write(lib.root / "seen.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept))
    write_viewed(lib.root, read_viewed(lib.root) - {i.id for i in items})
    return items


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


# --- paper edits (shared by `tag`, `status`, `note`, `pdf` and the TUI) ---------------------------

def remove(lib: Library, keys: Sequence[str]) -> list[model.Paper]:
    """Delete papers from the library (paper.md, paper.bib and the git-ignored PDF), in one commit.
    Each gets a `remove` record in seen.jsonl so feeds don't bring it back; `git revert` undoes it
    (except the PDF). A project bib keeps the entry: `cite` leaves unknown managed keys alone."""
    if not keys:
        raise UsageError("give at least one key")
    papers = [lib.get(k) for k in dict.fromkeys(keys)]  # unknown keys fail before anything is removed
    records = [{"id": p.key, "decision": "remove", "date": model.today().isoformat(), "key": p.key,
                "ids": {k: v for k, v in p.ids.items() if v}, "title": p.title, "year": p.year} for p in papers]
    with lib.transaction(f"ruru: rm {_summary([p.key for p in papers])}") as tx:
        for p in papers:
            d = lib.paper_dir(p.key)
            (d / "paper.pdf").unlink(missing_ok=True)  # git-ignored: not part of the commit
            tx.remove(d / "paper.bib")
            tx.remove(d / "paper.md")  # last: removes the directory once it is empty
        tx.write(lib.root / "seen.jsonl", append_seen(lib.root, records))
    return papers


def add_review(lib: Library, text: str, *, by: str | None = None, replace: bool = False) -> Review:
    """File a review (`reviews.loads` text) as new. Every paper it covers must be on the reading
    list (`model.UNREAD`): reviews are for what Jesse hasn't read yet. Missing `id`/`created`/`by`
    are filled in; `replace` overwrites a review with the same id that is still new."""
    review = load_review(text)
    review.created = review.created or model.now()
    day = review.created.date() if isinstance(review.created, datetime) else review.created
    review.id = review.id or reviews_slug(review.title, day)
    review.by = review.by or by
    review.status = "new"
    review.tags = sorted({t.strip().lower() for t in review.tags if t.strip()})
    issues = problems(review)
    papers = {k: lib.get(k) for k in review.papers if (lib.paper_dir(k) / "paper.md").exists()}
    if unknown := [k for k in review.papers if k not in papers]:
        issues.append(f"no paper with key {', '.join(unknown)}")
    if read := [f"{k} ({p.status})" for k, p in papers.items() if p.status not in model.UNREAD]:
        issues.append(f"{', '.join(read)} not on the reading list: reviews only cover papers yet to be read")
    if no_pdf := [k for k in papers if not lib.has_pdf(k)]:
        issues.append(f"no PDF for {', '.join(no_pdf)}: a review needs every paper's full text "
                      "(`ruru pdf missing --open`, save each as KEY.pdf, then `ruru pdf add`)")
    if issues:
        raise UsageError("review not filed: " + "; ".join(issues))
    store = Reviews(lib.root)
    path = store.path_for(review.id)
    if path.exists():
        current = store.get(review.id)
        if not replace or current.status != "new":
            hint = "; --replace overwrites it" if current.status == "new" else " (kept: give a new id)"
            raise UsageError(f"there is already a review {review.id!r}{hint}")
    review.path = path
    with lib.transaction(f"ruru: review {review.id}" + (f" ({review.by})" if review.by else "")) as tx:
        tx.write(path, reviews_dumps(review))
    return review


def triage_reviews(lib: Library, ids: Sequence[str], decision: str) -> list[Review]:
    """keep: the review stays, no longer new; discard: deleted (git history keeps it)."""
    if decision not in ("keep", "discard"):
        raise UsageError("decision must be keep or discard")
    store = Reviews(lib.root)
    found = [store.get(i) for i in dict.fromkeys(ids)]  # unknown ids fail before anything is written
    if not found:
        raise UsageError("give at least one review id")
    with lib.transaction(f"ruru: review {decision} {_summary([r.id for r in found])}") as tx:
        for r in found:
            assert r.path is not None
            if decision == "discard":
                tx.remove(r.path)
            else:
                r.status = "kept"
                tx.write(r.path, reviews_dumps(r))
    return found


def tag(lib: Library, keys: Sequence[str], add: Sequence[str] = (),
        remove: Sequence[str] = ()) -> list[model.Paper]:
    if not keys or not (add or remove):
        raise UsageError("usage: ruru tag KEY… +TAG… -TAG…")
    papers = [lib.get(k) for k in keys]  # unknown keys fail before anything is written
    ops = " ".join([f"+{t}" for t in add] + [f"-{t}" for t in remove])
    with lib.transaction(f"ruru: tag {' '.join(keys)} {ops}") as tx:
        for p in papers:
            p.tags = sorted((set(p.tags) | set(add)) - set(remove))
            tx.save(p)
    return papers


def set_status(lib: Library, keys: Sequence[str], status: str) -> list[model.Paper]:
    papers = [lib.get(k) for k in keys]
    with lib.transaction(f"ruru: status {' '.join(keys)} {status}") as tx:
        for p in papers:
            lib.set_status(p, status)
            tx.save(p)
    return papers


def set_notes(lib: Library, key: str, text: str) -> bool:
    """Replace Jesse's `## Notes` section. False (and nothing written) if it is unchanged."""
    paper = lib.get(key)
    if text.strip("\n") == paper.notes:
        return False
    with lib.transaction(f"ruru: notes {key}") as tx:
        paper.body = model.set_notes(paper.body, text)
        tx.save(paper)
    return True


@dataclass
class EnrichResult:
    key: str
    filled: dict[str, object]  # field (or "ids") -> its new value
    error: str | None = None


def enrich(lib: Library, client: Client, keys: Sequence[str] = (), *,
           dry_run: bool = False) -> tuple[list[EnrichResult], list[RuruError]]:
    """Fill the empty metadata (abstract, ids, venue, …) of `keys`, or of every paper without an
    abstract, from Crossref/OpenAlex/arXiv. Never changes a value that is there, so it is safe on
    BibTeX imports, and takes nothing from a record whose title differs from the paper's."""
    papers = [lib.get(k) for k in keys] if keys else [p for p in lib.papers() if not p.abstract]
    resolver = Resolver(client)
    results: list[EnrichResult] = []
    failures: list[RuruError] = []
    found: dict[str, model.Paper] = {}
    for p in papers:
        try:
            upstream = resolver.lookup(p)
            if norm_title(upstream.title) != norm_title(p.title):  # e.g. an arXiv id leading to a retitled article
                raise UsageError(f"the record found is titled {upstream.title!r}; not filled "
                                 f"(`ruru refresh {p.key}` would take it as it is)")
            new = fill_gaps(p, upstream)
            probe = model.Paper(ids={k: v for k, v in new.ids.items() if v and not p.ids.get(k)})
            other = lib.find_duplicate(probe) if any(probe.ids.values()) else None
            if other and other.key != p.key:
                raise UsageError(f"the record found is {other.key}'s (same id); not filled")
        except RuruError as e:
            print(f"ruru: {p.key}: {e}", file=sys.stderr)
            failures.append(e)
            results.append(EnrichResult(p.key, {}, str(e)))
            continue
        filled = {f: n for f, (_, n) in changes(p, new).items()}
        results.append(EnrichResult(p.key, filled))
        if filled:
            found[p.key] = upstream
    if found and not dry_run:
        with lib.transaction(f"ruru: enrich {_summary(list(found))}") as tx:
            for key, upstream in found.items():
                tx.save(fill_gaps(lib.get(key), upstream))  # re-read: someone may have edited it meanwhile
    return results, failures


def paper_link(p: model.Paper) -> str | None:
    """Where to read or download a paper: its DOI (the publisher, institutional sign-in), else its URL."""
    return f"https://doi.org/{p.ids['doi']}" if p.ids.get("doi") else p.url


def add_pdfs(lib: Library, paths: Sequence[Path], *, key: str | None = None,
             force: bool = False) -> tuple[list[tuple[str, Path]], list[tuple[Path, str]]]:
    """Move downloaded PDFs to papers/KEY/paper.pdf. A file is matched by its name (`KEY.pdf`, case
    aside) or `key`; a directory contributes every PDF named after a library key and ignores the
    rest. Returns (key, source) for each filed PDF and (path, problem) for each refused one."""
    known = lib.keys()
    keys = {k.lower(): k for k in known}
    if key and key not in known:
        raise UnknownKey(key)
    if key and (len(paths) != 1 or paths[0].is_dir()):
        raise UsageError("--key names one file")
    candidates: list[tuple[Path, str | None]] = []
    for path in paths:
        if path.is_dir():
            candidates += [(f, keys[f.stem.lower()]) for f in sorted(path.glob("*.pdf")) if f.stem.lower() in keys]
        else:
            candidates.append((path, key or keys.get(path.stem.lower())))
    filed: list[tuple[str, Path]] = []
    refused: list[tuple[Path, str]] = []
    with library_lock(lib.root):
        for src, k in candidates:
            dest = lib.paper_dir(k) / "paper.pdf" if k else None
            if not src.is_file():
                refused.append((src, "no such file"))
            elif not k or dest is None:
                refused.append((src, f"no paper with key {src.stem!r} (name it KEY.pdf, or use --key)"))
            elif not src.read_bytes()[:5].startswith(b"%PDF"):
                refused.append((src, "not a PDF (a saved login or landing page?)"))
            elif dest.exists() and not force:
                refused.append((src, f"{k} already has a PDF (--force replaces it)"))
            else:
                shutil.move(str(src), dest)  # git-ignored: nothing to commit
                filed.append((k, src))
    return filed, refused


def fetch_pdf(lib: Library, client: Client, key: str, *, force: bool = False) -> Path:
    """Download the open-access PDF to paper.pdf (git-ignored), finding its URL first if needed."""
    paper = lib.get(key)
    path = lib.paper_dir(key) / "paper.pdf"
    if path.exists() and not force:
        return path
    url = paper.pdf_url
    if not url:
        url = Resolver(client).refresh(paper).pdf_url
        if url:
            with lib.transaction(f"ruru: pdf_url {key}") as tx:
                current = lib.get(key)
                current.pdf_url = url
                tx.save(current)
    if not url:
        raise NothingMatched(f"{key}: no open-access PDF known")
    print(f"fetching {url}", file=sys.stderr)
    response = client.get(url)
    if response.status_code != 200:
        raise HttpError(f"HTTP {response.status_code} for {url}")
    if not response.content.startswith(b"%PDF"):
        raise HttpError(f"not a PDF: {url} (a landing page or a paywall?)")
    with library_lock(lib.root):
        atomic_write(path, response.content)
    return path
