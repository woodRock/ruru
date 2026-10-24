"""Undo: ruru's own commits are reverted by new commits, so history keeps both the change and its undoing."""

from __future__ import annotations

from dataclasses import dataclass

from ruru import gitops
from ruru.errors import NothingMatched, UsageError
from ruru.fsutil import library_lock
from ruru.library import Library

PREFIX = "ruru:"


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    message: str
    files: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {"sha": self.sha, "date": self.date, "message": self.message, "files": list(self.files)}


def undoable(lib: Library, limit: int = 10) -> list[Commit]:
    """The newest commits `undo` would revert, in order: ruru's, up to the first that is not (or a merge)."""
    out = []
    for c in gitops.commits(lib.root, limit):
        if not c["message"].startswith(PREFIX) or c["parents"] != "1":
            break
        out.append(Commit(c["sha"], c["date"], c["message"]))
    return out


def undo(lib: Library, count: int = 1, *, dry_run: bool = False) -> list[tuple[Commit, str | None]]:
    """Revert the last `count` commits ruru made. Returns (the commit, the sha of its revert) for each;
    nothing is changed with `dry_run`. Raises NothingMatched if the newest commit is not ruru's."""
    if not gitops.is_repo(lib.root):
        raise UsageError(f"{lib.root} is not a git repository: there is no history to undo")
    if count < 1:
        raise UsageError("count must be at least 1")
    with library_lock(lib.root):
        targets = [Commit(c.sha, c.date, c.message, tuple(gitops.changed_files(lib.root, c.sha)))
                   for c in undoable(lib, count)]
        if not targets:
            raise NothingMatched("the newest commit was not made by ruru; nothing to undo "
                                 "(`git log` shows what is there)")
        if len(targets) < count:
            raise NothingMatched(f"only the newest {len(targets)} commit(s) are ruru's; "
                                 f"`ruru undo -n {len(targets)}` undoes those")
        if dry_run:
            return [(c, None) for c in targets]
        for c in targets:
            if dirty := gitops.dirty_among(lib.root, c.files):
                raise UsageError(f"uncommitted changes in {', '.join(dirty[:3])}: commit or discard them first")
        done: list[tuple[Commit, str | None]] = []
        for c in targets:
            ok, detail = gitops.revert(lib.root, c.sha, f"{PREFIX} undo {c.message.removeprefix(PREFIX).strip()}")
            if not ok:
                raise UsageError(f"could not undo {c.message!r}: {detail}")
            done.append((c, detail))
        return done
