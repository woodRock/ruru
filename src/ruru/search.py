"""Full-text search over front matter and notes: every term must match somewhere. Also the
project filter and the sort orders that `ruru list` and the TUI's library share.

A linear scan (fast enough for thousands of papers); case- and accent-insensitive. Scores weight
title, authors, tags and key over venue, abstract and notes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from ruru.model import STATUSES, Paper
from ruru.text import fold

NO_PROJECT = "none"  # `-p none`, `p:none`: papers in no project (so no project may be called that)

# name -> (sort key, newest/largest first?)
_SORTS: dict[str, tuple[Callable[[Paper], Any], bool]] = {
    "added": (lambda p: (p.added or date.min, p.key), True),
    "key": (lambda p: p.key.lower(), False),
    "year": (lambda p: (p.year or 0, p.added or date.min, p.key), True),
    "status": (lambda p: (STATUSES.index(p.status) if p.status in STATUSES else len(STATUSES), p.key), False),
    "title": (lambda p: (fold(p.title), p.key), False),
}
SORTS = tuple(_SORTS)  # the first is the default


def in_project(p: Paper, name: str) -> bool:
    return not p.projects if name == NO_PROJECT else name in p.projects


def sort_papers(papers: Iterable[Paper], by: str = "added", *, reverse: bool = False) -> list[Paper]:
    """added: newest first; year: newest first; key, title: A-Z; status: to-read, later, reading, read."""
    key, descending = _SORTS[by]
    return sorted(papers, key=key, reverse=descending != reverse)

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
