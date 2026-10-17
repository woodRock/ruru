"""`ruru doctor`: is everything set up, consistent and reachable?

Checks config, settings, git (repo, identity, uncommitted changes), feeds and projects, every
paper.md (parses; paper.bib in step with it), the inbox, and (unless offline) the APIs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ruru import feeds, gitops, projects
from ruru.bibtex import to_bibtex
from ruru.config import config_path, load_settings, secret
from ruru.errors import RuruError
from ruru.http import Client
from ruru.inbox import Inbox
from ruru.library import Library


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)


APIS: dict[str, tuple[str, dict[str, Any]]] = {
    "crossref": ("https://api.crossref.org/works", {"rows": 0}),
    "openalex": ("https://api.openalex.org/works", {"per-page": 1, "select": "id"}),
    "arxiv": ("https://export.arxiv.org/api/query", {"search_query": "all:chromatography", "max_results": 1}),
}


def run(root: Path, *, offline: bool = False, client: Callable[[Library], Client] | None = None) -> list[Check]:
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str, *, bad: str = "fail") -> None:
        checks.append(Check(name, "ok" if ok else bad, detail))

    cfg = config_path()
    add("config", cfg.exists(), str(cfg) if cfg.exists() else f"{cfg} missing; using the default library",
        bad="warn")
    try:
        settings = load_settings(root)
    except RuruError as e:
        add("library", False, str(e))
        return checks
    add("library", True, str(root))
    lib = Library(root, settings)
    add("contact email", bool(settings.contact_email),
        settings.contact_email or "not set in ruru.toml (OpenAlex and Crossref polite pools)", bad="warn")
    add("openalex key", bool(secret("openalex_api_key")),
        "set" if secret("openalex_api_key") else "not set: OpenAlex's small shared daily budget applies",
        bad="warn")

    if not gitops.is_repo(root):
        add("git", False, "not a git repository: changes are not recorded", bad="warn")
    else:
        who = gitops.identity(root)
        add("git identity", bool(who), who or "no user.name/user.email: auto-commits will fail")
        dirty = gitops.status(root)
        add("git status", not dirty, "clean" if not dirty else
            f"{len(dirty)} uncommitted change(s), e.g. {dirty[0].strip()}", bad="warn")

    try:
        configured = feeds.load(settings.feeds)
        add("feeds", True, f"{len(configured)} configured" if configured else "none: `ruru feeds wizard`")
    except RuruError as e:
        add("feeds", False, str(e))
    try:
        for name, p in projects.load(settings.projects).items():
            missing = [str(x) for x in (p.path, *p.tex_paths) if not x.exists()]
            add(f"project {name}", not missing, "ok" if not missing else f"missing: {', '.join(missing)}",
                bad="warn")
    except RuruError as e:
        add("projects", False, str(e))

    broken, stale = [], []
    keys = lib.keys()
    for key in keys:
        try:
            paper = lib.get(key)
        except RuruError:
            broken.append(key)
            continue
        bib = lib.paper_dir(key) / "paper.bib"
        if not bib.exists() or bib.read_text(encoding="utf-8") != to_bibtex(paper, settings.bib_encoding):
            stale.append(key)
    add("papers", not broken, f"{len(keys)} papers" + (f"; unreadable: {', '.join(broken)}" if broken else ""))
    add("paper.bib", not stale, "all in step with paper.md" if not stale else
        f"out of step (edited by hand?): {', '.join(stale[:5])}; any ruru write regenerates them", bad="warn")
    box = Inbox(root)
    files = list(box.dir.glob("*/*.md")) if box.dir.is_dir() else []
    readable = len(box.items()) if files else 0
    add("inbox", readable == len(files), f"{readable} items" + (
        f"; {len(files) - readable} unreadable" if readable != len(files) else ""), bad="warn")

    if offline:
        return checks
    make = client or (lambda lib: Client(email=settings.contact_email, openalex_key=secret("openalex_api_key"),
                                         retries=0, timeout=15))
    with make(lib) as c:
        for name, (url, params) in APIS.items():
            start = time.monotonic()
            try:
                status = c.get(url, params).status_code
                ms = (time.monotonic() - start) * 1000
                add(f"api {name}", status == 200, f"HTTP {status} in {ms:.0f} ms")
            except RuruError as e:
                add(f"api {name}", False, str(e))
    return checks

