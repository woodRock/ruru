"""Reviews: short syntheses of related papers on the reading list, `reviews/<id>.md`.

A review is front matter (`REVIEW_FIELDS`) plus a Markdown body that cites papers as `[@key]` or
`[@a; @b]`. Agents write them (`ruru review add`); Jesse triages them like an inbox: a review is
`new` until he keeps it (`kept`) or discards it (deleted; git keeps the history). Lists put new
reviews first, then sort by `SORTS` (default: newest filed first). Reviews only
cover papers on the reading list (`model.UNREAD`) whose PDF is in the library, so they rest on the
full text of what Jesse has yet to read; his own `## Notes` stay for what he has read and understood.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml

from ruru import gitops, model
from ruru.errors import UsageError
from ruru.text import unaccent

REVIEW_FIELDS = ("id", "title", "papers", "tags", "status", "created", "by")
STATUSES = ("new", "kept")
SORTS = ("created", "title", "papers")  # the first is the default

_CITATION = re.compile(r"\[((?:@[^\]\s;]+\s*;\s*)*@[^\]\s;]+)\]")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


class UnknownReview(UsageError):
    def __init__(self, review_id: str) -> None:
        super().__init__(f"no review {review_id!r}")


@dataclass
class Review:
    id: str
    title: str
    papers: list[str]
    body: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = "new"
    created: date | datetime | None = None  # a datetime since 2026-10-18; older reviews have a date
    by: str | None = None
    path: Path | None = field(default=None, compare=False)

    def front_matter(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "papers": self.papers, "tags": self.tags,
                "status": self.status, "created": self.created, "by": self.by}


def created_label(r: Review) -> str:
    if isinstance(r.created, datetime):
        return r.created.strftime("%Y-%m-%d %H:%M")
    return r.created.isoformat() if r.created else ""


def sort_reviews(reviews: Iterable[Review], by: str = SORTS[0], *,
                 filed: Callable[[Review], float] = lambda r: 0.0) -> list[Review]:
    """New reviews first, then: created (newest first; `filed`, e.g. the commit time, breaks ties
    between date-only reviews), title (A-Z) or papers (most first)."""
    def when(r: Review) -> float:
        if isinstance(r.created, datetime):
            return r.created.timestamp()
        if r.created:
            return max(datetime.combine(r.created, time.min).timestamp(), filed(r))
        return filed(r)
    keys: dict[str, Callable[[Review], tuple[object, ...]]] = {
        "created": lambda r: (r.status != "new", -when(r), r.id),
        "title": lambda r: (r.status != "new", r.title.lower(), r.id),
        "papers": lambda r: (r.status != "new", -len(r.papers), r.id),
    }
    return sorted(reviews, key=keys[by])


def slug(title: str, on: date) -> str:
    """`<date>-<title words>`, at most 80 characters, cut at a word boundary."""
    words = re.sub(r"[^a-z0-9]+", "-", unaccent(title).lower()).strip("-")
    out = f"{on.isoformat()}-{words}"
    if len(out) > 80:
        out = out[:81].rsplit("-", 1)[0]
    return out


def cited(body: str) -> list[str]:
    """Keys cited in `body`, in order of first appearance."""
    keys: list[str] = []
    for group in _CITATION.findall(body):
        for part in group.split(";"):
            key = part.strip().removeprefix("@")
            if key not in keys:
                keys.append(key)
    return keys


def citations(body: str) -> list[tuple[re.Match[str], list[str]]]:
    """Each `[@…]` match with its keys, for renderers."""
    return [(m, [p.strip().removeprefix("@") for p in m.group(1).split(";")]) for m in _CITATION.finditer(body)]


def dumps(review: Review) -> str:
    fm = "".join(model.dump_value(k, v) for k, v in review.front_matter().items())
    return f"---\n{fm}---\n\n{review.body.strip()}\n"


def loads(text: str, path: Path | None = None) -> Review:
    if not text.startswith("---\n") or "\n---" not in text[3:]:
        raise model.FrontMatterError("a review starts with a front matter block (--- … ---)")
    end = text.index("\n---", 3)
    fm = yaml.safe_load(text[4:end + 1]) or {}
    if not isinstance(fm, dict):
        raise model.FrontMatterError("review front matter must be a mapping")
    body = text[end + 4:].lstrip("-").strip("\n")
    papers = fm.get("papers") or []
    if not isinstance(papers, list):
        raise model.FrontMatterError("papers must be a list of keys")
    return Review(
        id=str(fm.get("id") or ""), title=str(fm.get("title") or "").strip(),
        papers=[str(k) for k in papers], body=body,
        tags=[str(t) for t in fm.get("tags") or []], status=str(fm.get("status") or "new"),
        created=model._as_date(fm.get("created")), by=fm.get("by"), path=path,
    )


def problems(review: Review) -> list[str]:
    """What is wrong with a review on its own (the library checks are `ops.add_review`'s)."""
    out = []
    if not review.title:
        out.append("it needs a title")
    if not review.papers:
        out.append("it needs `papers:`, the keys it covers")
    if not _ID.match(review.id):
        out.append(f"id {review.id!r}: lower-case letters, digits and '-' only")
    if review.status not in STATUSES:
        out.append(f"status must be one of {', '.join(STATUSES)}")
    if stray := [k for k in cited(review.body) if k not in review.papers]:
        out.append(f"it cites {', '.join(stray)}, not in `papers:`")
    if uncited := [k for k in review.papers if k not in cited(review.body)]:
        out.append(f"`papers:` lists {', '.join(uncited)}, never cited as [@key]")
    return out


class Reviews:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / "reviews"

    def path_for(self, review_id: str) -> Path:
        return self.dir / f"{review_id}.md"

    def all(self, by: str = SORTS[0]) -> list[Review]:
        """New first, then by `by` (see `sort_reviews`)."""
        out = []
        for path in self.dir.glob("*.md") if self.dir.is_dir() else []:
            try:
                out.append(loads(path.read_text(encoding="utf-8"), path))
            except (ValueError, yaml.YAMLError) as e:
                print(f"ruru: warning: skipping {path.relative_to(self.root)}: {e}", file=sys.stderr)
        filed: Callable[[Review], float] = lambda r: 0.0
        if by == "created" and any(not isinstance(r.created, datetime) for r in out):
            added = gitops.added_times(self.root, "reviews")
            filed = lambda r: float(added.get(f"reviews/{r.id}.md", 0))
        return sort_reviews(out, by, filed=filed)

    def get(self, review_id: str) -> Review:
        path = self.path_for(review_id)
        if not _ID.match(review_id) or not path.exists():
            raise UnknownReview(review_id)
        return loads(path.read_text(encoding="utf-8"), path)

    def citing(self, key: str) -> list[Review]:
        return [r for r in self.all() if key in r.papers]
