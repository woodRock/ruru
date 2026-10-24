"""Text from a paper's PDF: for full-text search, and to check that a quote is really in the paper.

Uses pypdf (the optional `ruru[pdf]` extra) and falls back to poppler's `pdftotext` if that is on
the PATH. Pages are separated by a form feed, so a match can be given a page number.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from ruru.errors import UsageError

PAGE_BREAK = "\f"


class PdfTextUnavailable(UsageError):
    def __init__(self) -> None:
        super().__init__("no way to read PDF text: pip install 'ruru[pdf]' (pypdf), "
                         "or install poppler's pdftotext")


def have_extractor() -> bool:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return shutil.which("pdftotext") is not None
    return True


def extract(path: Path) -> str:
    """The PDF's text, pages joined by a form feed. Raises PdfTextUnavailable or an error naming the file."""
    try:
        import pypdf
    except ImportError:
        return _poppler(path)
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # it chatters about slightly malformed files
    try:
        reader = pypdf.PdfReader(str(path))
        return PAGE_BREAK.join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:
        raise UsageError(f"{path.name}: {e}") from None


def _poppler(path: Path) -> str:
    exe = shutil.which("pdftotext")
    if exe is None:
        raise PdfTextUnavailable()
    res = subprocess.run([exe, "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise UsageError(f"{path.name}: pdftotext failed: {res.stderr.strip()}")
    return res.stdout  # pdftotext already separates pages with a form feed


def pages(text: str) -> list[str]:
    return text.split(PAGE_BREAK)


# --- finding a quote -----------------------------------------------------------------------------

_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         "−": "-", " ": " "})


def normalise(text: str) -> str:
    """Text as comparable prose: line-end hyphenation joined, ligatures and curly quotes undone, accents
    and case folded, every run of whitespace one space."""
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = unicodedata.normalize("NFKD", text.translate(_QUOTES))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


def find_quote(text: str, quote: str) -> int | None:
    """The page (from 1) a quote appears on, ignoring line breaks, hyphenation, ligatures and case;
    None if it is not in the text. A quote spanning a page break is found on its first page."""
    wanted = normalise(quote).strip("…. \"'")
    if not wanted:
        return None
    parts = pages(text)
    for n, page in enumerate(parts, 1):
        if wanted in normalise(page):
            return n
    joined = [normalise(parts[i] + " " + parts[i + 1]) for i in range(len(parts) - 1)]
    for n, both in enumerate(joined, 1):
        if wanted in both:
            return n
    return None
