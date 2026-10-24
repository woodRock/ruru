"""Things that reach outside the library: the editor, clipboard, browser, downloads folder and detached
processes. Tests replace these (conftest), so callers say `desktop.edit_text(…)`, never a local copy."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

from ruru import ops
from ruru.config import secret
from ruru.errors import UsageError
from ruru.model import Paper


def spawn(argv: list[str], log: Path) -> None:
    """Start `argv` detached from this process and its terminal, output appended to `log`.
    Tests replace this (conftest) so that nothing is ever spawned."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)


def edit_text(text: str) -> str:
    """Open `text` in $VISUAL/$EDITOR (vi) and return what was saved. Used by `note` and the TUI."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text + "\n" if text else "")
        path = f.name
    try:
        if subprocess.run([*shlex.split(editor), path]).returncode != 0:
            raise UsageError(f"editor {editor!r} exited with an error; notes unchanged")
        return Path(path).read_text(encoding="utf-8")
    finally:
        os.unlink(path)


def copy_to_clipboard(text: str) -> bool:
    """Put `text` on the clipboard (pbcopy on macOS, wl-copy/xclip elsewhere); False if none is there."""
    for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            return subprocess.run(cmd, input=text.encode(), check=False).returncode == 0
    return False


def browse_for_pdf(p: Paper) -> str | None:
    """Open the paper's DOI page (where an institutional sign-in works) and put `KEY.pdf` on the
    clipboard for the save dialog. Returns the link, or None if the paper has none."""
    link = ops.paper_link(p)
    if link:
        webbrowser.open(link)
        copy_to_clipboard(f"{p.key}.pdf")
    return link


def downloads_dir() -> Path:
    """Where `ruru pdf add` looks by default: `downloads` in the user config, else ~/Downloads."""
    return Path(secret("downloads") or "~/Downloads").expanduser()


def open_links(papers: list[Paper]) -> list[Paper]:
    """Open the DOI (or URL) page of each paper that has one, at most MAX_TABS; return those opened."""
    openable = [p for p in papers if ops.paper_link(p)][:MAX_TABS]
    for p in openable:
        webbrowser.open(ops.paper_link(p) or "")
    return openable


MAX_TABS = 10  # `pdf missing --open` opens at most this many browser tabs at once


opener = "open" if sys.platform == "darwin" else "xdg-open"


def open_file(path: Path) -> None:
    """Open a file in the system viewer (Preview, …)."""
    subprocess.run([opener, str(path)], check=False)


def editor_command(path: Path, line: int) -> list[str]:
    """$VISUAL/$EDITOR (vi) opened at `line`: `+LINE FILE` for terminal editors, FILE:LINE for GUI ones."""
    editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi")
    name = Path(editor[0]).name
    if name in ("code", "cursor", "codium"):
        return [*editor, "-g", f"{path}:{line}"]
    if name in ("subl", "zed"):
        return [*editor, f"{path}:{line}"]
    return [*editor, f"+{line}", str(path)]


def edit_at(path: Path, line: int) -> None:
    subprocess.run(editor_command(path, line), check=False)
