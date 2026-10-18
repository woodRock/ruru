"""Full-text search over front matter and notes: every term must match somewhere. Also the
project filter and the sort orders that `ruru list` and the TUI's library share.

A linear scan (fast enough for thousands of papers); case- and accent-insensitive. Scores weight
title, authors, tags and key over venue, abstract and notes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Any

from ruru import gitops
from ruru.model import STATUSES, Paper
from ruru.text import fold

NO_PROJECT = "none"  # `-p none`, `p:none`: papers in no project (so no project may be called that)

Filed = Callable[[Paper], float]

# name -> (sort key given `filed`, newest/largest first?)
_SORTS: dict[str, tuple[Callable[[Paper, Filed], Any], bool]] = {
    "added": (lambda p, filed: (p.added or date.min, filed(p), p.key), True),
    "key": (lambda p, filed: p.key.lower(), False),
    "year": (lambda p, filed: (p.year or 0, p.added or date.min, filed(p), p.key), True),
    "status": (lambda p, filed: (STATUSES.index(p.status) if p.status in STATUSES else len(STATUSES), p.key),
               False),
    "title": (lambda p, filed: (fold(p.title), p.key), False),
}
SORTS = tuple(_SORTS)  # the first is the default


def in_project(p: Paper, name: str) -> bool:
    return not p.projects if name == NO_PROJECT else name in p.projects


def filed_times(root: Path) -> Filed:
    """When each paper was filed: the commit time of its paper.md (one git call). `added` is only a
    date, so this orders papers added on the same day; an uncommitted paper counts as newest."""
    added = gitops.added_times(root, "papers")
    return lambda p: float(added.get(f"papers/{p.key}/paper.md", float("inf")))


def sort_papers(papers: Iterable[Paper], by: str = "added", *, reverse: bool = False,
                filed: Filed = lambda p: 0.0) -> list[Paper]:
    """added: newest first (`filed`, e.g. `filed_times`, orders a day); year: newest first; key, title:
    A-Z; status: to-read, later, reading, read."""
    key, descending = _SORTS[by]
    return sorted(papers, key=lambda p: key(p, filed), reverse=descending != reverse)


def needs_filed(by: str) -> bool:
    return by in ("added", "year")

WEIGHTS = {"title": 5, "authors": 3, "tags": 3, "key": 3, "venue": 1, "abstract": 1, "notes": 1}


def _fields(p: Paper) -> dict[str, str]:
    return {
        "title": fold(p.title), "authors": fold(" ".join(p.authors)), "tags": fold(" ".join(p.tags)),
        "key": p.key, "venue": fold(p.venue or ""), "abstract": fold(p.abstract or ""), "notes": fold(p.body),
    }


def search(papers: Iterable[Paper], query: str) -> list[tuple[int, Paper]]:
    terms = fold(query).split()
    if not terms:
        return []
    hits = []
    for p in papers:
        fields = _fields(p)
        if all(any(t in text for text in fields.values()) for t in terms):
            score = sum(WEIGHTS[name] * text.count(t) for t in terms for name, text in fields.items())
            hits.append((score, p))
    hits.sort(key=lambda h: (-h[0], h[1].key))
    return hits
