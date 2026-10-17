"""The inbox: one front-matter-only file per unseen item, `inbox/<fetched date>/<id>.md`.

Ids are short and stable, derived from the item's identifiers: `arxiv-2610.01234`,
`doi-10.1021-acs.analchem.6b01234`, `oa-W4392…`, or `rss-<hash>` when there is nothing better.
Every triage decision is appended to `seen.jsonl`, which, with the library and the inbox, is what
`fetch` dedupes against.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from ruru import model
from ruru.errors import UsageError
from ruru.model import ID_KINDS, Paper, norm_id
from ruru.text import norm_title

# Inbox-only fields, around the paper's bibliographic ones.
ITEM_FIELDS = (
    "id", "title", "authors", "year", "venue", "type", "volume", "issue", "pages", "publisher", "ids",
    "url", "pdf_url", "abstract", "source", "published", "fetched", "suggestion",
)
_PAPER_FIELDS = tuple(f for f in ITEM_FIELDS if f in model.FIELDS)
DECISIONS = ("keep", "skip", "later")


class UnknownItem(UsageError):
    def __init__(self, item_id: str) -> None:
        super().__init__(f"no inbox item {item_id!r}")


def item_id(paper: Paper, fallback: str = "") -> str:
    if arxiv := norm_id("arxiv", paper.ids.get("arxiv")):
        return "arxiv-" + arxiv.replace("/", "_")
    if doi := norm_id("doi", paper.ids.get("doi")):
        return "doi-" + re.sub(r"[^a-z0-9.]+", "-", doi).strip("-")
    if oa := norm_id("openalex", paper.ids.get("openalex")):
        return "oa-" + oa
    # An article id at the end of the entry's URL (ScienceDirect: …/pii/S0021967326008472).
    tail = re.sub(r"[^a-z0-9.]+", "-", urlsplit(fallback).path.rsplit("/", 1)[-1].lower()).strip("-.")
    if 6 <= len(tail) <= 40 and any(c.isdigit() for c in tail):
        return "rss-" + tail
    return "rss-" + hashlib.sha1((fallback or paper.title).encode()).hexdigest()[:10]


@dataclass
class Item:
    id: str
    paper: Paper
    fetched: date
    published: date | None = None
    suggestion: dict[str, Any] | None = None
    path: Path | None = field(default=None, compare=False)

    def front_matter(self) -> dict[str, Any]:
        fm = self.paper.front_matter()
        out: dict[str, Any] = {"id": self.id}
        out |= {k: fm[k] for k in _PAPER_FIELDS}
        out |= {"published": self.published, "fetched": self.fetched, "suggestion": self.suggestion}
        return out


def dumps(item: Item) -> str:
    return "---\n" + "".join(model.dump_value(k, v) for k, v in item.front_matter().items()) + "---\n"


def loads(text: str, path: Path | None = None) -> Item:
    if not text.startswith("---\n") or "\n---" not in text[3:]:
        raise model.FrontMatterError("inbox item must be a front matter block")
    fm = yaml.safe_load(text[4:text.index("\n---", 3) + 1]) or {}
    if not isinstance(fm, dict) or "id" not in fm:
        raise model.FrontMatterError("inbox item needs an id")
    paper = model.from_front_matter({k: v for k, v in fm.items() if k in _PAPER_FIELDS}, body="")
    paper.status = "to-read"
    published = fm.get("published")
    return Item(
        id=str(fm["id"]), paper=paper,
        fetched=model._as_date(fm.get("fetched")) or model.today(),
        published=model._as_date(published) if published else None,
        suggestion=fm.get("suggestion"), path=path,
    )


class Inbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / "inbox"

    def path_for(self, item: Item) -> Path:
        return self.dir / item.fetched.isoformat() / f"{item.id}.md"

    def items(self) -> list[Item]:
        """Newest first: by fetch date, then publication date, then id."""
        out = []
        for path in self.dir.glob("*/*.md") if self.dir.is_dir() else []:
            try:
                out.append(loads(path.read_text(encoding="utf-8"), path))
            except (ValueError, yaml.YAMLError) as e:
                print(f"ruru: warning: skipping {path.relative_to(self.root)}: {e}", file=sys.stderr)
        out.sort(key=lambda i: (i.fetched, i.published or date.min, i.id), reverse=True)
        return out

    def get(self, item_id: str) -> Item:
        for path in self.dir.glob(f"*/{item_id}.md") if self.dir.is_dir() else []:
            return loads(path.read_text(encoding="utf-8"), path)
        raise UnknownItem(item_id)

    def exists(self, item_id: str) -> bool:
        return self.dir.is_dir() and any(self.dir.glob(f"*/{item_id}.md"))


# --- seen.jsonl ----------------------------------------------------------------------------------

def seen_record(item: Item, decision: str, key: str | None = None) -> dict[str, Any]:
    return {
        "id": item.id, "decision": decision, "date": model.today().isoformat(), "key": key,
        "ids": {k: v for k, v in item.paper.ids.items() if v}, "title": item.paper.title, "year": item.paper.year,
    }


def read_seen(root: Path) -> list[dict[str, Any]]:
    path = root / "seen.jsonl"
    if not path.exists():
        return []
    records = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            print(f"ruru: warning: seen.jsonl line {n} is not JSON; ignored", file=sys.stderr)
    return records


def append_seen(root: Path, records: list[dict[str, Any]]) -> str:
    """The new text of seen.jsonl (written by the caller's transaction)."""
    path = root / "seen.jsonl"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old and not old.endswith("\n"):
        old += "\n"
    return old + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)


# --- dedupe --------------------------------------------------------------------------------------

class Deduper:
    """The `same_paper` rule as set lookups, over everything already known."""

    def __init__(self) -> None:
        self.ids: dict[str, set[str]] = {k: set() for k in ID_KINDS}
        self.titles: dict[str, list[int | None]] = {}

    def add(self, ids: dict[str, Any], title: str, year: int | None) -> None:
        for kind in ID_KINDS:
            if value := norm_id(kind, ids.get(kind)):
                self.ids[kind].add(value)
        if t := norm_title(title or ""):
            self.titles.setdefault(t, []).append(year)

    def add_paper(self, p: Paper) -> None:
        self.add(p.ids, p.title, p.year)

    def seen(self, p: Paper) -> bool:
        for kind in ID_KINDS:
            value = norm_id(kind, p.ids.get(kind))
            if value and value in self.ids[kind]:
                return True
        years = self.titles.get(norm_title(p.title or ""))
        if years is None:
            return False
        return any(y is None or p.year is None or abs(y - p.year) <= 1 for y in years)

    @classmethod
    def everything(cls, papers: list[Paper], items: list[Item], seen: list[dict[str, Any]]) -> Deduper:
        d = cls()
        for p in papers:
            d.add_paper(p)
        for i in items:
            d.add_paper(i.paper)
        for r in seen:
            d.add(r.get("ids") or {}, r.get("title") or "", r.get("year"))
        return d
