"""`ruru tui`: an interactive view of the library (optional extra: `pip install 'ruru[tui]'`).

Only `ruru tui` imports this module, and so Textual. Every action calls the same function the
CLI does (`ruru.ops`), so the two cannot diverge, and every change is committed as usual. The app
polls the library's files and reloads when something else (an agent, the CLI, a scheduled fetch)
changes them.

Keys: j/k move, / filter, r reload, q quit; tabs 1 Inbox, 2 Library, 3 Projects.
Inbox: y keep, n skip, l later, a accept the suggestion, t keep with tags, p keep for a project,
o open URL, f fetch, S relevance or date order (the match column: closeness to your library; ≈ marks a
possible duplicate). Library: enter open, t tag, s status, e edit notes, d PDF (fetch/open), o URL.
Projects: c re-check, m get the missing PDFs its claims cite, enter the claims screen.
Claims: / filter (k:KEY v:STATE: a verdict, unchecked, outdated, own), v verdict, x drop it, d the cited PDF
(or a draft's .tex, software's README), e edit the .tex there,
m missing PDFs.
"""

from __future__ import annotations

from ruru.tui.app import DETAIL_SECONDS, POLL_SECONDS, VIEW_SECONDS, RuruApp, fingerprint, run
from ruru.tui.render import (
    ClaimsView,
    claim_markdown,
    claim_marks,
    claims_summary,
    claims_view,
    filter_claims,
    filter_items,
    filter_papers,
    filter_reviews,
    item_markdown,
    paper_markdown,
    parse_filter,
    project_markdown,
    review_markdown,
)
from ruru.tui.screens import ClaimsScreen, PaperScreen
from ruru.tui.widgets import Choose, Prompt, Table

__all__ = [
    "DETAIL_SECONDS", "POLL_SECONDS", "VIEW_SECONDS", "Choose", "ClaimsScreen", "ClaimsView", "PaperScreen",
    "Prompt", "RuruApp", "Table", "claim_markdown", "claim_marks", "claims_summary", "claims_view",
    "filter_claims", "filter_items", "filter_papers", "filter_reviews", "fingerprint", "item_markdown",
    "paper_markdown", "parse_filter", "project_markdown", "review_markdown", "run",
]
