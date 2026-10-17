"""`ruru export bib`: one BibTeX file for a selection of papers, sorted by key."""

from __future__ import annotations

from collections.abc import Iterable

from ruru.bibtex import to_bibtex
from ruru.model import Paper


def export_bib(papers: Iterable[Paper], encoding: str = "ascii") -> str:
    return "\n".join(to_bibtex(p, encoding) for p in sorted(papers, key=lambda p: p.key))
