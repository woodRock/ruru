import os

import pytest

from ruru import gitops
from ruru.errors import LockHeld
from ruru.fsutil import atomic_write, library_lock


def test_atomic_write_replaces_and_leaves_no_temp(tmp_path):
    path = tmp_path / "a" / "file.md"
    atomic_write(path, "one ā\n")
    atomic_write(path, "two\n")
    assert path.read_text(encoding="utf-8") == "two\n"
    assert os.listdir(path.parent) == ["file.md"]


def test_atomic_write_permissions(tmp_path):
    new = tmp_path / "new.md"
    atomic_write(new, "x")
    umask = os.umask(0)
    os.umask(umask)
    assert new.stat().st_mode & 0o777 == 0o666 & ~umask  # not mkstemp's 0600
    new.chmod(0o640)
    atomic_write(new, b"y")
    assert new.stat().st_mode & 0o777 == 0o640 and new.read_bytes() == b"y"


def test_atomic_write_failure_keeps_old_file(tmp_path, monkeypatch):
    path = tmp_path / "file.md"
    atomic_write(path, "old")

    def boom(*a):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write(path, "new")
    assert path.read_text() == "old"
    assert os.listdir(tmp_path) == ["file.md"]


def test_lock_is_exclusive_and_released(tmp_path):
    with library_lock(tmp_path, timeout=0), pytest.raises(LockHeld), library_lock(tmp_path, timeout=0.1):
        pass
    with library_lock(tmp_path, timeout=0):
        pass  # released


def test_commit_only_touched_files(tmp_path):
    gitops.init(tmp_path)
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").write_text("b")
    assert gitops.commit(tmp_path, [tmp_path / "a"], "only a")
    assert not gitops.commit(tmp_path, [tmp_path / "a"], "nothing changed")
    status = gitops._git(tmp_path, "status", "--porcelain").stdout
    assert status == "?? b\n"
    (tmp_path / "a").unlink()
    assert gitops.commit(tmp_path, [tmp_path / "a"], "remove a")
    assert gitops._git(tmp_path, "ls-files").stdout == ""


def test_commit_failure_warns(tmp_path, capsys):
    gitops.init(tmp_path)
    (tmp_path / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (tmp_path / ".git" / "hooks" / "pre-commit").chmod(0o755)
    (tmp_path / "a").write_text("a")
    assert not gitops.commit(tmp_path, [tmp_path / "a"], "blocked")
    assert "git commit failed" in capsys.readouterr().err
