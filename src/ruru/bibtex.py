"""BibTeX out (deterministic, from the front matter) and in (bibtexparser v1).

Two encodings, set by `bib_encoding` in ruru.toml: `ascii` escapes non-ASCII letters for classic
BibTeX (`ā` → `{\\=a}`), `utf8` keeps them for biber/biblatex. Both escape `&`, `%` and `#`.
"""

from __future__ import annotations

import re
import unicodedata

from ruru.model import Paper, norm_id
from ruru.text import display_name, split_name

ENCODINGS = ("ascii", "utf8")

_ENTRY_TYPE = {
    "article": "article", "inproceedings": "inproceedings", "preprint": "misc", "book": "book",
    "thesis": "phdthesis", "misc": "misc",
}
_VENUE_FIELD = {"article": "journal", "inproceedings": "booktitle", "thesis": "school", "misc": "howpublished"}

_ACCENTS = {
    "́": "'", "̀": "`", "̂": "^", "̈": '"', "̃": "~", "̄": "=",
    "̇": ".", "̌": "v", "̆": "u", "̋": "H", "̧": "c", "̨": "k",
    "̊": "r", "̣": "d",
}
_LETTERS = {
    "ø": r"{\o}", "Ø": r"{\O}", "ß": r"{\ss}", "æ": r"{\ae}", "Æ": r"{\AE}", "œ": r"{\oe}",
    "Œ": r"{\OE}", "ł": r"{\l}", "Ł": r"{\L}", "ı": r"{\i}", "å": r"{\aa}", "Å": r"{\AA}",
    "–": "--", "—": "---", "‘": "`", "’": "'", "“": "``", "”": "''", "…": r"\ldots{}",
    " ": "~", "±": r"$\pm$", "×": r"$\times$", "α": r"$\alpha$", "β": r"$\beta$",
    "γ": r"$\gamma$", "δ": r"$\delta$", "μ": r"$\mu$", "σ": r"$\sigma$", "λ": r"$\lambda$",
}


def _ascii(s: str) -> str:
    out: list[str] = []
    for ch in s:
        if ord(ch) < 128:
            out.append(ch)
        elif ch in _LETTERS:
            out.append(_LETTERS[ch])
        else:
            decomposed = unicodedata.normalize("NFD", ch)
            base, marks = decomposed[0], decomposed[1:]
            if marks and all(m in _ACCENTS for m in marks) and ord(base) < 128:
                if base == "i":
                    base = "\\i"  # dotless i under an accent: {\'\i}
                for m in marks:
                    sep = " " if _ACCENTS[m].isalpha() and base[0] != "\\" else ""
                    base = "\\" + _ACCENTS[m] + sep + base
                out.append("{" + base + "}")
            else:
                out.append(ch)  # no LaTeX form known: left as is
    return "".join(out)


def escape(s: str, encoding: str = "ascii") -> str:
    s = re.sub(r"(?<!\\)([&%#])", r"\\\1", s)
    return _ascii(s) if encoding == "ascii" else s


def _protect(title: str) -> str:
    """Braces round words with inner capitals (GC–MS, LiDAR), so sentence-case styles keep them."""
    def guard(token: str) -> str:
        if any(c in token for c in "{}$\\"):
            return token
        parts = re.split(r"[-–—/]", token)
        if any(re.search(r"[A-Za-z0-9]\S*[A-Z]", re.sub(r"[^\w]", "", p)) for p in parts):
            return "{" + token + "}"
        return token
    return " ".join(guard(t) for t in title.split())


def _name(author: str) -> str:
    given, family = split_name(author)
    if not given:
        return "{" + family + "}" if " " in family else family
    return f"{family}, {given}"


def to_bibtex(paper: Paper, encoding: str = "ascii") -> str:
    fields: list[tuple[str, str]] = []

    def add(name: str, value: str | None, *, text: bool = True) -> None:
        if value:
            fields.append((name, escape(value, encoding) if text else value))

    arxiv = norm_id("arxiv", paper.ids.get("arxiv"))
    add("author", " and ".join(_name(a) for a in paper.authors))
    if paper.title:
        add("title", _protect(paper.title))
    venue_field = _VENUE_FIELD.get(paper.type)
    publisher = paper.publisher
    if paper.type == "book" and not publisher:
        publisher = paper.venue
    elif venue_field:
        add(venue_field, paper.venue)
    if paper.type == "preprint" and arxiv and not paper.venue:
        add("howpublished", f"arXiv preprint arXiv:{arxiv}")
    add("year", str(paper.year) if paper.year else None)
    add("volume", paper.volume)
    add("number", paper.issue)
    add("pages", re.sub(r"\s*[-–]+\s*", "--", paper.pages) if paper.pages else None)
    add("publisher", publisher)
    add("doi", norm_id("doi", paper.ids.get("doi")), text=False)
    if arxiv:
        add("eprint", arxiv, text=False)
        add("archiveprefix", "arXiv")
    add("url", paper.url, text=False)
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
    return f"@{_ENTRY_TYPE.get(paper.type, 'misc')}{{{paper.key},\n{body}\n}}\n"


# --- parsing -------------------------------------------------------------------------------------

def _strip_braces(s: str) -> str:
    return " ".join(s.replace("{", "").replace("}", "").split())


def _paper_type(entry: dict[str, str]) -> str:
    t = entry.get("ENTRYTYPE", "misc").lower()
    if t in ("article", "book"):
        return t
    if t in ("inproceedings", "conference", "proceedings"):
        return "inproceedings"
    if t.endswith("thesis"):
        return "thesis"
    if entry.get("eprint") or "arxiv" in entry.get("journal", "").lower():
        return "preprint"
    return "misc"


_AND = re.compile(r"\s+and\s+")


def parse_bibtex(text: str) -> list[tuple[str, Paper]]:
    """(original key, Paper without a key) for every entry, in file order."""
    import bibtexparser  # imported here: it costs ~20 ms that most commands never need
    from bibtexparser.bparser import BibTexParser
    from bibtexparser.customization import convert_to_unicode

    parser = BibTexParser(common_strings=True)
    parser.customization = convert_to_unicode
    parser.ignore_nonstandard_types = False
    db = bibtexparser.loads(text, parser=parser)
    out = []
    for e in db.entries:
        e = {k.lower(): v for k, v in e.items()} | {"ENTRYTYPE": e["ENTRYTYPE"], "ID": e["ID"]}
        ptype = _paper_type(e)
        arxiv = e.get("eprint") if e.get("archiveprefix", "arxiv").lower() == "arxiv" else None
        year = re.search(r"\d{4}", e.get("year", ""))
        paper = Paper(
            title=_strip_braces(e.get("title", "")),
            authors=[display_name(_strip_braces(a)) for a in _AND.split(e.get("author", "")) if a.strip()],
            year=int(year.group()) if year else None,
            venue=_strip_braces(e.get("journal") or e.get("booktitle") or e.get("school") or "") or None,
            type=ptype,
            volume=e.get("volume"), issue=e.get("number"),
            pages=e["pages"].replace("--", "-") if e.get("pages") else None,
            publisher=_strip_braces(e["publisher"]) if e.get("publisher") else None,
            url=e.get("url"),
            abstract=_strip_braces(e["abstract"]) if e.get("abstract") else None,
            source="import",
        )
        paper.ids["doi"] = norm_id("doi", e.get("doi"))
        paper.ids["arxiv"] = norm_id("arxiv", arxiv)
        out.append((e["ID"], paper))
    return out
