"""Library operations shared by the CLI and the TUI: fetch, triage, suggestions, feed settings.

Network work happens before any lock is taken; each operation then writes in one transaction.
"""

from __future__ import annotations

import json
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ruru import feeds as feedmod
from ruru import model
from ruru.config import SETTINGS_FILE
from ruru.errors import RuruError, UsageError
from ruru.feeds import Feed
from ruru.fsutil import atomic_write
from ruru.http import Client
from ruru.inbox import DECISIONS, Deduper, Inbox, Item, append_seen, dumps, item_id, read_seen, seen_record
from ruru.library import Library
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
    with lib.transaction(message) as tx:
        tx.write(lib.root / SETTINGS_FILE, text)
    lib.settings.feeds = list(raw.get("feeds", []))


def add_feed(lib: Library, feed: Feed) -> None:
    feed.validate()
    if feed.name in {f.get("name") for f in lib.settings.feeds}:
        raise UsageError(f"there is already a feed named {feed.name!r}")
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    _rewrite_settings(lib, feedmod.add_to_toml(text, feed), f"ruru: feeds add {feed.name}")


def remove_feed(lib: Library, name: str) -> None:
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    _rewrite_settings(lib, feedmod.remove_from_toml(text, name), f"ruru: feeds remove {name}")
