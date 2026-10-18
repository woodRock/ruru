"""Turning an identifier, URL or title into metadata, and refreshing a paper's metadata.

Sources: Crossref (authoritative for DOIs), OpenAlex (OpenAlex ids, abstracts, open-access PDFs,
title search) and arXiv. A title is accepted only when exactly one work has that exact
(normalised) title, within a year of `year` if given: ruru never guesses.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, replace

from ruru.errors import UsageError
from ruru.http import Client, HttpError, NotFound
from ruru.model import ID_KINDS, Paper, norm_id
from ruru.resolve import arxiv, crossref, openalex
from ruru.text import norm_title

# Fields `refresh` may update; everything else (key, status, tags, notes, …) is Jesse's.
METADATA = ("title", "authors", "year", "venue", "type", "volume", "issue", "pages", "publisher",
            "url", "pdf_url", "abstract")
# Fields `enrich` may fill when empty: not the title or type, which identify the work as Jesse has it.
FILLABLE = tuple(f for f in METADATA if f not in ("title", "type"))

_DOI = re.compile(r"(10\.\d{4,9}/[^\s?#]+)", re.I)
_ARXIV_NEW = r"\d{4}\.\d{4,5}(?:v\d+)?"
_ARXIV_OLD = r"[a-z][a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?"
_ARXIV = re.compile(rf"^(?:arxiv:)?({_ARXIV_NEW}|{_ARXIV_OLD})$", re.I)
_ARXIV_URL = re.compile(rf"arxiv\.org/(?:abs|pdf)/({_ARXIV_NEW}|{_ARXIV_OLD})", re.I)
_OPENALEX = re.compile(r"^(?:https?://openalex\.org/)?(W\d+)$", re.I)


class AmbiguousTitle(UsageError):
    pass


@dataclass(frozen=True)
class Ident:
    kind: str  # doi | arxiv | openalex | title
    value: str


def parse_identifier(text: str) -> Ident:
    t = text.strip()
    if m := _OPENALEX.match(t):
        return Ident("openalex", m.group(1).upper())
    if m := (_ARXIV.match(t) or _ARXIV_URL.search(t)):
        return Ident("arxiv", norm_id("arxiv", m.group(1)) or m.group(1))
    if m := _DOI.search(t):
        doi = re.sub(r"(\.pdf|[.,;)\]])$", "", m.group(1)).lower()
        if doi.startswith(openalex.ARXIV_DOI):
            return Ident("arxiv", norm_id("arxiv", doi[len(openalex.ARXIV_DOI):]) or doi)
        return Ident("doi", doi)
    if re.match(r"^https?://", t, re.I):
        raise UsageError(f"no DOI or arXiv id in {t}; give the DOI instead")
    if not t:
        raise UsageError("empty identifier")
    return Ident("title", t)


def merge(primary: Paper, secondary: Paper) -> Paper:
    """`primary` with its empty fields and ids filled from `secondary`."""
    out = replace(primary, ids=dict(primary.ids))
    for name in METADATA:
        if not getattr(out, name) and getattr(secondary, name):
            setattr(out, name, getattr(secondary, name))
    if out.type == "misc" and secondary.type != "misc":
        out.type = secondary.type
    for kind in ID_KINDS:
        out.ids[kind] = out.ids.get(kind) or secondary.ids.get(kind)
    return out


def _shared_id(a: Paper, b: Paper) -> bool:
    return any(a.ids.get(k) and a.ids.get(k) == b.ids.get(k) for k in ID_KINDS)


def _describe(p: Paper) -> str:
    where = p.venue or (f"arXiv:{p.ids['arxiv']}" if p.ids.get("arxiv") else p.type)
    ident = f"doi:{p.ids['doi']}" if p.ids.get("doi") else (p.ids.get("openalex") or "")
    return f"{p.year or '----'}  {p.title}  ({where})  {ident}".rstrip()


class Resolver:
    def __init__(self, client: Client) -> None:
        self.client = client

    def resolve(self, text: str, *, year: int | None = None) -> Paper:
        ident = parse_identifier(text)
        if ident.kind == "doi":
            return self.by_doi(ident.value)
        if ident.kind == "arxiv":
            return self.by_arxiv(ident.value)
        if ident.kind == "openalex":
            return self.by_openalex(ident.value)
        return self.by_title(ident.value, year=year)

    def _openalex_quietly(self, ident: str) -> Paper | None:
        """OpenAlex as a secondary source: a miss or an outage costs only the extras."""
        try:
            return openalex.work(self.client, ident)
        except NotFound:
            return None
        except HttpError as e:
            print(f"ruru: warning: {e} (continuing without OpenAlex)", file=sys.stderr)
            return None

    def by_doi(self, doi: str) -> Paper:
        try:
            paper = crossref.lookup(self.client, doi)
        except NotFound:
            # Not a Crossref DOI (DataCite: Zenodo, figshare, …): OpenAlex may still know it.
            return openalex.work(self.client, f"doi:{doi}")
        extra = self._openalex_quietly(f"doi:{doi}")
        return merge(paper, extra) if extra else paper

    def by_arxiv(self, arxiv_id: str) -> Paper:
        pre = arxiv.lookup(self.client, arxiv_id)
        if pre.ids.get("doi"):
            published = self.by_doi(pre.ids["doi"] or "")
            return merge(published, pre)  # published metadata, arXiv id and abstract kept
        return pre

    def by_openalex(self, work_id: str) -> Paper:
        paper = openalex.work(self.client, work_id)
        if paper.ids.get("doi"):
            try:
                return merge(crossref.lookup(self.client, paper.ids["doi"] or ""), paper)
            except NotFound:
                pass
        return paper

    def title_matches(self, title: str, *, year: int | None = None, kind: str | None = None) -> list[Paper]:
        want = norm_title(title)
        return [
            p for p in openalex.search_title(self.client, title, kind=kind)
            if norm_title(p.title) == want and (year is None or p.year is None or abs(p.year - year) <= 1)
        ]

    def by_title(self, title: str, *, year: int | None = None) -> Paper:
        matches = self.title_matches(title, year=year)
        if not matches:
            raise NotFound(f"no work titled {title!r}" + (f" near {year}" if year else "")
                           + "; check the title, or give a DOI or arXiv id")
        # Collapse only records that share an id. The library's "same title within a year" rule
        # would merge two different works that happen to share a title, which is a guess.
        distinct = [m for i, m in enumerate(matches) if not any(_shared_id(m, n) for n in matches[:i])]
        if len(distinct) > 1:
            listing = "\n".join(f"  {_describe(m)}" for m in distinct)
            raise AmbiguousTitle(f"{len(distinct)} works are titled {title!r}; give a DOI, arXiv id or "
                                 f"--year:\n{listing}")
        found = distinct[0]
        if found.ids.get("doi"):
            try:
                return merge(crossref.lookup(self.client, found.ids["doi"] or ""), found)
            except NotFound:
                pass
        return found

    def lookup(self, paper: Paper) -> Paper:
        """The upstream record for `paper`: by its best id, else by its exact title."""
        ids = paper.ids
        if ids.get("doi"):
            return self.by_doi(ids["doi"] or "")
        if ids.get("arxiv"):
            return self.by_arxiv(ids["arxiv"] or "")
        if ids.get("openalex"):
            return self.by_openalex(ids["openalex"] or "")
        return self.by_title(paper.title, year=paper.year)

    def refresh(self, paper: Paper) -> Paper:
        """Re-resolve `paper` (preprint → published where possible), keeping everything that is Jesse's."""
        fresh = self.lookup(paper)
        if fresh.type == "preprint" and not fresh.ids.get("doi"):
            # Published under the same title, without the authors telling arXiv?
            published = [m for m in self.title_matches(fresh.title, year=fresh.year, kind="article")
                         if m.ids.get("doi")]
            if len(published) == 1:
                fresh = merge(self.by_doi(published[0].ids["doi"] or ""), fresh)
        return apply_metadata(paper, fresh)


def apply_metadata(paper: Paper, fresh: Paper) -> Paper:
    """`paper` with metadata from `fresh`; empty upstream values never erase what is there."""
    out = replace(paper, ids=dict(paper.ids))
    for name in METADATA:
        value = getattr(fresh, name)
        if value:
            setattr(out, name, value)
    for kind, value in fresh.ids.items():
        if value:
            out.ids[kind] = value
    return out


def fill_gaps(paper: Paper, found: Paper) -> Paper:
    """`paper` with only its empty `FILLABLE` fields and missing ids taken from `found` (`ruru enrich`).
    Unlike `apply_metadata`, nothing already there changes, so an imported entry keeps its title,
    authors and type exactly as its .bib had them."""
    out = replace(paper, ids=dict(paper.ids))
    for name in FILLABLE:
        if not getattr(out, name) and getattr(found, name):
            setattr(out, name, getattr(found, name))
    for kind, value in found.ids.items():
        if value and not out.ids.get(kind):
            out.ids[kind] = value
    return out


def changes(old: Paper, new: Paper) -> dict[str, tuple[object, object]]:
    fields = (*METADATA, "ids")
    return {f: (getattr(old, f), getattr(new, f)) for f in fields if getattr(old, f) != getattr(new, f)}


__all__ = ["AmbiguousTitle", "Ident", "Resolver", "apply_metadata", "changes", "fill_gaps", "merge",
           "parse_identifier"]
