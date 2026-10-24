"""Library operations shared by the CLI and the TUI.

Network work happens before any lock is taken; each operation then writes in one transaction. The
operations live in submodules by area; everything public is re-exported here, so callers (and
tests that replace one) say `ops.fetch`, `ops.triage`, … whichever module defines it.
"""

from __future__ import annotations

from ruru.ops.claims import (
    cited_keys,
    cited_sources,
    cited_versions,
    claims,
    claims_and_files,
    drop_verdicts,
    record_verdicts,
)
from ruru.ops.fulltext import (
    QuoteCheck,
    check_quote,
    merge_hits,
    pdf_paths,
    pdf_text,
    search_pdfs,
    sync_pdf_text,
)
from ruru.ops.history import Commit, undo, undoable
from ruru.ops.papers import (
    EnrichResult,
    add_pdfs,
    cited_papers,
    client_for,
    enrich,
    fetch_open_access,
    fetch_pdf,
    paper_link,
    rekey,
    remove,
    set_notes,
    set_status,
    tag,
)
from ruru.ops.projects import CiteResult, add_project, check, cite, get_project, remove_project
from ruru.ops.ranking import (
    IndexUpdate,
    Related,
    duplicate_pairs,
    order_inbox,
    ranked_inbox,
    related,
    relevance,
    update_index,
)
from ruru.ops.reviews import add_review, render_review_file, triage_reviews
from ruru.ops.settings import (
    OWN_TAG,
    add_cited,
    add_feed,
    cited_entries,
    own_keys,
    remove_cited,
    remove_feed,
    set_own,
)
from ruru.ops.triage import FeedResult, fetch, suggest, triage, unkeep, unskip

__all__ = [
    "OWN_TAG",
    "CiteResult",
    "Commit",
    "EnrichResult",
    "FeedResult",
    "IndexUpdate",
    "QuoteCheck",
    "Related",
    "add_cited",
    "add_feed",
    "add_pdfs",
    "add_project",
    "add_review",
    "check",
    "check_quote",
    "cite",
    "cited_entries",
    "cited_keys",
    "cited_papers",
    "cited_sources",
    "cited_versions",
    "claims",
    "claims_and_files",
    "client_for",
    "drop_verdicts",
    "duplicate_pairs",
    "enrich",
    "fetch",
    "fetch_open_access",
    "fetch_pdf",
    "get_project",
    "merge_hits",
    "order_inbox",
    "own_keys",
    "paper_link",
    "pdf_paths",
    "pdf_text",
    "ranked_inbox",
    "record_verdicts",
    "rekey",
    "related",
    "relevance",
    "remove",
    "remove_cited",
    "remove_feed",
    "remove_project",
    "render_review_file",
    "search_pdfs",
    "set_notes",
    "set_own",
    "set_status",
    "suggest",
    "sync_pdf_text",
    "tag",
    "triage",
    "triage_reviews",
    "undo",
    "undoable",
    "unkeep",
    "unskip",
    "update_index",
]
