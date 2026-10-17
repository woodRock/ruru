"""OpenAlex: works by DOI or id, title search, open-access PDFs.

arXiv's DataCite DOIs (10.48550/arXiv.<id>) become `ids.arxiv`, never `ids.doi`, so `ids.doi`
always means a published version.
"""

from __future__ import annotations

import re
from typing import Any

from ruru.http import Client
from ruru.model import Paper, norm_id

API = "https://api.openalex.org/works"
SELECT = ("id,doi,title,publication_year,type,authorships,primary_location,best_oa_location,biblio,ids,"
          "abstract_inverted_index")
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
    # Commas, colons and pipes are filter syntax; they carry no meaning in a title search.
    clean = " ".join(re.sub(r"[,:|]", " ", title).split())
    flt = f"title.search:{clean}" + (f",type:{kind}" if kind else "")
    data = client.get_json(API, {"filter": flt, "per-page": per_page, "select": SELECT}, what=f"title {title!r}")
    return [to_paper(w) for w in data.get("results", [])]
