"""arXiv API (Atom): ID lookup and queries. The client keeps arXiv's 3-second spacing."""

from __future__ import annotations

from datetime import date
from typing import Any

import feedparser

from ruru.http import Client, HttpError, NotFound
from ruru.model import Paper, norm_id

API = "https://export.arxiv.org/api/query"


def _clean(s: str | None) -> str:
    return " ".join((s or "").split())


def entry_to_paper(e: Any) -> Paper:
    aid = norm_id("arxiv", e.get("id", "")) or ""
    published = e.get("published", "")
    paper = Paper(
        title=_clean(e.get("title")),
        authors=[_clean(a.get("name")) for a in e.get("authors", []) if a.get("name")],
        year=int(published[:4]) if published[:4].isdigit() else None,
        type="preprint",
        abstract=_clean(e.get("summary")) or None,
        url=f"https://arxiv.org/abs/{aid}",
        pdf_url=f"https://arxiv.org/pdf/{aid}",
    )
    paper.ids["arxiv"] = aid
    paper.ids["doi"] = norm_id("doi", e.get("arxiv_doi"))  # set by authors once published
    return paper


def parse_dated(text: str) -> list[tuple[Paper, date | None]]:
    """(paper, date first submitted) for every entry."""
    d = feedparser.parse(text)
    if d.bozo and not d.entries and not d.get("version"):
        raise HttpError("export.arxiv.org: malformed response")
    out = []
    for e in d.entries:
        if "api/errors" in e.get("id", ""):
            raise NotFound(f"export.arxiv.org: {_clean(e.get('summary')) or 'error'}")
        published = e.get("published", "")[:10]
        out.append((entry_to_paper(e), date.fromisoformat(published) if len(published) == 10 else None))
    return out


def parse_feed(text: str) -> list[Paper]:
    return [p for p, _ in parse_dated(text)]


def lookup(client: Client, arxiv_id: str) -> Paper:
    aid = norm_id("arxiv", arxiv_id) or arxiv_id
    papers = parse_feed(client.get_text(API, {"id_list": aid, "max_results": 1}, what=f"arXiv:{aid}"))
    if not papers:
        raise NotFound(f"export.arxiv.org: no paper arXiv:{aid}")
    return papers[0]


def query(client: Client, search_query: str, *, start: int = 0,
          max_results: int = 100) -> list[tuple[Paper, date | None]]:
    """Newest submissions first, with their submission dates."""
    params = {"search_query": search_query, "start": start, "max_results": max_results,
              "sortBy": "submittedDate", "sortOrder": "descending"}
    return parse_dated(client.get_text(API, params, what=search_query))
