"""Crossref: authoritative DOI metadata."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote

from ruru.http import Client
from ruru.model import Paper, norm_id

API = "https://api.crossref.org/works"

TYPES = {
    "journal-article": "article", "proceedings-article": "inproceedings", "posted-content": "preprint",
    "book": "book", "monograph": "book", "edited-book": "book", "reference-book": "book",
    "dissertation": "thesis",
}


def strip_markup(s: str | None) -> str | None:
    """JATS/HTML → plain text: '<jats:p>Fish <i>oil</i></jats:p>' → 'Fish oil'."""
    if not s:
        return None
    s = re.sub(r"<jats:title>.*?</jats:title>", " ", s, flags=re.S)  # 'Abstract' headings
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return " ".join(s.split()) or None


def _first(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    return strip_markup(value) if value else None


def _year(m: dict[str, Any]) -> int | None:
    for key in ("issued", "published-print", "published-online", "published", "created"):
        parts = (m.get(key) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def _author(a: dict[str, Any]) -> str:
    return " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")


def to_paper(m: dict[str, Any]) -> Paper:
    paper = Paper(
        title=_first(m.get("title")) or "",
        authors=[name for name in (_author(a) for a in m.get("author", [])) if name],
        year=_year(m),
        venue=_first(m.get("container-title")),
        type=TYPES.get(m.get("type", ""), "misc"),
        volume=m.get("volume"), issue=m.get("issue"), pages=m.get("page"),
        publisher=m.get("publisher"),
        url=m.get("URL"),
        abstract=strip_markup(m.get("abstract")),
    )
    paper.ids["doi"] = norm_id("doi", m.get("DOI"))
    return paper


def lookup(client: Client, doi: str) -> Paper:
    data = client.get_json(f"{API}/{quote(doi, safe='/()')}", what=f"doi:{doi}")
    return to_paper(data["message"])
