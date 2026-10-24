"""PDF text: extracting it into the index, searching it, and checking that a quote is in a paper."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ruru import pdftext
from ruru.index import PdfSync
from ruru.library import Library
from ruru.model import Paper

Progress = Callable[[str], None]


def pdf_paths(lib: Library, keys: Sequence[str] | None = None) -> dict[str, Path]:
    """key -> paper.pdf for every paper that has one (or just `keys`)."""
    wanted = lib.keys() if keys is None else list(keys)
    return {k: lib.paper_dir(k) / "paper.pdf" for k in wanted if lib.has_pdf(k)}


def sync_pdf_text(lib: Library, keys: Sequence[str] | None = None, *, retry: bool = False,
                  progress: Progress | None = None) -> PdfSync:
    """Extract the text of each PDF that is new or changed since it was last read. Raises
    PdfTextUnavailable when there is something to extract and nothing to extract it with."""
    pdfs = pdf_paths(lib, keys)
    if not pdftext.have_extractor() and lib.index.pending_pdfs(pdfs):
        raise pdftext.PdfTextUnavailable()
    # The extractor is looked up at call time, so that tests can replace it. A sync of some keys must
    # not forget the text of the others: only a full sync drops PDFs that are gone.
    return lib.index.sync_pdfs(pdfs, lambda path: pdftext.extract(path), retry=retry, progress=progress,
                               forget=keys is None)


def pdf_text(lib: Library, key: str, *, progress: Progress | None = None) -> str | None:
    """The text of a paper's PDF (extracted now if needed); None if it has no PDF or none could be read."""
    if not lib.has_pdf(key):
        return None
    result = sync_pdf_text(lib, [key], progress=progress)
    why = result.failed.get(key) or lib.index.pdf_error(key)  # now, or on an earlier try (until it changes)
    if why:
        raise pdftext.UsageError(f"{key}: no text from the PDF: {why}")
    return lib.index.pdf_text(key)


def search_pdfs(lib: Library, query: str, *, progress: Progress | None = None) -> tuple[dict[str, str], PdfSync]:
    """key -> snippet for each PDF containing every word of `query` (reading new PDFs first)."""
    result = sync_pdf_text(lib, progress=progress)
    return dict(lib.index.search_pdfs(query)), result


@dataclass(frozen=True)
class QuoteCheck:
    status: str  # found | missing | unavailable
    page: int | None = None  # the PDF page it was found on
    why: str = ""


def check_quote(lib: Library, key: str, quote: str) -> QuoteCheck:
    """Is `quote` in the paper's PDF? `unavailable` when there is no PDF text to look in."""
    if not lib.exists(key) or not lib.has_pdf(key):
        return QuoteCheck("unavailable", why="no PDF")
    try:
        text = pdf_text(lib, key)
    except pdftext.UsageError as e:
        return QuoteCheck("unavailable", why=str(e))
    if not text or not text.strip(pdftext.PAGE_BREAK + " \n"):
        return QuoteCheck("unavailable", why="the PDF has no text layer (scanned?)")
    page = pdftext.find_quote(text, quote)
    return QuoteCheck("found", page) if page else QuoteCheck("missing")


def merge_hits(hits: list[tuple[int, Paper]], pdf: dict[str, str], lib: Library
               ) -> list[tuple[int, Paper, str | None]]:
    """Metadata hits plus PDF hits: a paper found in both scores one higher; a PDF-only hit scores 1."""
    by_key = {p.key: (score, p) for score, p in hits}
    out: list[tuple[int, Paper, str | None]] = []
    for key, (score, p) in by_key.items():
        out.append((score + 1, p, pdf[key]) if key in pdf else (score, p, None))
    for key, snippet in pdf.items():
        if key not in by_key and lib.exists(key):
            out.append((1, lib.get(key), snippet))
    out.sort(key=lambda h: (-h[0], h[1].key))
    return out
