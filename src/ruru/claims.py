"""Cited claims: the sentences of a project's .tex that cite library papers, and verdicts on them.

`ruru claims PROJECT` lists every sentence with a citation, under a stable id (a hash of the
sentence with its cite commands written as `[@a; @b]`), so rewording a sentence or changing what
it cites gives it a new id and leaves the old verdict stale. Whoever reads the cited paper (Jesse,
or an agent) records a verdict per (claim, key) with `ruru verdict`; verdicts live in the library
at `claims/<project>.json`. ruru never edits the .tex.

Verdicts:
  supported     the cited paper says it: needs a verbatim quote (and a page, from a PDF)
  partial       it says something weaker or narrower: needs the quote and a note on the gap
  unsupported   it doesn't say this: needs a note
  not-found     read it, couldn't find the passage either way
  background    a general attribution ("see [x]"); nothing specific to check

A verdict on a draft or software (see `ruru.cited`) records its version; when the draft's .tex or
the repo's commit has changed since, the key's state is `outdated` until it is judged again.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ruru.errors import UsageError
from ruru.projects import _CITE, strip_comments

VERDICTS = ("supported", "partial", "unsupported", "not-found", "background")
NEEDS_QUOTE = {"supported", "partial"}
NEEDS_NOTE = {"partial", "unsupported"}
SOURCES = ("pdf", "abstract", "draft", "software", "other")  # what the verdict rests on
MARKS = {"supported": ("✓", "green"), "partial": ("~", "yellow"), "unsupported": ("✗", "red"),
         "not-found": ("?", "yellow"), "background": ("·", "dim")}
UNCHECKED = "○"
OUTDATED = "↻"  # a verdict on a draft or software that has changed since
OWN = "★"  # Jesse's own work: a label, checked like anything else
STATES = (*VERDICTS, "unchecked", "outdated")  # what a cited key can be
FILTERS = (*STATES, "own")  # `own`: the key is Jesse's own work, whatever its state
PROBLEMS = ("unsupported", "partial", "not-found")
DIR = "claims"

# Words whose trailing full stop doesn't end a sentence.
_ABBREVIATIONS = {"e.g", "i.e", "al", "cf", "fig", "figs", "eq", "eqs", "sec", "secs", "tab", "vs", "etc",
                  "no", "vol", "pp", "ch", "ref", "refs", "approx", "resp"}
_HEADING = re.compile(r"\\(?:part|chapter|(?:sub)*section|(?:sub)?paragraph)\*?\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}")
_BREAK = re.compile(r"\n[ \t]*\n|\\item\b|\\(?:begin|end)\s*\{[^}]*\}|\\caption\s*\{|\\maketitle\b")
_STOP = re.compile(r"[.!?](?=\s+[A-Z\\(]|\s*$)")
_LABEL = re.compile(r"\\label\s*\{[^}]*\}")
_NOT_CLAIMS = {"nocite"}  # \nocite puts a key in the bibliography without saying anything


@dataclass
class Claim:
    id: str
    file: Path
    line: int
    keys: list[str]
    text: str  # the sentence, whitespace collapsed, each cite command as [@a; @b]


@dataclass
class Verdict:
    claim: str
    key: str
    verdict: str
    source: str
    page: str | None
    quote: str | None
    note: str | None
    by: str | None
    date: str
    text: str  # the claim's text when the verdict was given
    version: str | None = None  # draft (`tex:<hash>`) or software (`git:<commit>`) when judged

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def claim_id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def _cite_name(m: re.Match[str]) -> str:
    return m.group(0)[1:].split("{")[0].split("[")[0].rstrip("*").strip()


def _render(sentence: str) -> str:
    def cite(m: re.Match[str]) -> str:
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        return "[" + "; ".join(f"@{k}" for k in keys) + "]"
    return " ".join(_LABEL.sub("", _CITE.sub(cite, sentence)).split())


def _boundaries(tex: str) -> list[tuple[int, int]]:
    """Spans of text between which a sentence can't continue: breaks, headings, sentence ends."""
    spans = [(m.start(), m.end()) for m in _BREAK.finditer(tex)]
    spans += [(m.start(), m.end()) for m in _HEADING.finditer(tex)]
    for m in _STOP.finditer(tex):
        word = re.search(r"([A-Za-z.]+)$", tex[max(0, m.start() - 12):m.start()])
        w = word.group(1).lower().strip(".") if word else ""
        if m.group(0) == "." and (w in _ABBREVIATIONS or (len(w) == 1 and w.isalpha())):
            continue  # "et al.", "Fig.", an initial
        spans.append((m.end(), m.end()))  # the full stop belongs to the sentence before it
    return sorted(spans)


def _sentences(tex: str) -> list[tuple[int, int]]:
    out, start = [], 0
    for a, b in _boundaries(tex):
        if a >= start:
            out.append((start, a))
            start = b
    out.append((start, len(tex)))
    return out


def extract(path: Path) -> list[Claim]:
    """The sentences of one .tex file that cite something, in document order (no \\input following:
    `claims_for` walks the files `scan_tex` found). Comments are stripped, lines keep their numbers,
    and in a file with \\begin{document} only the body counts."""
    tex = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    body = re.search(r"\\begin\s*\{document\}", tex)
    offset = body.end() if body else 0
    cites = [m for m in _CITE.finditer(tex, offset) if _cite_name(m) not in _NOT_CLAIMS]
    claims: list[Claim] = []
    for start, end in _sentences(tex):
        inside = [m for m in cites if start <= m.start() < end]
        if not inside:
            continue
        keys: list[str] = []
        for m in inside:
            keys += [k for k in (k.strip() for k in m.group(1).split(",")) if k and k != "*" and k not in keys]
        if not keys:
            continue
        text = _render(tex[start:end])
        line = tex.count("\n", 0, inside[0].start()) + 1
        claims.append(Claim(claim_id(text), path, line, keys, text))
    return claims


def where(claim: Claim, mains: list[Path]) -> str:
    """The claim's file, relative to the directory of the main .tex it belongs under."""
    base = next((m.parent for m in mains if claim.file.is_relative_to(m.parent)), None)
    return str(claim.file.relative_to(base) if base else claim.file)


def state(claim: Claim, key: str, verdicts: dict[tuple[str, str], Verdict],
          versions: dict[str, str | None] | None = None) -> str:
    """A cited key's verdict; `outdated` if it was given on a version of a draft or software that
    has changed since (`versions`: key → its current version); else `unchecked`."""
    v = verdicts.get((claim.id, key))
    if v is None:
        return "unchecked"
    now = (versions or {}).get(key)
    return "outdated" if v.version and now and now != v.version else v.verdict


def matches(claim: Claim, states: set[str], verdicts: dict[tuple[str, str], Verdict],
            versions: dict[str, str | None], own: set[str]) -> bool:
    """Does some cited key have one of `states` (FILTERS: `own` for Jesse's own work)?"""
    return any(state(claim, k, verdicts, versions) in states or ("own" in states and k in own)
               for k in claim.keys)


@dataclass
class Summary:
    claims: int
    keys: list[str]  # distinct cited keys, in order of first citation
    counts: dict[str, int]  # every STATES entry over (claim, key) pairs, plus `own`: pairs citing own work
    no_pdf: list[str]  # in the library without a PDF
    not_in_library: list[str]  # neither in the library nor a cited draft or software
    problems: list[tuple[Claim, Verdict]] = field(default_factory=list)  # PROBLEMS verdicts


def summarise(claims: list[Claim], verdicts: dict[tuple[str, str], Verdict], own: set[str],
              exists: Callable[[str], bool], has_pdf: Callable[[str], bool],
              versions: dict[str, str | None] | None = None, cited: set[str] | None = None) -> Summary:
    """What `ruru claims --summary` and the TUI's project pane report."""
    pairs = [(c, k) for c in claims for k in c.keys]
    states = [state(c, k, verdicts, versions) for c, k in pairs]
    keys = list(dict.fromkeys(k for _, k in pairs))
    counts = {name: states.count(name) for name in STATES} | {"own": sum(k in own for _, k in pairs)}
    problems = [(c, v) for c, k in pairs if (v := verdicts.get((c.id, k))) and v.verdict in PROBLEMS]
    return Summary(len(claims), keys, counts, [k for k in keys if exists(k) and not has_pdf(k)],
                   [k for k in keys if not exists(k) and k not in (cited or set())], problems)


# --- verdicts ------------------------------------------------------------------------------------

def path_for(root: Path, project: str) -> Path:
    return root / DIR / f"{project}.json"


def load(root: Path, project: str) -> dict[tuple[str, str], Verdict]:
    path = path_for(root, project)
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {(r["claim"], r["key"]): Verdict(**r) for r in rows}


def dumps(verdicts: dict[tuple[str, str], Verdict]) -> str:
    rows = [verdicts[k].to_json() for k in sorted(verdicts)]
    return json.dumps(rows, ensure_ascii=False, indent=2) + "\n"


def make(claim: Claim, key: str, verdict: str, *, source: str = "pdf", page: str | None = None,
         quote: str | None = None, note: str | None = None, by: str | None = None,
         has_pdf: bool = True, today: date | None = None, version: str | None = None) -> Verdict:
    """A checked verdict: the quote and note rules above, and `pdf` only for a paper with one."""
    if key not in claim.keys:
        raise UsageError(f"claim {claim.id} cites {', '.join(claim.keys)}, not {key}")
    if verdict not in VERDICTS:
        raise UsageError(f"verdict must be one of {', '.join(VERDICTS)}")
    if source not in SOURCES:
        raise UsageError(f"--from must be one of {', '.join(SOURCES)}")
    quote, note = (quote or "").strip() or None, (note or "").strip() or None
    page = str(page or "").strip() or None
    if verdict in NEEDS_QUOTE and not quote:
        raise UsageError(f"{claim.id} {key}: '{verdict}' needs --quote, the cited paper's own words")
    if verdict in NEEDS_NOTE and not note:
        raise UsageError(f"{claim.id} {key}: '{verdict}' needs --note saying what the paper does say")
    if source == "pdf" and not has_pdf:
        raise UsageError(f"{key} has no PDF: read it first (`ruru pdf missing {key} --open`), "
                         "or say what the verdict rests on with --from abstract|other")
    return Verdict(claim.id, key, verdict, source, page, quote, note, by,
                   (today or date.today()).isoformat(), claim.text, version)
