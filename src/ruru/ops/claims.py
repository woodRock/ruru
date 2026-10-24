"""Claims in a project's .tex, the verdicts recorded on them, and what a verdict is checked against."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ruru import cited as citedmod
from ruru import claims as claimsmod
from ruru import projects as projmod
from ruru.errors import RuruError, UsageError
from ruru.library import Library
from ruru.ops import fulltext
from ruru.ops.common import summary_of
from ruru.ops.projects import get_project
from ruru.ops.settings import cited_entries
from ruru.projects import Project, scan_tex


def _tex_scan(project: Project) -> projmod.TexScan:
    if not project.tex:
        raise UsageError(f"project {project.name!r} lists no tex files; re-add it with --tex")
    scan = scan_tex(project.tex_paths)
    for f in scan.missing_files:
        print(f"ruru: warning: tex file not found: {f}", file=sys.stderr)
    return scan


def cited_sources(lib: Library, key: str) -> list[Path]:
    """What a claim citing `key` is checked against: the paper's PDF, a draft's .tex files, or a
    repo's README and CITATION.cff. Empty when there's nothing to read yet."""
    if lib.exists(key):
        return [lib.paper_dir(key) / "paper.pdf"] if lib.has_pdf(key) else []
    entry = cited_entries(lib).get(key)
    if entry is None:
        return []
    if entry.kind == "draft":
        assert entry.project
        return [f for f in _tex_scan(get_project(lib, entry.project)).files if f.exists()]
    return entry.docs()


def cited_versions(lib: Library, keys: Sequence[str] | None = None) -> dict[str, str | None]:
    """The current version of each cited draft (`tex:<hash>`) and software (`git:<commit>`)."""
    out: dict[str, str | None] = {}
    for key, entry in cited_entries(lib).items():
        if keys is not None and key not in keys:
            continue
        if entry.kind == "draft":
            try:
                out[key] = citedmod.tex_version(cited_sources(lib, key))
            except RuruError:
                out[key] = None
        else:
            out[key] = entry.git_version()
    return out


def cited_keys(lib: Library, name: str) -> list[str]:
    """The keys the project's claims cite, in order of first citation (not \\nocite-only ones)."""
    return list(dict.fromkeys(k for c in claims(lib, name) for k in c.keys))


def claims(lib: Library, name: str) -> list[claimsmod.Claim]:
    """The project's citing sentences, file by file (main files first, then what they \\input)."""
    return claims_and_files(lib, name)[0]


def claims_and_files(lib: Library, name: str) -> tuple[list[claimsmod.Claim], list[Path]]:
    """`claims`, and every .tex file read for them (the TUI caches on their mtimes)."""
    files = _tex_scan(get_project(lib, name)).files
    return [c for f in files for c in claimsmod.extract(f)], files


def record_verdicts(lib: Library, name: str, records: Sequence[dict[str, Any]], *,
                    by: str | None = None, verify: bool = True) -> list[claimsmod.Verdict]:
    """Record verdicts on claims the .tex makes now, one commit. Each record: `claim`, `key`,
    `verdict`, and optionally `from` (pdf, the default; abstract; other), `page`, `quote`, `note`,
    `verify` (false: don't look for the quote in the PDF). A quote from a PDF whose text can be read
    must be in it. Every record is checked before anything is written."""
    if not records:
        raise UsageError("no verdicts given")
    current = {c.id: c for c in claims(lib, name)}
    entries = cited_entries(lib)
    versions = cited_versions(lib, [str(r.get("key", "")) for r in records])
    made = []
    for r in records:
        claim = current.get(str(r.get("claim", "")))
        if claim is None:
            raise UsageError(f"no claim {r.get('claim')!r} in {name}'s tex: `ruru claims {name}` lists them")
        key = str(r.get("key", ""))
        entry = entries.get(key)
        source = str(r.get("from") or (entry.kind if entry else "pdf"))
        if source in citedmod.KINDS and (entry is None or entry.kind != source):
            raise UsageError(f"--from {source} is for a cited {source}: `ruru cited add {key} --{source} …`")
        if entry and source == "pdf":
            raise UsageError(f"{key} is a cited {entry.kind}, with no PDF: say --from {entry.kind}")
        quote = r.get("quote")
        if quote and source == "pdf" and verify and r.get("verify") is not False:
            check = fulltext.check_quote(lib, key, str(quote))
            if check.status == "missing":
                raise UsageError(f"{key}: that quote is not in its PDF; copy it exactly from the paper "
                                 "(or --no-verify if the text extraction is at fault)")
            if check.status == "unavailable":
                print(f"ruru: warning: {key}: quote not checked against the PDF ({check.why})", file=sys.stderr)
        made.append(claimsmod.make(
            claim, key, str(r.get("verdict", "")), source=source, page=r.get("page"),
            quote=r.get("quote"), note=r.get("note"), by=r.get("by") or by,
            has_pdf=lib.exists(key) and lib.has_pdf(key),
            version=versions.get(key) if source in citedmod.KINDS else None))
    verdicts = claimsmod.load(lib.root, name)
    verdicts.update({(v.claim, v.key): v for v in made})
    summary = summary_of([f"{v.key}:{v.verdict}" for v in made])
    with lib.transaction(f"ruru: verdict {name} {summary}" + (f" ({by})" if by else "")) as tx:
        tx.write(claimsmod.path_for(lib.root, name), claimsmod.dumps(verdicts))
    return made


def drop_verdicts(lib: Library, name: str, claim: str, keys: Sequence[str] = ()) -> list[claimsmod.Verdict]:
    """Forget the verdicts on one claim (all its keys, or `keys`), current or stale."""
    get_project(lib, name)
    verdicts = claimsmod.load(lib.root, name)
    gone = [v for (c, k), v in verdicts.items() if c == claim and (not keys or k in keys)]
    if not gone:
        raise UsageError(f"no verdicts on claim {claim!r}" + (f" for {', '.join(keys)}" if keys else ""))
    for v in gone:
        del verdicts[(v.claim, v.key)]
    with lib.transaction(f"ruru: verdict {name} drop {claim}") as tx:
        tx.write(claimsmod.path_for(lib.root, name), claimsmod.dumps(verdicts))
    return gone
