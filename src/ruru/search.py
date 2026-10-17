"""Full-text search over front matter and notes: every term must match somewhere.

A linear scan (fast enough for thousands of papers); case- and accent-insensitive. Scores weight
title, authors, tags and key over venue, abstract and notes.
"""

from __future__ import annotations

from collections.abc import Iterable

from ruru.model import Paper
from ruru.text import fold

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
