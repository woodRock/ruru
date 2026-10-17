"""`ruru export json`: the whole library as one JSON document (schema in docs/json.md).

Papers, inbox items, feeds and projects use exactly the `--json` shapes of `list`/`show`, `inbox`,
`feeds` and `project`, so one schema serves both. Every later view (a static site, a digest) is
generated from this document, never from the library directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ruru import feeds, gitops, projects
from ruru.inbox import Inbox
from ruru.library import Library
from ruru.output import item_json, paper_json

SCHEMA_VERSION = 1


def export_document(lib: Library, *, activity: int = 100, notes: bool = True) -> dict[str, Any]:
    state = feeds.load_state(lib.root)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "library": [
            paper_json(p, path=str((lib.paper_dir(p.key) / "paper.md").relative_to(lib.root)),
                       has_pdf=lib.has_pdf(p.key), full=notes)
            for p in lib.papers()
        ],
        "inbox": [item_json(i, lib.root) for i in Inbox(lib.root).items()],
        "feeds": [f.to_dict() | {"last_fetch": (state.get(f.name) or {}).get("last_fetch")}
                  for f in feeds.load(lib.settings.feeds)],
        "projects": [{"name": n, **p.to_dict(lib.settings.projects[n].get("path"))}
                     for n, p in projects.load(lib.settings.projects).items()],
        "activity": gitops.log(lib.root, activity),
    }
