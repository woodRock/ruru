"""Near-duplicates: the same work under different identifiers, which the `same_paper` rule misses.

A preprint and its published version often differ in title and carry no shared id. Two records are
flagged when their title-and-abstract vectors are close, they share a first author or two authors,
and their years are at most two apart. "Close" is a cosine of at least `SIMILARITY`, or the lower
`SAME_AUTHORS_SIMILARITY` when both have the very same two or more authors (a retitled paper keeps its
authors and its abstract). Authors are blocked by family name first, so this stays fast on a large library.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ruru import rank
from ruru.model import Paper, same_paper
from ruru.text import fold, split_name

SIMILARITY = 0.7
SAME_AUTHORS_SIMILARITY = 0.5
MAX_YEAR_GAP = 2


@dataclass(frozen=True)
class Entry:
    id: str  # a library key or an inbox id
    paper: Paper
    counts: Mapping[str, int]
    in_library: bool


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    similarity: float
    same: bool  # `same_paper` already says so (a shared id, or the same title)

    def to_json(self) -> dict[str, object]:
        return {"a": self.a, "b": self.b, "similarity": round(self.similarity, 3), "same": self.same}


def families(authors: Sequence[str]) -> list[str]:
    return [fold(split_name(a)[1]) for a in authors if split_name(a)[1]]


def _related_authors(x: Sequence[str], y: Sequence[str]) -> bool:
    if not x or not y:
        return False
    shared = set(x) & set(y)
    return x[0] == y[0] or len(shared) >= 2


def _threshold(x: Sequence[str], y: Sequence[str], default: float) -> float:
    return SAME_AUTHORS_SIMILARITY if len(x) >= 2 and set(x) == set(y) else min(default, SIMILARITY)


def _close_years(a: Paper, b: Paper) -> bool:
    return a.year is None or b.year is None or abs(a.year - b.year) <= MAX_YEAR_GAP


def find(entries: Sequence[Entry], threshold: float = SIMILARITY) -> list[Pair]:
    """Pairs of near-duplicate entries, most similar first."""
    idf = rank.Idf([e.counts for e in entries])
    vectors = [rank.vectorise(e.counts, idf) for e in entries]
    fams = [families(e.paper.authors) for e in entries]
    blocks: dict[str, list[int]] = {}
    for i, names in enumerate(fams):
        for name in set(names):
            blocks.setdefault(name, []).append(i)
    seen: set[tuple[int, int]] = set()
    pairs = []
    for members in blocks.values():
        for x, i in enumerate(members):
            for j in members[x + 1:]:
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                a, b = entries[i], entries[j]
                if not _related_authors(fams[i], fams[j]) or not _close_years(a.paper, b.paper):
                    continue
                sim = rank.cosine(vectors[i], vectors[j])
                same = same_paper(a.paper, b.paper)
                if sim >= _threshold(fams[i], fams[j], threshold) or same:
                    first, second = sorted((a, b), key=lambda e: (not e.in_library, e.id))
                    pairs.append(Pair(first.id, second.id, min(1.0, sim), same))
    pairs.sort(key=lambda p: (-p.similarity, p.a, p.b))
    return pairs


def duplicates_of(pairs: Sequence[Pair]) -> dict[str, str]:
    """id -> the id it most resembles, for the later id of each pair (a library key comes first, so an
    inbox item is flagged against the library paper, and the second of two items against the first)."""
    out: dict[str, str] = {}
    for p in pairs:  # most similar first
        out.setdefault(p.b, p.a)
    return out
