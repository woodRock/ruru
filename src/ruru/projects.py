"""Paper repos: `ruru cite` writes a managed block into a project's .bib, `ruru check` audits it.

A project (`[projects.<name>]` in ruru.toml) is a paper repository, e.g. a local clone of an
Overleaf project: `path`, `bib` (relative to path), `tex` (main files, relative to path) and an
optional `bib_encoding`. ruru writes only between the markers below and never touches entries
outside them. ruru never commits in the paper repo; that is Jesse's.
"""

from __future__ import annotations

import itertools
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruru import tomledit
from ruru.bibtex import ENCODINGS, parse_bibtex, to_bibtex
from ruru.errors import UsageError
from ruru.model import same_paper
from ruru.search import NO_PROJECT

BEGIN = "% >>> ruru managed — do not edit inside this block (ruru cite / ruru check --fix)"
END = "% <<< ruru managed"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass
class Project:
    name: str
    path: Path
    bib: str
    tex: list[str] = field(default_factory=list)
    bib_encoding: str | None = None

    @property
    def bib_path(self) -> Path:
        return self.path / self.bib

    @property
    def tex_paths(self) -> list[Path]:
        return [self.path / t for t in self.tex]

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> Project:
        if not _NAME.match(name):
            raise UsageError(f"project name {name!r}: use letters, digits, '-' and '_'")
        if name == NO_PROJECT:
            raise UsageError(f"{NO_PROJECT!r} can't be a project name: `-p {NO_PROJECT}` means in no project")
        if not d.get("path") or not d.get("bib"):
            raise UsageError(f"project {name!r} needs path and bib")
        tex = d.get("tex") or []
        if d.get("bib_encoding") not in (None, *ENCODINGS):
            raise UsageError(f"project {name!r}: bib_encoding must be 'ascii' or 'utf8'")
        return cls(name, Path(str(d["path"])).expanduser(), str(d["bib"]),
                   [tex] if isinstance(tex, str) else [str(t) for t in tex], d.get("bib_encoding"))

    def to_dict(self, raw_path: str | None = None) -> dict[str, Any]:
        return {"path": raw_path or str(self.path), "bib": self.bib, "tex": self.tex,
                "bib_encoding": self.bib_encoding}


def load(settings_projects: dict[str, dict[str, Any]]) -> dict[str, Project]:
    return {name: Project.from_dict(name, d) for name, d in settings_projects.items()}


def add_to_toml(text: str, name: str, values: dict[str, Any]) -> str:
    return tomledit.append_table(text, f"[projects.{name}]", values)


def remove_from_toml(text: str, name: str) -> str:
    try:
        return tomledit.remove_table(text, f"[projects.{name}]")
    except KeyError:
        raise UsageError(f"no project named {name!r}") from None


# --- .bib files and the managed block ------------------------------------------------------------

_ENTRY = re.compile(r"^[ \t]*@[ \t]*(\w+)[ \t]*[{(][ \t]*([^,\s{}()]+)[ \t]*,", re.M)
_NOT_ENTRIES = {"comment", "string", "preamble"}


def entry_keys(text: str) -> list[str]:
    return [m.group(2) for m in _ENTRY.finditer(text) if m.group(1).lower() not in _NOT_ENTRIES]


@dataclass
class BibFile:
    before: str
    inside: str
    after: str
    has_block: bool

    @classmethod
    def parse(cls, text: str) -> BibFile:
        begin = re.search(r"^% >>> ruru managed.*$", text, re.M)
        if not begin:
            return cls(text, "", "", False)
        end = re.search(r"^% <<< ruru managed.*$", text[begin.end():], re.M)
        if not end:
            raise UsageError("the bib file has a '% >>> ruru managed' line but no '% <<< ruru managed' line")
        stop = begin.end() + end.start()
        after = text[begin.end() + end.end():]
        return cls(text[:begin.start()], text[begin.end():stop].strip("\n"), after.removeprefix("\n"), True)

    @property
    def outside_keys(self) -> list[str]:
        return entry_keys(self.before + "\n" + self.after)

    @property
    def managed(self) -> dict[str, str]:
        """key -> entry text, for the entries in the block."""
        starts = [m.start() for m in _ENTRY.finditer(self.inside)] + [len(self.inside)]
        out = {}
        for a, b in itertools.pairwise(starts):
            chunk = self.inside[a:b].strip("\n") + "\n"
            keys = entry_keys(chunk)
            if keys:
                out[keys[0]] = chunk
        return out

    def render(self, entries: dict[str, str]) -> str:
        block = BEGIN + "\n" + "\n".join(entries[k].rstrip("\n") + "\n" for k in sorted(entries)) + END + "\n"
        if self.has_block:
            return self.before + block + self.after
        before = self.before.rstrip("\n")
        return (before + "\n\n" if before else "") + block


# --- .tex citations ------------------------------------------------------------------------------

# \cite, \citep, \citet*, \parencite, \textcite, \autocite, \citeauthor, … with up to two optional
# [..] arguments, then {key1, key2}. \nocite{*} cites everything.
_CITE = re.compile(r"\\(?:[A-Za-z]*cite[A-Za-z]*)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]*)\}")
_INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")
_BIBS = re.compile(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def strip_comments(tex: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", tex)


@dataclass
class TexScan:
    keys: list[str] = field(default_factory=list)  # in order of first citation
    files: list[Path] = field(default_factory=list)
    bibs: list[Path] = field(default_factory=list)
    nocite_all: bool = False
    missing_files: list[Path] = field(default_factory=list)


def scan_tex(mains: list[Path]) -> TexScan:
    scan = TexScan()
    seen_keys: set[str] = set()

    def visit(path: Path, root: Path) -> None:
        if path.suffix != ".tex" and not path.exists():
            path = path.with_name(path.name + ".tex")
        if path in scan.files:
            return
        if not path.is_file():
            scan.missing_files.append(path)
            return
        scan.files.append(path)
        tex = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for m in _BIBS.finditer(tex):
            for name in m.group(1).split(","):
                bib = root / name.strip()
                scan.bibs.append(bib if bib.suffix == ".bib" else bib.with_name(bib.name + ".bib"))
        # Citations and inputs in document order, so the key order is the reading order.
        events = sorted([(m.start(), "cite", m) for m in _CITE.finditer(tex)]
                        + [(m.start(), "input", m) for m in _INPUT.finditer(tex)], key=lambda e: e[0])
        for _, kind, m in events:
            if kind == "input":
                visit(root / m.group(1).strip(), root)  # LaTeX resolves inputs from the main file's directory
                continue
            for key in (k.strip() for k in m.group(1).split(",")):
                if key == "*":
                    scan.nocite_all = True
                elif key and key not in seen_keys:
                    seen_keys.add(key)
                    scan.keys.append(key)

    for main in mains:
        visit(main, main.parent)
    return scan


# --- check ---------------------------------------------------------------------------------------

@dataclass
class Report:
    project: str | None
    tex: list[str]
    bib: str | None
    cited: int
    missing: list[dict[str, Any]] = field(default_factory=list)  # cited, not in the bib file
    unused: list[str] = field(default_factory=list)  # in the managed block, never cited
    kept_unused: list[str] = field(default_factory=list)  # tagged with the project, never cited
    stale: list[str] = field(default_factory=list)  # managed entry differs from the library
    duplicates: list[list[str]] = field(default_factory=list)  # same paper, different keys
    warnings: list[str] = field(default_factory=list)

    @property
    def issues(self) -> int:
        return (len(self.missing) + len(self.unused) + len(self.kept_unused) + len(self.stale)
                + len(self.duplicates))

    def to_json(self) -> dict[str, Any]:
        return {
            "project": self.project, "tex": self.tex, "bib": self.bib, "cited": self.cited,
            "issues": {"missing": self.missing, "unused": self.unused, "kept_unused": self.kept_unused,
                       "stale": self.stale, "duplicates": self.duplicates},
            "issue_count": self.issues, "warnings": self.warnings,
        }


def duplicate_keys(bib_text: str) -> list[list[str]]:
    try:
        entries = parse_bibtex(bib_text)
    except Exception as e:  # bibtexparser raises assorted errors on odd input
        print(f"ruru: warning: could not parse the bib file for duplicates: {e}", file=sys.stderr)
        return []
    pairs = []
    for i, (ka, a) in enumerate(entries):
        for kb, b in entries[i + 1:]:
            if ka != kb and same_paper(a, b):
                pairs.append(sorted([ka, kb]))
    return pairs


def bib_encoding(project: Project | None, default: str) -> str:
    return (project.bib_encoding if project else None) or default


def entry_for(library: Any, key: str, encoding: str) -> str:
    return to_bibtex(library.get(key), encoding)
