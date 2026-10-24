"""Auto-commit: after each change, commit only the files that command touched. Never push."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def is_repo(root: Path) -> bool:
    return (root / ".git").exists()


def init(root: Path) -> None:
    if not is_repo(root):
        _git(root, "init", "-q", "-b", "main")


def commit(root: Path, paths: Sequence[Path], message: str) -> bool:
    """Stage and commit exactly `paths`. False (and a warning on stderr) if git refused."""
    rel = sorted({str(p.resolve().relative_to(root.resolve())) for p in paths})
    present = [p for p in rel if (root / p).exists()]
    gone = [p for p in rel if not (root / p).exists()]
    if present:
        _git(root, "add", "-A", "--", *present)
    if gone:
        _git(root, "rm", "-q", "--cached", "--ignore-unmatch", "--", *gone)
    if _git(root, "diff", "--cached", "--quiet", "--", *rel).returncode == 0:
        return False  # nothing changed
    res = _git(root, "commit", "-q", "-m", message, "--", *rel)
    if res.returncode != 0:
        print(f"ruru: warning: git commit failed: {res.stderr.strip() or res.stdout.strip()}", file=sys.stderr)
        return False
    return True


def log(root: Path, limit: int = 50) -> list[dict[str, str]]:
    """Recent commits, newest first: {"date": ISO 8601, "message": subject}. [] outside a repo."""
    if not is_repo(root):
        return []
    res = _git(root, "log", f"-{limit}", "--format=%aI%x1f%s")
    return [dict(zip(("date", "message"), line.split("\x1f", 1))) for line in res.stdout.splitlines() if line]


def deleted_file(root: Path, pattern: str) -> tuple[str, str] | None:
    """(path, text) of the most recent deletion of a file matching `pattern`, from the commit
    before it was deleted; None if git has no such deletion."""
    commit = _git(root, "log", "--diff-filter=D", "-n", "1", "--format=%H", "--", pattern).stdout.strip()
    if not commit:
        return None
    names = _git(root, "show", "--diff-filter=D", "--name-only", "--format=", commit, "--", pattern)
    path = names.stdout.strip().splitlines()[0]
    return path, _git(root, "show", f"{commit}^:{path}").stdout


def added_times(root: Path, pathspec: str) -> dict[str, int]:
    """Unix time of the commit that first added each file matching `pathspec` (one git call)."""
    if not is_repo(root):
        return {}
    out = _git(root, "log", "--diff-filter=A", "--format=@%ct", "--name-only", "--", pathspec).stdout
    times: dict[str, int] = {}
    when = 0
    for line in out.splitlines():
        if line.startswith("@"):
            when = int(line[1:])
        elif line.strip():
            times.setdefault(line.strip(), when)  # newest first: keep the latest add (a re-filed review)
    return times


def status(root: Path) -> list[str]:
    """`git status --porcelain` lines (uncommitted changes, untracked files)."""
    return [line for line in _git(root, "status", "--porcelain").stdout.splitlines() if line]


def identity(root: Path) -> str | None:
    """Who commits would be by, resolved as git does (config or GIT_* variables); None if unknown."""
    res = _git(root, "var", "GIT_COMMITTER_IDENT")
    if res.returncode != 0 or ">" not in res.stdout:
        return None
    return res.stdout.split(">", 1)[0] + ">"


def commits(root: Path, limit: int = 20) -> list[dict[str, str]]:
    """Recent commits, newest first: {"sha", "date" (ISO 8601), "message" (subject), "parents" (count)}."""
    if not is_repo(root):
        return []
    res = _git(root, "log", f"-{limit}", "--format=%H%x1f%aI%x1f%s%x1f%P")
    out = []
    for line in res.stdout.splitlines():
        sha, date, message, parents = (line.split("\x1f") + ["", "", "", ""])[:4]
        if sha:
            out.append({"sha": sha, "date": date, "message": message, "parents": str(len(parents.split()))})
    return out


def changed_files(root: Path, sha: str) -> list[str]:
    """Paths a commit touched."""
    res = _git(root, "show", "--name-only", "--format=", sha)
    return [line for line in res.stdout.splitlines() if line.strip()]


def dirty_among(root: Path, paths: Sequence[str]) -> list[str]:
    """Those of `paths` with uncommitted changes (a revert would collide with them)."""
    if not paths:
        return []
    res = _git(root, "status", "--porcelain", "--", *paths)
    return [line[3:] for line in res.stdout.splitlines() if line.strip()]


def revert(root: Path, sha: str, message: str) -> tuple[bool, str]:
    """Revert one commit as a new commit with `message`. (True, new sha), or (False, git's complaint)
    with the working tree left as it was."""
    res = _git(root, "revert", "--no-edit", sha)
    if res.returncode != 0:
        _git(root, "revert", "--abort")
        return False, (res.stderr or res.stdout).strip()
    _git(root, "commit", "--amend", "-q", "-m", message)
    return True, _git(root, "rev-parse", "HEAD").stdout.strip()
