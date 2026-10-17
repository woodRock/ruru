"""OpenAlex: works by DOI or id, title search, open-access PDFs.

arXiv's DataCite DOIs (10.48550/arXiv.<id>) become `ids.arxiv`, never `ids.doi`, so `ids.doi`
always means a published version.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ruru.http import Client
from ruru.model import Paper, norm_id

API = "https://api.openalex.org/works"
SELECT = ("id,doi,title,publication_year,publication_date,type,authorships,primary_location,best_oa_location,"
          "biblio,ids,abstract_inverted_index")
ARXIV_DOI = "10.48550/arxiv."

TYPES = {
    "article": "article", "review": "article", "letter": "article", "editorial": "article",
    "conference-paper": "inproceedings", "preprint": "preprint", "book": "book",
    "dissertation": "thesis",
}


def abstract(inverted: dict[str, list[int]] | None) -> str | None:
    if not inverted:
        return None
    positions = {i: word for word, idx in inverted.items() for i in idx}
    return " ".join(positions[i] for i in sorted(positions)) or None


def _pages(biblio: dict[str, Any]) -> str | None:
    first, last = biblio.get("first_page"), biblio.get("last_page")
    if first and last and first != last:
        return f"{first}-{last}"
    return first or None


def to_paper(w: dict[str, Any]) -> Paper:
    location = w.get("primary_location") or {}
    source = location.get("source") or {}
    ptype = TYPES.get(w.get("type") or "", "misc")
    doi = norm_id("doi", w.get("doi"))
    arxiv = None
    if doi and doi.startswith(ARXIV_DOI):
        arxiv, doi = norm_id("arxiv", doi[len(ARXIV_DOI):]), None
        ptype = "preprint"
    venue = source.get("display_name") if source.get("type") != "repository" else None
    biblio = w.get("biblio") or {}
    paper = Paper(
        title=" ".join((w.get("title") or "").split()),
        authors=[a["author"]["display_name"] for a in w.get("authorships", [])
                 if (a.get("author") or {}).get("display_name")],
        year=w.get("publication_year"),
        venue=venue,
        type=ptype,
        volume=biblio.get("volume"), issue=biblio.get("issue"), pages=_pages(biblio),
        url=w.get("doi") if doi else location.get("landing_page_url"),
        pdf_url=(w.get("best_oa_location") or {}).get("pdf_url"),
        abstract=abstract(w.get("abstract_inverted_index")),
    )
    paper.ids["doi"] = doi
    paper.ids["arxiv"] = arxiv
    paper.ids["openalex"] = norm_id("openalex", w.get("id"))
    return paper


def work(client: Client, ident: str) -> Paper:
    """`ident`: 'W123', 'https://openalex.org/W123' or 'doi:10.…'."""
    if not ident.startswith("doi:"):
        ident = norm_id("openalex", ident) or ident
    return to_paper(client.get_json(f"{API}/{ident}", {"select": SELECT}, what=ident))


def search_title(client: Client, title: str, *, per_page: int = 10, kind: str | None = None) -> list[Paper]:
    flt = f"title.search:{_filter_value(title)}" + (f",type:{kind}" if kind else "")
    data = client.get_json(API, {"filter": flt, "per-page": per_page, "select": SELECT}, what=f"title {title!r}")
    return [to_paper(w) for w in data.get("results", [])]


def _filter_value(text: str) -> str:
    # Commas, colons and pipes are filter syntax; they carry no meaning in a search phrase.
    return " ".join(re.sub(r"[,:|]", " ", text).split())


def search(client: Client, query: str, *, per_page: int = 10) -> list[Paper]:
    """Relevance-ranked search over titles, abstracts and full text (`ruru find`)."""
    params = {"search": query, "per-page": per_page, "select": SELECT}
    data = client.get_json(API, params, what=f"search {query!r}")
    return [to_paper(w) for w in data.get("results", [])]


def _published(w: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(w.get("publication_date") or "")
    except ValueError:
        return None


def works_feed(client: Client, *, since: date, filter: str | None = None, search: str | None = None,
               limit: int = 200) -> list[tuple[Paper, date | None]]:
    """Works published on or after `since`, newest first. `search` is a phrase matched in titles
    and abstracts (OpenAlex's loose full-text `search=` drowns a feed in noise)."""
    parts = [filter] if filter else []
    if search:
        phrase = search if '"' in search else f'"{_filter_value(search)}"'
        parts.append(f"title_and_abstract.search:{phrase}")
    parts.append(f"from_publication_date:{since.isoformat()}")
    params: dict[str, Any] = {"filter": ",".join(parts), "sort": "publication_date:desc",
                              "per-page": min(limit, 100), "select": SELECT, "cursor": "*"}
    out: list[tuple[Paper, date | None]] = []
    while len(out) < limit:
        data = client.get_json(API, params, what=params["filter"])
        results = data.get("results", [])
        out += [(to_paper(w), _published(w)) for w in results]
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not results or not cursor:
            break
        params["cursor"] = cursor
    return out[:limit]


def search_sources(client: Client, name: str, *, per_page: int = 8) -> list[dict[str, Any]]:
    """Journals and other sources by name: id, display_name, issn_l, works_count, publisher."""
    params = {"search": name, "per-page": per_page,
              "select": "id,display_name,issn_l,issn,works_count,host_organization_name,type"}
    return list(client.get_json("https://api.openalex.org/sources", params, what=f"source {name!r}")["results"])


def search_authors(client: Client, name: str, *, per_page: int = 8) -> list[dict[str, Any]]:
    """Authors by name, with institutions and topics to tell namesakes apart."""
    params = {"search": name, "per-page": per_page,
              "select": "id,display_name,works_count,last_known_institutions,topics"}
    return list(client.get_json("https://api.openalex.org/authors", params, what=f"author {name!r}")["results"])
