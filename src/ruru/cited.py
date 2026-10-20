"""Cited work outside the library: Jesse's drafts and software (`[cited.<key>]` in ruru.toml).

The library holds published papers. A paper repo can also cite a draft (another ruru project,
whose .tex is the full text) or software (a local git repo, described by its CITATION.cff). Claims
citing them are checked like any other, against the draft's .tex or the repo's README; the verdict
records a version (a hash of the draft's .tex, the repo's commit), and when that version moves on
the verdict shows as outdated.

    [cited.chromadcnn]
    kind = "draft"
    project = "chroma-dcnn"

    [cited.warpalign]
    kind = "software"
    path = "~/Desktop/warp-align"
    url = "https://github.com/woodRock/warp-align"   # optional: CITATION.cff's repository-code
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ruru.bibtex import escape
from ruru.errors import UsageError

KINDS = ("draft", "software")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:.-]*$")
DOCS = ("README.md", "README.rst", "README", "CITATION.cff")  # what a software verdict is read against


@dataclass
class Cited:
    key: str
    kind: str
    project: str | None = None  # draft: the ruru project whose .tex is the draft
    path: Path | None = None  # software: the local repo
    url: str | None = None

    @classmethod
    def from_dict(cls, key: str, d: dict[str, Any]) -> Cited:
        if not _KEY.match(key):
            raise UsageError(f"cited key {key!r}: use letters, digits and - _ : .")
        kind = d.get("kind")
        if kind not in KINDS:
            raise UsageError(f"[cited.{key}]: kind must be {' or '.join(KINDS)}")
        if kind == "draft" and not d.get("project"):
            raise UsageError(f"[cited.{key}]: a draft needs project = \"<ruru project>\"")
        if kind == "software" and not (d.get("path") or d.get("url")):
            raise UsageError(f"[cited.{key}]: software needs path (the local repo) or url")
        path = Path(str(d["path"])).expanduser() if d.get("path") else None
        return cls(key, kind, d.get("project"), path, d.get("url"))

    def to_dict(self, raw_path: str | None = None) -> dict[str, Any]:
        return {"kind": self.kind, "project": self.project,
                "path": raw_path or (str(self.path) if self.path else None), "url": self.url}

    def citation(self) -> dict[str, Any]:
        """The repo's CITATION.cff, or {} (no path, no file, or not YAML)."""
        cff = self.path / "CITATION.cff" if self.path else None
        if not cff or not cff.is_file():
            return {}
        try:
            data = yaml.safe_load(cff.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def link(self) -> str | None:
        return self.url or self.citation().get("repository-code") or self.citation().get("url")

    def docs(self) -> list[Path]:
        """Software: the files a verdict is read against (README, CITATION.cff)."""
        return [self.path / name for name in DOCS if self.path and (self.path / name).is_file()]

    def git_version(self) -> str | None:
        """Software: `git:<commit>` of the repo's HEAD (`-dirty` with uncommitted changes), or None."""
        if not self.path or not (self.path / ".git").exists():
            return None
        def git(*a: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(["git", "-C", str(self.path), *a], capture_output=True, text=True)
        head = git("rev-parse", "--short=12", "HEAD")
        if head.returncode != 0:
            return None
        dirty = git("status", "--porcelain", "--untracked-files=no").stdout.strip()
        return f"git:{head.stdout.strip()}" + ("-dirty" if dirty else "")


def load(settings_cited: dict[str, dict[str, Any]]) -> dict[str, Cited]:
    return {key: Cited.from_dict(key, d) for key, d in settings_cited.items()}


def tex_version(files: list[Path]) -> str:
    """Draft: `tex:<hash>` of its .tex files' contents, so any edit (committed or not) shows."""
    h = hashlib.sha1()
    for f in sorted(files):
        h.update(str(f.name).encode() + b"\0" + (f.read_bytes() if f.exists() else b"") + b"\0")
    return f"tex:{h.hexdigest()[:10]}"


def bibtex(entry: Cited, encoding: str = "ascii") -> str:
    """Software as a classic-BibTeX @misc (no @software in BibTeX), from its CITATION.cff:
    authors, title, version, year, URL and DOI."""
    if entry.kind != "software":
        raise UsageError(f"{entry.key} is a draft: write its entry by hand (@unpublished), "
                         "or `ruru add` its DOI once it is published")
    cff = entry.citation()
    if not cff.get("title"):
        raise UsageError(f"{entry.key}: no CITATION.cff with a title in {entry.path or 'its path'}")
    authors = " and ".join(_comma(a) for a in cff.get("authors") or [])
    title = str(cff["title"])
    if ":" in title:  # keep the software's name as written: "warpalign: …"
        name, rest = title.split(":", 1)
        title = "{" + escape(name, encoding) + "}:" + escape(rest, encoding)
    else:
        title = "{" + escape(title, encoding) + "}"
    version = cff.get("version")
    year = str(cff.get("date-released") or "")[:4] or None
    link = entry.link
    how = "Software" + (f", version {version}" if version else "") + (f". [Online]. Available: \\url{{{link}}}"
                                                                     if link else "")
    fields = [("author", escape(authors, encoding)), ("title", title), ("howpublished", how), ("year", year),
              ("doi", cff.get("doi"))]
    body = ",\n".join(f"  {k:<12} = {{{v}}}" for k, v in fields if v)
    return f"@misc{{{entry.key},\n{body}\n}}\n"


def _comma(author: Any) -> str:
    """CFF person → `Family, Given` (an entity's `name` as is)."""
    if not isinstance(author, dict):
        return str(author)
    if "family-names" in author:
        given = author.get("given-names")
        particle = author.get("name-particle")
        family = " ".join(x for x in (particle, author["family-names"]) if x)
        return f"{family}, {given}" if given else family
    return str(author.get("name", ""))
