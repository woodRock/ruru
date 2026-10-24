"""What the library knows about relevance: ranking the inbox, related papers and near-duplicates.

Word counts come from the index (recounted only for files that changed); the maths is in
`ruru.rank` and `ruru.dupes`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from ruru import dupes, rank
from ruru.errors import UnknownKey
from ruru.inbox import Inbox, Item, UnknownItem, read_seen
from ruru.index import Doc, PdfSync
from ruru.library import Library
from ruru.model import Paper
from ruru.ops import fulltext

Scope = Literal["library", "inbox", "all"]


def paper_counts(p: Paper) -> dict[str, int]:
    return rank.counts(p.title, p.abstract, p.tags, p.venue)


def library_counts(lib: Library, papers: Sequence[Paper]) -> dict[str, dict[str, int]]:
    docs: list[Doc] = [(p.key, lib.paper_dir(p.key) / "paper.md", lambda p=p: paper_counts(p))  # type: ignore[misc]
                       for p in papers]
    return lib.index.counts("paper", docs)


def inbox_counts(lib: Library, items: Sequence[Item]) -> dict[str, dict[str, int]]:
    docs: list[Doc] = [(i.id, i.path, lambda i=i: paper_counts(i.paper))  # type: ignore[misc]
                       for i in items if i.path is not None]
    return lib.index.counts("inbox", docs)


def skipped_titles(lib: Library) -> list[dict[str, int]]:
    """Word counts of the titles that were skipped (once each): what the inbox should look less like."""
    seen: set[str] = set()
    out = []
    for r in read_seen(lib.root):
        title = str(r.get("title") or "")
        if r.get("decision") == "skip" and title and title.casefold() not in seen:
            seen.add(title.casefold())
            out.append(rank.title_counts(title))
    return out


def relevance(lib: Library, items: Sequence[Item], papers: Sequence[Paper] | None = None
              ) -> dict[str, rank.Relevance] | None:
    """Each item's relevance to the library, or None while the library has too few papers to compare with."""
    if not items:
        return {}
    papers = list(lib.papers()) if papers is None else papers
    if len(papers) < rank.MIN_LIBRARY:
        return None
    library = library_counts(lib, papers)
    return rank.relevance(
        library, inbox_counts(lib, items), {i.id: rank.title_counts(i.paper.title) for i in items},
        skipped_titles(lib))


def order_inbox(items: list[Item], scores: dict[str, rank.Relevance] | None, by: str) -> list[Item]:
    """Items by relevance (ties and the unranked keep the date order they came in), or as they came."""
    if by == "date" or scores is None:
        return items
    return sorted(items, key=lambda i: -scores[i.id].score if i.id in scores else 0.0)


def ranked_inbox(lib: Library, sort: str = "relevance", *, wanted: bool = True
                 ) -> tuple[list[Item], dict[str, rank.Relevance] | None, dict[str, str]]:
    """Every inbox item in `sort` order ("relevance" or "date"), their relevance scores (None while the
    library is too small to rank against) and the items that look like something already here.
    `wanted=False` skips the scoring and the duplicate search when only the order matters."""
    every = Inbox(lib.root).items()
    papers = list(lib.papers()) if (wanted or sort == "relevance") and every else []
    scores = relevance(lib, every, papers) if (wanted or sort == "relevance") else None
    dup = dupes.duplicates_of(duplicate_pairs(lib, papers=papers, items=every)) if wanted and every else {}
    return order_inbox(every, scores, sort), scores, {i.id: dup[i.id] for i in every if i.id in dup}


@dataclass(frozen=True)
class Related:
    id: str
    kind: Literal["library", "inbox"]
    paper: Paper
    similarity: float


def related(lib: Library, ident: str, *, n: int = 10, scope: Scope = "library") -> tuple[Paper, list[Related]]:
    """The papers (and, with `scope`, inbox items) closest to a library paper or an inbox item."""
    inbox = Inbox(lib.root)
    if lib.exists(ident):
        origin, own = lib.get(ident), ident
    else:
        try:
            origin, own = inbox.get(ident).paper, f"inbox:{ident}"
        except UnknownItem:
            raise UnknownKey(ident) from None
    papers = list(lib.papers()) if scope in ("library", "all") else []
    items = inbox.items() if scope in ("inbox", "all") else []
    counts = library_counts(lib, papers)
    counts |= {f"inbox:{k}": c for k, c in inbox_counts(lib, items).items()}
    mine = paper_counts(origin)
    idf = rank.Idf([*counts.values(), mine])
    corpus = rank.Corpus(counts, idf)
    found: dict[str, tuple[Paper, Literal["library", "inbox"]]] = {p.key: (p, "library") for p in papers}
    found |= {f"inbox:{i.id}": (i.paper, "inbox") for i in items}
    out = []
    for doc_id, sim in corpus.nearest(rank.vectorise(mine, idf), n, exclude={own}):
        paper, kind = found[doc_id]
        out.append(Related(doc_id.removeprefix("inbox:"), kind, paper, sim))
    return origin, out


def duplicate_pairs(lib: Library, *, with_inbox: bool = True, papers: Sequence[Paper] | None = None,
                    items: Sequence[Item] | None = None) -> list[dupes.Pair]:
    """Near-duplicates within the library and between it and the inbox."""
    papers = list(lib.papers()) if papers is None else papers
    if items is None:
        items = Inbox(lib.root).items() if with_inbox else []
    library = library_counts(lib, papers)
    entries = [dupes.Entry(p.key, p, library[p.key], True) for p in papers if library.get(p.key)]
    inbox = inbox_counts(lib, items)
    entries += [dupes.Entry(i.id, i.paper, inbox[i.id], False) for i in items if inbox.get(i.id)]
    return dupes.find(entries)


@dataclass
class IndexUpdate:
    papers: int
    inbox: int
    pdfs: PdfSync | None = None


def update_index(lib: Library, *, pdfs: bool = False, retry: bool = False,
                 progress: Callable[[str], None] | None = None) -> IndexUpdate:
    """Bring the index up to date: word counts always (cheap), PDF text only when asked (slow)."""
    papers = list(lib.papers())
    items = Inbox(lib.root).items()
    update = IndexUpdate(len(library_counts(lib, papers)), len(inbox_counts(lib, items)))
    if pdfs:
        update.pdfs = fulltext.sync_pdf_text(lib, retry=retry, progress=progress)
    return update
