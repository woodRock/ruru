"""Fetching into the inbox and triaging it: fetch, keep/later/skip, unkeep/unskip, suggestions."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from ruru import feeds as feedmod
from ruru import gitops, model
from ruru.errors import RuruError, UsageError
from ruru.feeds import Feed
from ruru.fsutil import atomic_write
from ruru.http import Client
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
from ruru.library import Library
from ruru.ops.common import summary_of
from ruru.resolve import Resolver, merge


@dataclass
class FeedResult:
    name: str
    new: int = 0
    seen: int = 0
    error: str | None = None


def fetch(lib: Library, client: Client, feeds: Sequence[Feed], *, since: date | None = None,
          now: datetime | None = None) -> tuple[list[FeedResult], list[Item]]:
    """Pull every feed; new items (not in the library, the inbox or seen.jsonl) go to the inbox."""
    now = now or model.now()
    today = now.date()
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
            items.append(Item(item_id(paper, c.link), paper, fetched=now, published=c.published))
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
        tx.message = f"ruru: {decision} {summary_of([k or i.id for i, k in out])}{labels}{who}"
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
    with lib.transaction(f"ruru: unskip {summary_of(list(ids))}") as tx:
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
        items.append(Item(iid, p, fetched=model.now(), published=published))
    removed = {p.key for p in papers}
    with lib.transaction(f"ruru: unkeep {summary_of(sorted(removed))}") as tx:
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
