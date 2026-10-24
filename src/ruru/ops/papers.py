"""Edits to library papers: rekey, remove, tags, status, notes, enrichment and PDFs."""

from __future__ import annotations

import contextlib
import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ruru import model
from ruru.errors import NothingMatched, RuruError, UnknownKey, UsageError
from ruru.fsutil import atomic_write, library_lock
from ruru.http import Client, HttpError
from ruru.inbox import (
    append_seen,
)
from ruru.keys import base_key, unique_key
from ruru.library import Library
from ruru.ops.common import summary_of
from ruru.resolve import Resolver, changes, fill_gaps
from ruru.text import norm_title

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:.+-]*$")


def rekey(lib: Library, old: str, new: str | None = None) -> tuple[str, list[str]]:
    """Rename a paper's key (the only way a key ever changes). `new` defaults to the key its
    current metadata would get. Returns (new key, projects whose .tex and .bib still say `old`)."""
    paper = lib.get(old)
    taken = set(lib.keys()) - {old}
    new = new or unique_key(base_key(paper.authors, paper.year, paper.title), taken)
    if not _KEY.match(new):
        raise UsageError(f"{new!r} is not a usable BibTeX key")
    if new == old:
        raise UsageError(f"{old} already has that key")
    if new in taken:
        raise UsageError(f"key {new!r} is already taken")
    old_dir, new_dir = lib.paper_dir(old), lib.paper_dir(new)
    with lib.transaction(f"ruru: rekey {old} -> {new}") as tx:
        paper.key = new
        tx.save(paper)
        for name in ("paper.md", "paper.bib"):
            tx.remove(old_dir / name)
        if (old_dir / "paper.pdf").exists():
            (old_dir / "paper.pdf").rename(new_dir / "paper.pdf")  # git-ignored: just moved
        with contextlib.suppress(OSError):
            old_dir.rmdir()
    return new, list(paper.projects)


def remove(lib: Library, keys: Sequence[str]) -> list[model.Paper]:
    """Delete papers from the library (paper.md, paper.bib and the git-ignored PDF), in one commit.
    Each gets a `remove` record in seen.jsonl so feeds don't bring it back; `git revert` undoes it
    (except the PDF). A project bib keeps the entry: `cite` leaves unknown managed keys alone."""
    if not keys:
        raise UsageError("give at least one key")
    papers = [lib.get(k) for k in dict.fromkeys(keys)]  # unknown keys fail before anything is removed
    records = [{"id": p.key, "decision": "remove", "date": model.today().isoformat(), "key": p.key,
                "ids": {k: v for k, v in p.ids.items() if v}, "title": p.title, "year": p.year} for p in papers]
    with lib.transaction(f"ruru: rm {summary_of([p.key for p in papers])}") as tx:
        for p in papers:
            d = lib.paper_dir(p.key)
            (d / "paper.pdf").unlink(missing_ok=True)  # git-ignored: not part of the commit
            tx.remove(d / "paper.bib")
            tx.remove(d / "paper.md")  # last: removes the directory once it is empty
        tx.write(lib.root / "seen.jsonl", append_seen(lib.root, records))
    return papers


def tag(lib: Library, keys: Sequence[str], add: Sequence[str] = (),
        remove: Sequence[str] = ()) -> list[model.Paper]:
    if not keys or not (add or remove):
        raise UsageError("usage: ruru tag KEY… +TAG… -TAG…")
    papers = [lib.get(k) for k in keys]  # unknown keys fail before anything is written
    ops = " ".join([f"+{t}" for t in add] + [f"-{t}" for t in remove])
    with lib.transaction(f"ruru: tag {' '.join(keys)} {ops}") as tx:
        for p in papers:
            p.tags = sorted((set(p.tags) | set(add)) - set(remove))
            tx.save(p)
    return papers


def set_status(lib: Library, keys: Sequence[str], status: str) -> list[model.Paper]:
    papers = [lib.get(k) for k in keys]
    with lib.transaction(f"ruru: status {' '.join(keys)} {status}") as tx:
        for p in papers:
            lib.set_status(p, status)
            tx.save(p)
    return papers


def set_notes(lib: Library, key: str, text: str) -> bool:
    """Replace Jesse's `## Notes` section. False (and nothing written) if it is unchanged."""
    paper = lib.get(key)
    if text.strip("\n") == paper.notes:
        return False
    with lib.transaction(f"ruru: notes {key}") as tx:
        paper.body = model.set_notes(paper.body, text)
        tx.save(paper)
    return True


@dataclass
class EnrichResult:
    key: str
    filled: dict[str, object]  # field (or "ids") -> its new value
    error: str | None = None


def enrich(lib: Library, client: Client, keys: Sequence[str] = (), *,
           dry_run: bool = False) -> tuple[list[EnrichResult], list[RuruError]]:
    """Fill the empty metadata (abstract, ids, venue, …) of `keys`, or of every paper without an
    abstract, from Crossref/OpenAlex/arXiv. Never changes a value that is there, so it is safe on
    BibTeX imports, and takes nothing from a record whose title differs from the paper's."""
    papers = [lib.get(k) for k in keys] if keys else [p for p in lib.papers() if not p.abstract]
    resolver = Resolver(client)
    results: list[EnrichResult] = []
    failures: list[RuruError] = []
    found: dict[str, model.Paper] = {}
    for p in papers:
        try:
            upstream = resolver.lookup(p)
            if norm_title(upstream.title) != norm_title(p.title):  # e.g. an arXiv id leading to a retitled article
                raise UsageError(f"the record found is titled {upstream.title!r}; not filled "
                                 f"(`ruru refresh {p.key}` would take it as it is)")
            new = fill_gaps(p, upstream)
            probe = model.Paper(ids={k: v for k, v in new.ids.items() if v and not p.ids.get(k)})
            other = lib.find_duplicate(probe) if any(probe.ids.values()) else None
            if other and other.key != p.key:
                raise UsageError(f"the record found is {other.key}'s (same id); not filled")
        except RuruError as e:
            print(f"ruru: {p.key}: {e}", file=sys.stderr)
            failures.append(e)
            results.append(EnrichResult(p.key, {}, str(e)))
            continue
        filled = {f: n for f, (_, n) in changes(p, new).items()}
        results.append(EnrichResult(p.key, filled))
        if filled:
            found[p.key] = upstream
    if found and not dry_run:
        with lib.transaction(f"ruru: enrich {summary_of(list(found))}") as tx:
            for key, upstream in found.items():
                tx.save(fill_gaps(lib.get(key), upstream))  # re-read: someone may have edited it meanwhile
    return results, failures


def paper_link(p: model.Paper) -> str | None:
    """Where to read or download a paper: its DOI (the publisher, institutional sign-in), else its URL."""
    return f"https://doi.org/{p.ids['doi']}" if p.ids.get("doi") else p.url


def add_pdfs(lib: Library, paths: Sequence[Path], *, key: str | None = None,
             force: bool = False) -> tuple[list[tuple[str, Path]], list[tuple[Path, str]]]:
    """Move downloaded PDFs to papers/KEY/paper.pdf. A file is matched by its name (`KEY.pdf`, case
    aside) or `key`; a directory contributes every PDF named after a library key and ignores the
    rest. Returns (key, source) for each filed PDF and (path, problem) for each refused one."""
    known = lib.keys()
    keys = {k.lower(): k for k in known}
    if key and key not in known:
        raise UnknownKey(key)
    if key and (len(paths) != 1 or paths[0].is_dir()):
        raise UsageError("--key names one file")
    candidates: list[tuple[Path, str | None]] = []
    for path in paths:
        if path.is_dir():
            candidates += [(f, keys[f.stem.lower()]) for f in sorted(path.glob("*.pdf")) if f.stem.lower() in keys]
        else:
            candidates.append((path, key or keys.get(path.stem.lower())))
    filed: list[tuple[str, Path]] = []
    refused: list[tuple[Path, str]] = []
    with library_lock(lib.root):
        for src, k in candidates:
            dest = lib.paper_dir(k) / "paper.pdf" if k else None
            if not src.is_file():
                refused.append((src, "no such file"))
            elif not k or dest is None:
                refused.append((src, f"no paper with key {src.stem!r} (name it KEY.pdf, or use --key)"))
            elif not src.read_bytes()[:5].startswith(b"%PDF"):
                refused.append((src, "not a PDF (a saved login or landing page?)"))
            elif dest.exists() and not force:
                refused.append((src, f"{k} already has a PDF (--force replaces it)"))
            else:
                shutil.move(str(src), dest)  # git-ignored: nothing to commit
                filed.append((k, src))
    return filed, refused


def fetch_pdf(lib: Library, client: Client, key: str, *, force: bool = False) -> Path:
    """Download the open-access PDF to paper.pdf (git-ignored), finding its URL first if needed."""
    paper = lib.get(key)
    path = lib.paper_dir(key) / "paper.pdf"
    if path.exists() and not force:
        return path
    url = paper.pdf_url
    if not url:
        url = Resolver(client).refresh(paper).pdf_url
        if url:
            with lib.transaction(f"ruru: pdf_url {key}") as tx:
                current = lib.get(key)
                current.pdf_url = url
                tx.save(current)
    if not url:
        raise NothingMatched(f"{key}: no open-access PDF known")
    print(f"fetching {url}", file=sys.stderr)
    response = client.get(url)
    if response.status_code != 200:
        raise HttpError(f"HTTP {response.status_code} for {url}")
    if not response.content.startswith(b"%PDF"):
        raise HttpError(f"not a PDF: {url} (a landing page or a paywall?)")
    with library_lock(lib.root):
        atomic_write(path, response.content)
    return path



def client_for(lib: Library) -> Client:
    """An HTTP client configured from the library's settings and the user config."""
    from ruru.config import secret
    return Client(email=lib.settings.contact_email, openalex_key=secret("openalex_api_key"),
                  cache_dir=lib.root / ".ruru" / "http")


def cited_papers(lib: Library, project: str) -> tuple[list[model.Paper], list[str]]:
    """The library papers the project's claims cite, and the cited keys the library lacks (drafts and
    software in `[cited]` left out: they have no PDF)."""
    from ruru.ops.claims import cited_keys
    outside = set(lib.settings.cited)
    cited = [k for k in cited_keys(lib, project) if k not in outside]
    return [lib.get(k) for k in cited if lib.exists(k)], [k for k in cited if not lib.exists(k)]


def fetch_open_access(lib: Library, papers: Sequence[model.Paper]) -> list[model.Paper]:
    """Download the open-access PDF of each paper without one; return those still without."""
    from ruru import ops  # resolved through the package, so that tests can replace ops.fetch_pdf

    missing = [p for p in papers if not lib.has_pdf(p.key)]
    if missing:
        with client_for(lib) as client:
            for p in missing:
                try:
                    ops.fetch_pdf(lib, client, p.key)
                except RuruError as e:
                    print(f"{p.key}: {e}", file=sys.stderr)
    return [p for p in missing if not lib.has_pdf(p.key)]
