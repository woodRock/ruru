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


def status(root: Path) -> list[str]:
    """`git status --porcelain` lines (uncommitted changes, untracked files)."""
    return [line for line in _git(root, "status", "--porcelain").stdout.splitlines() if line]


def identity(root: Path) -> str | None:
    """Who commits would be by, resolved as git does (config or GIT_* variables); None if unknown."""
    res = _git(root, "var", "GIT_COMMITTER_IDENT")
    if res.returncode != 0 or ">" not in res.stdout:
        return None
    return res.stdout.split(">", 1)[0] + ">"
