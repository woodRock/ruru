"""Reviews: filing and triaging syntheses of reading-list papers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from ruru import model
from ruru.errors import UsageError
from ruru.fsutil import atomic_write
from ruru.library import Library
from ruru.ops.common import summary_of
from ruru.reviews import Review, Reviews, problems
from ruru.reviews import dumps as reviews_dumps
from ruru.reviews import loads as load_review
from ruru.reviews import slug as reviews_slug


def add_review(lib: Library, text: str, *, by: str | None = None, replace: bool = False) -> Review:
    """File a review (`reviews.loads` text) as new. Every paper it covers must be on the reading
    list (`model.UNREAD`): reviews are for what Jesse hasn't read yet. Missing `id`/`created`/`by`
    are filled in; `replace` overwrites a review with the same id that is still new."""
    review = load_review(text)
    review.created = review.created or model.now()
    day = review.created.date() if isinstance(review.created, datetime) else review.created
    review.id = review.id or reviews_slug(review.title, day)
    review.by = review.by or by
    review.status = "new"
    review.tags = sorted({t.strip().lower() for t in review.tags if t.strip()})
    issues = problems(review)
    papers = {k: lib.get(k) for k in review.papers if (lib.paper_dir(k) / "paper.md").exists()}
    if unknown := [k for k in review.papers if k not in papers]:
        issues.append(f"no paper with key {', '.join(unknown)}")
    if read := [f"{k} ({p.status})" for k, p in papers.items() if p.status not in model.UNREAD]:
        issues.append(f"{', '.join(read)} not on the reading list: reviews only cover papers yet to be read")
    if no_pdf := [k for k in papers if not lib.has_pdf(k)]:
        issues.append(f"no PDF for {', '.join(no_pdf)}: a review needs every paper's full text "
                      "(`ruru pdf missing --open`, save each as KEY.pdf, then `ruru pdf add`)")
    if issues:
        raise UsageError("review not filed: " + "; ".join(issues))
    store = Reviews(lib.root)
    path = store.path_for(review.id)
    if path.exists():
        current = store.get(review.id)
        if not replace or current.status != "new":
            hint = "; --replace overwrites it" if current.status == "new" else " (kept: give a new id)"
            raise UsageError(f"there is already a review {review.id!r}{hint}")
    review.path = path
    with lib.transaction(f"ruru: review {review.id}" + (f" ({review.by})" if review.by else "")) as tx:
        tx.write(path, reviews_dumps(review))
    return review


def triage_reviews(lib: Library, ids: Sequence[str], decision: str) -> list[Review]:
    """keep: the review stays, no longer new; discard: deleted (git history keeps it)."""
    if decision not in ("keep", "discard"):
        raise UsageError("decision must be keep or discard")
    store = Reviews(lib.root)
    found = [store.get(i) for i in dict.fromkeys(ids)]  # unknown ids fail before anything is written
    if not found:
        raise UsageError("give at least one review id")
    with lib.transaction(f"ruru: review {decision} {summary_of([r.id for r in found])}") as tx:
        for r in found:
            assert r.path is not None
            if decision == "discard":
                tx.remove(r.path)
            else:
                r.status = "kept"
                tx.write(r.path, reviews_dumps(r))
    return found



def render_review_file(lib: Library, review_id: str) -> Path:
    """Render a review to .ruru/reviews/<id>.html (git-ignored; regenerated every time)."""
    from ruru.export import render_review
    r = Reviews(lib.root).get(review_id)
    path = lib.root / ".ruru" / "reviews" / f"{r.id}.html"
    atomic_write(path, render_review(lib, r))
    return path
