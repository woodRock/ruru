"""Feeds: declared in ruru.toml, fetched into the inbox.

Kinds:
  arxiv     `query`: an arXiv API search_query, e.g. 'cat:physics.chem-ph AND abs:chromatography'
  openalex  `filter` (an OpenAlex filter, e.g. 'authorships.author.id:A1|A2') and/or `search`
            (a phrase matched in titles and abstracts)
  crossref  `issn`: a journal, by ISSN (new works as Crossref registers them)
  rss       `url`: an RSS/Atom feed; entries with a DOI are resolved through Crossref

Fetch state (last successful fetch per feed) lives in `.ruru/feeds.json`, not in git.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import feedparser

from ruru import tomledit
from ruru.errors import UsageError
from ruru.http import Client, HttpError
from ruru.model import Paper, norm_id
from ruru.resolve import arxiv, crossref, openalex
from ruru.resolve.crossref import strip_markup
from ruru.text import display_name

KINDS = ("arxiv", "openalex", "crossref", "rss")
REQUIRED = {"arxiv": ("query",), "crossref": ("issn",), "rss": ("url",)}
FIRST_FETCH_DAYS = 7  # how far back a feed's first fetch looks
OVERLAP_DAYS = 1  # later fetches re-read a day, so nothing slips between runs (dedupe drops repeats)
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class Feed:
    name: str
    kind: str
    title: str | None = None
    query: str | None = None
    filter: str | None = None
    search: str | None = None
    issn: str | None = None
    url: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Feed:
        unknown = set(d) - set(cls.__dataclass_fields__)
        if unknown:
            raise UsageError(f"feed {d.get('name')!r}: unknown field(s) {', '.join(sorted(unknown))}")
        feed = cls(**{k: (None if v is None else str(v)) for k, v in d.items()})  # type: ignore[arg-type]
        feed.validate()
        return feed

    def validate(self) -> None:
        if not self.name or not _NAME.match(self.name):
            raise UsageError(f"feed name {self.name!r}: use lowercase letters, digits and hyphens")
        if self.kind not in KINDS:
            raise UsageError(f"feed {self.name!r}: kind must be one of {', '.join(KINDS)}")
        missing = [f for f in REQUIRED.get(self.kind, ()) if not getattr(self, f)]
        if self.kind == "openalex" and not (self.filter or self.search):
            missing = ["filter or search"]
        if missing:
            raise UsageError(f"feed {self.name!r} ({self.kind}) needs {', '.join(missing)}")

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def describe(self) -> str:
        search = (self.search if '"' in self.search else f'"{self.search}"') if self.search else None
        what = self.query or self.issn or self.url or " + ".join(x for x in (self.filter, search) if x)
        return f"{self.kind}: {what}" + (f"  ({self.title})" if self.title else "")


def load(settings_feeds: list[dict[str, Any]]) -> list[Feed]:
    feeds = [Feed.from_dict(d) for d in settings_feeds]
    names = [f.name for f in feeds]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise UsageError(f"ruru.toml: duplicate feed name(s): {', '.join(dupes)}")
    return feeds


def add_to_toml(text: str, feed: Feed) -> str:
    return tomledit.append_table(text, "[[feeds]]", feed.to_dict())


def remove_from_toml(text: str, name: str) -> str:
    try:
        return tomledit.remove_table(text, "[[feeds]]", name)
    except KeyError:
        raise UsageError(f"no feed named {name!r}") from None


# --- state ---------------------------------------------------------------------------------------

def state_path(root: Path) -> Path:
    return root / ".ruru" / "feeds.json"


def load_state(root: Path) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(state_path(root).read_text())
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def since_for(feed: Feed, state: dict[str, dict[str, str]], today: date) -> date:
    last = (state.get(feed.name) or {}).get("last_fetch")
    try:
        if last:
            return date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS)
    except ValueError:
        pass  # a corrupt state file: treat the feed as new
    return today - timedelta(days=FIRST_FETCH_DAYS)


# --- fetching ------------------------------------------------------------------------------------

@dataclass
class Candidate:
    paper: Paper
    published: date | None
    needs_resolve: bool = False  # an RSS stub with a DOI: resolve it only if it is new
    link: str = ""


def _arxiv(client: Client, feed: Feed, since: date, limit: int) -> list[Candidate]:
    out: list[Candidate] = []
    start = 0
    while len(out) < limit:
        page = arxiv.query(client, feed.query or "", start=start, max_results=min(100, limit))
        out += [Candidate(p, d) for p, d in page if d is None or d >= since]
        if len(page) < min(100, limit) or any(d is not None and d < since for _, d in page):
            break
        start += len(page)
    return out[:limit]


_DOI_IN = re.compile(r"(10\.\d{4,9}/[^\s?#<>\"]+)")


def _entry_doi(e: Any) -> str | None:
    for value in (e.get("prism_doi"), e.get("dc_identifier"), e.get("id"), e.get("link")):
        if value and (m := _DOI_IN.search(str(value))):
            return norm_id("doi", m.group(1))
    return None


def _entry_date(e: Any) -> date | None:
    for key in ("published_parsed", "updated_parsed"):
        if e.get(key):
            return date(*e[key][:3])
    # ScienceDirect: '<p>Publication date: 22 November 2026</p>'
    if m := re.search(r"Publication date:\s*(\d{1,2} \w+ \d{4})", e.get("summary", "")):
        try:
            return datetime.strptime(m.group(1), "%d %B %Y").date()
        except ValueError:
            return None
    return None


def _rss_stub(e: Any) -> Paper:
    summary = e.get("summary", "")
    text = re.sub(r"<[^>]+>", "\n", summary)
    authors = [display_name(a.strip()) for a in re.split(r",|\n|;", e.get("author", "")) if a.strip()]
    if not authors and (m := re.search(r"Author\(s\):\s*(.+)", text)):
        authors = [a.strip() for a in m.group(1).split(",") if a.strip()]
    venue = None
    if m := re.search(r"Source:\s*([^,\n]+)", text):
        venue = m.group(1).strip()
    venue = e.get("prism_publicationname") or venue
    published = _entry_date(e)
    paper = Paper(title=strip_markup(e.get("title")) or "", authors=authors, venue=venue,
                  year=published.year if published else None, type="article",
                  url=e.get("id") if str(e.get("id", "")).startswith("http") else e.get("link"))
    paper.ids["doi"] = _entry_doi(e)
    return paper


def _rss(client: Client, feed: Feed, since: date, limit: int) -> list[Candidate]:
    d = feedparser.parse(client.get_text(feed.url or "", what=f"feed {feed.name}"))
    if not d.get("version"):
        raise HttpError(f"{feed.url}: not an RSS or Atom feed (blocked, or moved?)")
    out = []
    for e in d.entries[:limit]:
        published = _entry_date(e)
        if published and published < since:
            continue
        stub = _rss_stub(e)
        out.append(Candidate(stub, published, needs_resolve=bool(stub.ids["doi"]), link=e.get("link", "")))
    return out


def fetch(client: Client, feed: Feed, since: date, *, limit: int = 200) -> list[Candidate]:
    if feed.kind == "arxiv":
        return _arxiv(client, feed, since, limit)
    if feed.kind == "openalex":
        pairs = openalex.works_feed(client, since=since, filter=feed.filter, search=feed.search, limit=limit)
        return [Candidate(p, d) for p, d in pairs]
    if feed.kind == "crossref":
        pairs = crossref.journal_feed(client, feed.issn or "", since=since, limit=limit)
        return [Candidate(p, d) for p, d in pairs]
    return _rss(client, feed, since, limit)
