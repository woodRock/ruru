"""The library: a git repository of `papers/<key>/paper.md` files plus settings.

Reads never lock: every write is atomic, so a reader sees either the old or the new file. Writes
go through `Library.transaction()`, which holds `.ruru/lock`, records every file it writes and
commits exactly those files when it ends (unless auto-commit is off).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ruru import gitops, model
from ruru.bibtex import to_bibtex
from ruru.config import SETTINGS_FILE, Settings, load_settings, settings_template
from ruru.errors import UnknownKey, UsageError
from ruru.fsutil import atomic_write, library_lock
from ruru.keys import base_key, unique_key
from ruru.model import Paper

GITIGNORE = """\
# PDFs stay next to each paper but are never committed.
*.pdf
/exports/
/.ruru/
.DS_Store
"""


@dataclass
class Transaction:
    library: Library
    message: str
    touched: list[Path] = field(default_factory=list)

    def write(self, path: Path, text: str) -> None:
        atomic_write(path, text)
        self.touched.append(path)

    def remove(self, path: Path) -> None:
        path.unlink(missing_ok=True)
        self.touched.append(path)
        with contextlib.suppress(OSError):
            path.parent.rmdir()  # an emptied inbox/<date>/ directory

    def save(self, paper: Paper) -> None:
        """Write paper.md and regenerate paper.bib from it."""
        d = self.library.paper_dir(paper.key)
        self.write(d / "paper.md", model.dumps(paper))
        self.write(d / "paper.bib", to_bibtex(paper, self.library.settings.bib_encoding))


class Library:
    def __init__(self, root: Path, settings: Settings, *, auto_commit: bool | None = None) -> None:
        self.root = root
        self.settings = settings
        self.auto_commit = settings.auto_commit if auto_commit is None else auto_commit

    @classmethod
    def open(cls, root: Path, *, auto_commit: bool | None = None) -> Library:
        return cls(root, load_settings(root), auto_commit=auto_commit)

    @classmethod
    def init(cls, root: Path, *, contact_email: str = "", bib_encoding: str = "ascii") -> Library:
        if (root / SETTINGS_FILE).exists():
            raise UsageError(f"{root} is already a ruru library")
        root.mkdir(parents=True, exist_ok=True)
        gitops.init(root)
        (root / "papers").mkdir(exist_ok=True)
        lib = cls(root, Settings(contact_email=contact_email, bib_encoding=bib_encoding))
        with lib.transaction("ruru: init") as tx:
            tx.write(root / SETTINGS_FILE, settings_template(contact_email, bib_encoding))
            if not (root / ".gitignore").exists():
                tx.write(root / ".gitignore", GITIGNORE)
            if not (root / "seen.jsonl").exists():
                tx.write(root / "seen.jsonl", "")
        return lib

    # --- reading ---------------------------------------------------------------------------------

    def paper_dir(self, key: str) -> Path:
        return self.root / "papers" / key

    def keys(self) -> list[str]:
        d = self.root / "papers"
        if not d.is_dir():
            return []
        return sorted(p.parent.name for p in d.glob("*/paper.md"))

    def exists(self, key: str) -> bool:
        return (self.paper_dir(key) / "paper.md").is_file()

    def get(self, key: str) -> Paper:
        path = self.paper_dir(key) / "paper.md"
        if not path.is_file():
            raise UnknownKey(key)
        try:
            return model.loads(path.read_text(encoding="utf-8"))
        except (model.FrontMatterError, ValueError) as e:
            raise UsageError(f"{path}: {e}") from None

    def papers(self) -> Iterator[Paper]:
        for key in self.keys():
            yield self.get(key)

    def has_pdf(self, key: str) -> bool:
        return (self.paper_dir(key) / "paper.pdf").is_file()

    def find_duplicate(self, paper: Paper) -> Paper | None:
        return next((p for p in self.papers() if model.same_paper(p, paper)), None)

    def check_projects(self, projects: list[str]) -> None:
        unknown = [p for p in projects if p not in self.settings.projects]
        if unknown:
            raise UsageError(
                f"unknown project(s): {', '.join(unknown)}; add one with `ruru project add NAME …`"
            )

    # --- writing ---------------------------------------------------------------------------------

    @contextlib.contextmanager
    def transaction(self, message: str = "ruru: update") -> Iterator[Transaction]:
        with library_lock(self.root):
            tx = Transaction(self, message)
            yield tx
            if tx.touched and self.auto_commit and gitops.is_repo(self.root):
                gitops.commit(self.root, tx.touched, tx.message)

    def add(self, tx: Transaction, paper: Paper, *, key: str | None = None) -> tuple[Paper, bool]:
        """Add `paper` with a new key (or `key`). Returns (paper, False) if it is already here."""
        existing = self.find_duplicate(paper)
        if existing:
            return existing, False
        taken = set(self.keys())
        if key:
            if key in taken:
                raise UsageError(f"key {key!r} is already taken")
            paper.key = key
        else:
            paper.key = unique_key(base_key(paper.authors, paper.year, paper.title), taken)
        paper.added = paper.added or model.today()
        tx.save(paper)
        return paper, True

    def set_status(self, paper: Paper, status: str, on: date | None = None) -> None:
        if status not in model.STATUSES:
            raise UsageError(f"status must be one of {', '.join(model.STATUSES)}")
        paper.status = status
        if status == "read":
            paper.read_on = paper.read_on or on or model.today()
