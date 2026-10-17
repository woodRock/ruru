"""Atomic writes and the library lock, so a TUI and an agent can write at the same time."""

from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

from ruru.errors import LockHeld

LOCK_TIMEOUT = 10.0  # seconds to wait for another ruru process before exiting 4


def atomic_write(path: Path, data: str | bytes) -> None:
    """Write to a temporary file in the same directory, then rename over `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, _mode(path))  # mkstemp makes 0600 files; keep the usual permissions
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _mode(path: Path) -> int:
    try:
        return path.stat().st_mode & 0o777
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        return 0o666 & ~umask


def lock_timeout() -> float:
    return float(os.environ.get("RURU_LOCK_TIMEOUT", LOCK_TIMEOUT))


@contextlib.contextmanager
def library_lock(root: Path, timeout: float | None = None) -> Iterator[None]:
    """Exclusive lock on `<root>/.ruru/lock`; LockHeld if another process keeps it past `timeout`."""
    timeout = lock_timeout() if timeout is None else timeout
    path = root / ".ruru" / "lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as f:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockHeld(f"library is locked by another ruru process ({path})") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
