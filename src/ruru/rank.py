"""Ranking by similarity to the library: TF-IDF over titles, abstracts and tags.

No model and no network. A paper or inbox item is a bag of stemmed words (title words count three
times, tags twice). Words are weighted by how rare they are across everything ruru has text for
(inverse document frequency), a document is the vector of those weights scaled to length 1, and the
similarity of two documents is the cosine of the angle between them.

An inbox item's relevance is how close it sits to its nearest library papers: the closest counts
for half, the next for 0.3, the third for 0.2, so a niche interest represented by a single paper is
not diluted by the rest of the library. Items that resemble titles you skipped are pushed down.

Everything here is pure: callers pass word counts in (the index caches them) and get numbers out.
"""

from __future__ import annotations

import heapq
import math
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from ruru.text import words

VERSION = 1  # bump when tokenisation changes: the index then recounts every document

MIN_LIBRARY = 5  # below this many papers with text there is nothing to compare an item with
TOP_K = 3
POSITIVE = (0.5, 0.3, 0.2)  # weights of the nearest, second and third nearest library paper
NEGATIVE = 0.5  # how much a resemblance to skipped titles counts against an item
MAX_DF_RATIO = 0.5  # words in more than half of a big corpus tell little: not looked up
BIG_CORPUS = 30
TITLE_WEIGHT = 3
TAG_WEIGHT = 2

_STOP_TEXT = """
a about above after again against all also am an and any are as at be because been before being below
between both but by can cannot could did do does doing down during each either few for from further
had has have having he her here hers him his how however i if in into is it its itself just may me
might more most much must my no nor not now of off on once only or other our ours out over own same
she should since so some such than that the their theirs them then there these they this those though
through thus to too under until up upon us use used uses using very via was we were what when where
whether which while who whom whose why will with within without would yet you your
paper papers propose proposed proposes present presents presented show shows shown showed result
results method methods approach approaches novel new study studies work based well one two three
et al demonstrate demonstrates demonstrated address addresses article research
"""
STOPWORDS = frozenset(_STOP_TEXT.split())

_SUFFIXES = ("ches", "shes", "xes", "zes", "sses")


def stem(word: str) -> str:
    """A conservative plural and spelling normaliser: alignments → alignment, optimisation → optimization."""
    word = word.replace("isation", "ization")
    n = len(word)
    if n <= 3:
        return word
    if word.endswith("ies") and n > 4:
        return word[:-3] + "y"
    if word.endswith(_SUFFIXES):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


_NUMBER = re.compile(r"^\d+$")


def tokens(text: str) -> list[str]:
    """Stemmed words of `text` without stopwords, single letters and bare numbers."""
    out = []
    for w in words(text):
        if len(w) < 2 or _NUMBER.match(w) or w in STOPWORDS:
            continue
        out.append(stem(w))
    return out


def counts(title: str, abstract: str | None = None, tags: Iterable[str] = (), venue: str | None = None
           ) -> dict[str, int]:
    """Word counts of a document: the title counts three times, tags twice."""
    c: Counter[str] = Counter()
    for t in tokens(title):
        c[t] += TITLE_WEIGHT
    for t in tokens(abstract or ""):
        c[t] += 1
    for tag in tags:
        for t in tokens(tag):
            c[t] += TAG_WEIGHT
    for t in tokens(venue or ""):
        c[t] += 1
    return dict(c)


def title_counts(title: str) -> dict[str, int]:
    return dict(Counter(tokens(title)))


# --- vectors -------------------------------------------------------------------------------------

class Idf:
    """Inverse document frequencies over a set of documents (smoothed, so an unseen word is the rarest)."""

    def __init__(self, docs: Iterable[Mapping[str, int]]) -> None:
        self.df: Counter[str] = Counter()
        self.n = 0
        for doc in docs:
            self.n += 1
            self.df.update(doc.keys())

    def weight(self, term: str) -> float:
        return math.log((1 + self.n) / (1 + self.df.get(term, 0))) + 1.0


Vector = dict[str, float]


def vectorise(doc: Mapping[str, int], idf: Idf) -> Vector:
    """Sub-linear term frequency times idf, scaled to length 1 (empty for a document with no words)."""
    w = {t: (1.0 + math.log(c)) * idf.weight(t) for t, c in doc.items() if c > 0}
    norm = math.sqrt(sum(v * v for v in w.values()))
    return {t: v / norm for t, v in w.items()} if norm else {}


def cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    if len(b) < len(a):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


class Corpus:
    """Documents as vectors, with an inverted index for finding the nearest ones to a query."""

    def __init__(self, docs: Mapping[str, Mapping[str, int]], idf: Idf) -> None:
        self.idf = idf
        self.vectors: dict[str, Vector] = {}
        self.postings: dict[str, list[tuple[str, float]]] = {}
        for doc_id, doc in docs.items():
            vec = vectorise(doc, idf)
            if not vec:
                continue
            self.vectors[doc_id] = vec
            for term, weight in vec.items():
                self.postings.setdefault(term, []).append((doc_id, weight))

    def __len__(self) -> int:
        return len(self.vectors)

    def nearest(self, query: Mapping[str, float], k: int = TOP_K, *,
                exclude: Collection[str] = ()) -> list[tuple[str, float]]:
        """The `k` documents closest to `query` (a unit vector), as (id, cosine), best first."""
        n = len(self.vectors)
        scores: dict[str, float] = {}
        for term, q in query.items():
            posting = self.postings.get(term)
            if not posting or (n >= BIG_CORPUS and len(posting) > MAX_DF_RATIO * n):
                continue
            for doc_id, weight in posting:
                scores[doc_id] = scores.get(doc_id, 0.0) + q * weight
        for doc_id in exclude:
            scores.pop(doc_id, None)
        best = heapq.nsmallest(k, scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(doc_id, min(1.0, s)) for doc_id, s in best]


# --- relevance of inbox items --------------------------------------------------------------------

@dataclass(frozen=True)
class Relevance:
    score: float  # 0 … 1; higher is closer to what the library holds
    similar: tuple[tuple[str, float], ...]  # nearest library papers: (key, similarity), best first
    penalty: float = 0.0  # what resemblance to skipped titles took off

    def to_json(self) -> dict[str, object]:
        return {"relevance": round(self.score, 3),
                "similar_to": [{"key": k, "similarity": round(s, 3)} for k, s in self.similar]}


def _weighted(sims: Sequence[float]) -> float:
    return sum(w * s for w, s in zip(POSITIVE, sims))


def relevance(library: Mapping[str, Mapping[str, int]], items: Mapping[str, Mapping[str, int]],
              item_titles: Mapping[str, Mapping[str, int]], skipped: Sequence[Mapping[str, int]]
              ) -> dict[str, Relevance] | None:
    """Relevance of each item to the library (key -> word counts), less a penalty for resembling a
    skipped title. None when the library is too small to compare with."""
    usable = {k: c for k, c in library.items() if c}
    if len(usable) < MIN_LIBRARY:
        return None
    idf = Idf([*usable.values(), *items.values()])
    papers = Corpus(usable, idf)
    skips = Corpus({str(i): c for i, c in enumerate(skipped)}, idf) if skipped else None
    out: dict[str, Relevance] = {}
    for item_id, doc in items.items():
        vec = vectorise(doc, idf)
        near = papers.nearest(vec)
        positive = _weighted([s for _, s in near])
        penalty = 0.0
        if skips is not None:
            title_vec = vectorise(item_titles.get(item_id) or {}, idf)
            penalty = NEGATIVE * _weighted([s for _, s in skips.nearest(title_vec)])
        out[item_id] = Relevance(max(0.0, positive - penalty), tuple(near), penalty)
    return out
