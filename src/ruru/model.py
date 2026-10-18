"""The on-disk paper: `paper.md` = YAML front matter (the source of truth) + Markdown notes.

The front matter is written with every known field, always in the order of FIELDS, so git diffs
stay readable. Unknown fields are kept, after the known ones. The body is kept verbatim apart
from the edits below: Jesse's `## Notes` section and the agent-written `## Agent notes` section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import yaml

from ruru.text import norm_title

STATUSES = ("to-read", "later", "reading", "read")
UNREAD = STATUSES[:-1]  # the TUI's reading list; `read` papers are its Library
TYPES = ("article", "inproceedings", "preprint", "book", "thesis", "misc")
ID_KINDS = ("doi", "arxiv", "openalex", "s2")
FIELDS = (
    "key", "title", "authors", "year", "venue", "type", "volume", "issue", "pages", "publisher",
    "ids", "url", "pdf_url", "abstract", "status", "tags", "projects", "added", "read_on", "source",
)
DATE_FIELDS = ("added", "read_on")

NOTES_HEADING = "## Notes"
AGENT_HEADING = "## Agent notes"
AGENT_MARKER = "<!-- ruru: agent-written; each entry dated and attributed -->"
DEFAULT_BODY = (
    f"\n{NOTES_HEADING}\n\n\n{AGENT_HEADING}\n\n{AGENT_MARKER}\n"
)

_NOTES_RE = re.compile(r"^## Notes[ \t]*$", re.M)
_AGENT_RE = re.compile(r"^## Agent notes[ \t]*$", re.M)


def today() -> date:
    return date.today()


def _ids() -> dict[str, str | None]:
    return dict.fromkeys(ID_KINDS)


@dataclass
class Paper:
    key: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    type: str = "misc"
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    ids: dict[str, str | None] = field(default_factory=_ids)
    url: str | None = None
    pdf_url: str | None = None
    abstract: str | None = None
    status: str = "to-read"
    tags: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    added: date | None = None
    read_on: date | None = None
    source: str = "add"
    extra: dict[str, Any] = field(default_factory=dict)
    body: str = DEFAULT_BODY

    def front_matter(self) -> dict[str, Any]:
        fm = {name: getattr(self, name) for name in FIELDS}
        fm["ids"] = {kind: self.ids.get(kind) for kind in ID_KINDS} | {
            k: v for k, v in self.ids.items() if k not in ID_KINDS
        }
        return fm | self.extra

    @property
    def notes(self) -> str:
        return get_notes(self.body)

    @property
    def agent_notes(self) -> str:
        return get_agent_notes(self.body)


class FrontMatterError(ValueError):
    pass


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def from_front_matter(fm: dict[str, Any], body: str = DEFAULT_BODY) -> Paper:
    known = {name: fm[name] for name in FIELDS if name in fm}
    extra = {k: v for k, v in fm.items() if k not in FIELDS}
    for name in DATE_FIELDS:
        if name in known:
            known[name] = _as_date(known[name])
    for name in ("volume", "issue", "pages"):
        if known.get(name) is not None:
            known[name] = str(known[name])
    if known.get("year") is not None:
        known["year"] = int(known["year"])
    known["ids"] = _ids() | {k: (None if v is None else str(v)) for k, v in (known.get("ids") or {}).items()}
    for name in ("authors", "tags", "projects"):
        known[name] = [str(x) for x in known.get(name) or []]
    return Paper(**known, extra=extra, body=body)


def loads(text: str) -> Paper:
    if not text.startswith("---\n"):
        raise FrontMatterError("paper.md must start with a '---' front matter block")
    end = text.find("\n---\n", 3)
    if end < 0:
        raise FrontMatterError("unterminated front matter block")
    fm = yaml.safe_load(text[4:end + 1]) or {}
    if not isinstance(fm, dict):
        raise FrontMatterError("front matter must be a mapping")
    return from_front_matter(fm, text[end + 5:])


def dump_value(key: str, value: Any) -> str:
    width = 2**31 - 1
    if isinstance(value, list | dict):
        # Flow style for lists and the ids mapping: one line each, as in the README example.
        return f"{key}: " + yaml.safe_dump(value, default_flow_style=True, allow_unicode=True,
                                           sort_keys=False, width=width)
    return yaml.safe_dump({key: value}, allow_unicode=True, sort_keys=False, width=width)


def dumps(paper: Paper) -> str:
    fm = "".join(dump_value(k, v) for k, v in paper.front_matter().items())
    return f"---\n{fm}---\n{paper.body}"


# --- body sections -------------------------------------------------------------------------------

def get_notes(body: str) -> str:
    m = _NOTES_RE.search(body)
    if not m:
        return ""
    a = _AGENT_RE.search(body, m.end())
    return body[m.end():a.start() if a else len(body)].strip("\n")


def set_notes(body: str, text: str) -> str:
    m = _NOTES_RE.search(body)
    if not m:
        body = body.rstrip("\n") + f"\n\n{NOTES_HEADING}\n"
        m = _NOTES_RE.search(body)
        assert m is not None
    a = _AGENT_RE.search(body, m.end())
    end = a.start() if a else len(body)
    text = text.strip("\n")
    middle = f"\n\n{text}\n\n" if text else "\n\n\n"
    if not a:
        middle = middle.rstrip("\n") + "\n"
    return body[:m.end()] + middle + body[end:]


def get_agent_notes(body: str) -> str:
    a = _AGENT_RE.search(body)
    if not a:
        return ""
    return body[a.end():].replace(AGENT_MARKER, "").strip("\n")


def append_agent_note(body: str, heading: str, text: str) -> str:
    if not _AGENT_RE.search(body):
        body = body.rstrip("\n") + f"\n\n{AGENT_HEADING}\n\n{AGENT_MARKER}\n"
    return body.rstrip("\n") + f"\n\n### {heading}\n\n{text.strip()}\n"


# --- identity ------------------------------------------------------------------------------------

def norm_id(kind: str, value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if kind == "doi":
        value = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", value, flags=re.I).lower()
    elif kind == "arxiv":
        value = re.sub(r"^(arxiv:|https?://arxiv\.org/(abs|pdf)/)", "", value, flags=re.I)
        value = re.sub(r"(v\d+)?(\.pdf)?$", "", value)
    elif kind == "openalex":
        value = value.rsplit("/", 1)[-1].upper()
    return value or None


def same_paper(a: Paper, b: Paper) -> bool:
    """A shared DOI, arXiv, OpenAlex or S2 id, or the same normalised title within a year."""
    for kind in ID_KINDS:
        x, y = norm_id(kind, a.ids.get(kind)), norm_id(kind, b.ids.get(kind))
        if x and x == y:
            return True
    ta, tb = norm_title(a.title), norm_title(b.title)
    if not ta or ta != tb:
        return False
    return a.year is None or b.year is None or abs(a.year - b.year) <= 1
