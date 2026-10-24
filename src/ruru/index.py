"""A derived cache in `.ruru/index.sqlite`: word counts for ranking, and the text of PDFs.

The files stay the only source of truth. Every row is keyed on the file it came from and the
file's size and modification time, so a row is used only while that file is unchanged; anything
else is recomputed. Deleting the index loses nothing but time (`ruru index rebuild`). If the
database can't be opened or written (read-only disk, a corrupt file, a writer holding it), every
call still answers, just without the cache.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ruru import rank
from ruru.text import fold, words

SCHEMA = 1
VERSION = SCHEMA * 1000 + rank.VERSION  # a change to either rebuilds the index
SNIPPET_CHARS = 90

Counts = dict[str, int]
Doc = tuple[str, Path, Callable[[], Counts]]  # (id, the file it comes from, how to count its words)


def signature(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns}:{st.st_size}"


@dataclass
class PdfSync:
    extracted: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # key -> why
    current: int = 0  # already in the index and unchanged


@dataclass
class Status:
    path: Path
    exists: bool
    size: int
    papers: int
    inbox: int
    pdfs: int
    pdfs_failed: int
    fts: bool

    def to_json(self) -> dict[str, object]:
        return {"path": str(self.path), "exists": self.exists, "bytes": self.size, "papers": self.papers,
                "inbox": self.inbox, "pdfs": self.pdfs, "pdfs_failed": self.pdfs_failed,
                "full_text_search": self.fts}


class Index:
    def __init__(self, root: Path, *, fts: bool = True) -> None:
        self.root = root
        self.path = root / ".ruru" / "index.sqlite"
        self._db: sqlite3.Connection | None = None
        self._opened = False
        self._want_fts = fts  # False: don't use SQLite's FTS5 even if it is there (tests; odd builds)
        self.fts = False

    # --- connection ------------------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection | None:
        """The open database, or None when the cache can't be used (then nothing is cached)."""
        if self._opened:
            return self._db
        self._opened = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = self._open()
        except (sqlite3.Error, OSError):
            self._db = None
            with contextlib.suppress(OSError):  # a corrupt file: start again next time
                self.path.unlink(missing_ok=True)
        return self._db

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] != VERSION:
                self._reset(db)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS terms(kind TEXT, id TEXT, sig TEXT, counts TEXT, PRIMARY KEY(kind, id));
                CREATE TABLE IF NOT EXISTS pdf(key TEXT PRIMARY KEY, sig TEXT, error TEXT, text TEXT);
            """)
            try:
                if not self._want_fts:
                    raise sqlite3.OperationalError("FTS5 not wanted")
                had = db.execute("SELECT 1 FROM sqlite_master WHERE name='pdf_fts'").fetchone() is not None
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pdf_fts USING fts5("
                           "text, content='pdf', content_rowid='rowid', "
                           "tokenize='porter unicode61 remove_diacritics 2')")
                if not had:  # text stored while FTS was off or missing must be searchable now
                    db.execute("INSERT INTO pdf_fts(pdf_fts) VALUES('rebuild')")
                self.fts = True
            except sqlite3.OperationalError:  # SQLite built without FTS5: search scans the table instead
                self.fts = False
            db.commit()
        except sqlite3.Error:
            db.close()
            raise
        return db

    @staticmethod
    def _reset(db: sqlite3.Connection) -> None:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if not name.startswith("sqlite_"):
                db.execute(f'DROP TABLE IF EXISTS "{name}"')  # also the FTS shadow tables
        db.execute(f"PRAGMA user_version = {VERSION}")

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
        self._db, self._opened = None, False

    def clear(self) -> None:
        """Delete the cache (it is rebuilt on demand)."""
        self.close()
        for suffix in ("", "-journal", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                Path(f"{self.path}{suffix}").unlink(missing_ok=True)

    # --- word counts -----------------------------------------------------------------------------

    def counts(self, kind: str, docs: Sequence[Doc]) -> dict[str, Counts]:
        """Word counts for each document, from the cache while its file is unchanged."""
        db = self._connect()
        cached: dict[str, tuple[str, str]] = {}
        if db is not None:
            try:
                rows = db.execute("SELECT id, sig, counts FROM terms WHERE kind=?", (kind,))
                cached = {i: (s, c) for i, s, c in rows}
            except sqlite3.Error:
                db = None
        out: dict[str, Counts] = {}
        fresh: list[tuple[str, str, str, str]] = []
        for doc_id, path, count in docs:
            sig = signature(path)
            hit = cached.get(doc_id)
            if hit is not None and sig is not None and hit[0] == sig:
                out[doc_id] = json.loads(hit[1])
                continue
            out[doc_id] = count()
            if sig is not None:
                fresh.append((kind, doc_id, sig, json.dumps(out[doc_id], separators=(",", ":"))))
        gone = [(kind, i) for i in cached if i not in out]
        if db is not None and (fresh or gone):
            try:
                with db:
                    db.executemany("DELETE FROM terms WHERE kind=? AND id=?", gone)
                    db.executemany("INSERT OR REPLACE INTO terms(kind, id, sig, counts) VALUES (?,?,?,?)", fresh)
            except sqlite3.Error:
                pass  # another process holds the database: the answer is right, only uncached
        return out

    # --- PDF text --------------------------------------------------------------------------------

    def pending_pdfs(self, pdfs: Mapping[str, Path]) -> list[str]:
        """The keys whose PDF text is not in the index yet (new or changed since it was read)."""
        db = self._connect()
        have: dict[str, str] = {}
        if db is not None:
            with contextlib.suppress(sqlite3.Error):
                have = dict(db.execute("SELECT key, sig FROM pdf"))
        return [k for k in sorted(pdfs) if have.get(k) != signature(pdfs[k])]

    def sync_pdfs(self, pdfs: Mapping[str, Path], extract: Callable[[Path], str], *, retry: bool = False,
                  progress: Callable[[str], None] | None = None, forget: bool = True) -> PdfSync:
        """Extract the text of each PDF that is new or changed (`pdfs`: key -> path); with `forget`, drop
        the text of PDFs no longer listed. A PDF that failed is not tried again until it changes (or `retry`)."""
        result = PdfSync()
        db = self._connect()
        if db is None:
            result.failed = dict.fromkeys(pdfs, f"cannot open {self.path}")
            return result
        try:
            have = {k: (s, e) for k, s, e in db.execute("SELECT key, sig, error FROM pdf")}
            for key in sorted(have.keys() - pdfs.keys()) if forget else []:
                self._drop_pdf(db, key)
            for key in sorted(pdfs):
                sig = signature(pdfs[key])
                known = have.get(key)
                if sig is not None and known is not None and known[0] == sig and not (retry and known[1]):
                    result.current += 1
                    continue
                if progress:
                    progress(key)
                try:
                    text, error = extract(pdfs[key]), ""
                except Exception as e:
                    text, error = "", str(e) or type(e).__name__
                    result.failed[key] = error
                self._put_pdf(db, key, sig or "", text, error)
                if not error:
                    result.extracted.append(key)
            db.commit()
        except sqlite3.Error as e:
            result.failed["*"] = str(e)
        return result

    def _drop_pdf(self, db: sqlite3.Connection, key: str) -> None:
        old = db.execute("SELECT rowid, text FROM pdf WHERE key=?", (key,)).fetchone()
        if old is None:
            return
        if self.fts:
            db.execute("INSERT INTO pdf_fts(pdf_fts, rowid, text) VALUES('delete', ?, ?)", (old[0], old[1]))
        db.execute("DELETE FROM pdf WHERE key=?", (key,))

    def _put_pdf(self, db: sqlite3.Connection, key: str, sig: str, text: str, error: str) -> None:
        self._drop_pdf(db, key)
        cur = db.execute("INSERT INTO pdf(key, sig, error, text) VALUES (?,?,?,?)", (key, sig, error, text))
        if self.fts:
            db.execute("INSERT INTO pdf_fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, text))

    def pdf_text(self, key: str) -> str | None:
        """The stored text of a paper's PDF; None if it was not extracted (or failed)."""
        db = self._connect()
        if db is None:
            return None
        try:
            row = db.execute("SELECT text, error FROM pdf WHERE key=?", (key,)).fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row is not None and not row[1] else None

    def pdf_error(self, key: str) -> str | None:
        """Why the PDF's text could not be read, if that was tried and failed."""
        db = self._connect()
        if db is None:
            return None
        try:
            row = db.execute("SELECT error FROM pdf WHERE key=?", (key,)).fetchone()
        except sqlite3.Error:
            return None
        return row[0] or None if row is not None else None

    def search_pdfs(self, query: str, limit: int = 500) -> list[tuple[str, str]]:
        """(key, snippet) for each PDF containing every word of `query`, best match first."""
        terms = words(query)
        db = self._connect()
        if not terms or db is None:
            return []
        try:
            if self.fts:
                match = " ".join(f'"{t}"' for t in terms)
                rows = db.execute(
                    "SELECT pdf.key, snippet(pdf_fts, 0, '', '', ' … ', 16) FROM pdf_fts "
                    "JOIN pdf ON pdf.rowid = pdf_fts.rowid WHERE pdf_fts MATCH ? AND pdf.error = '' "
                    "ORDER BY rank LIMIT ?", (match, limit)).fetchall()
                return [(k, " ".join(s.split())) for k, s in rows]
            hits = []
            for key, text in db.execute("SELECT key, text FROM pdf WHERE error = ''"):
                folded = fold(text)
                if all(t in folded for t in terms):
                    hits.append((key, _snippet(text, folded, terms[0])))
            return sorted(hits)[:limit]
        except sqlite3.Error:
            return []

    # --- reporting -------------------------------------------------------------------------------

    def status(self) -> Status:
        db = self._connect()
        size = self.path.stat().st_size if self.path.exists() else 0
        if db is None:
            return Status(self.path, False, size, 0, 0, 0, 0, False)
        n = lambda sql: db.execute(sql).fetchone()[0]
        return Status(
            self.path, True, size,
            n("SELECT count(*) FROM terms WHERE kind='paper'"), n("SELECT count(*) FROM terms WHERE kind='inbox'"),
            n("SELECT count(*) FROM pdf WHERE error=''"), n("SELECT count(*) FROM pdf WHERE error<>''"), self.fts)


def _snippet(text: str, folded: str, term: str) -> str:
    """A bit of `text` around the first occurrence of `term` (positions in `folded` match `text` only
    approximately when accents were folded, which is fine for a preview)."""
    at = max(0, folded.find(term))
    return " ".join(text[max(0, at - SNIPPET_CHARS // 2):at + SNIPPET_CHARS].split())


def warn_failures(failed: Mapping[str, str]) -> None:
    for key, why in sorted(failed.items()):
        print(f"ruru: warning: {key}: no text from the PDF: {why}", file=sys.stderr)
