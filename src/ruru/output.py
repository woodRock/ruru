"""Output for people (colour on a TTY only) and for agents (`--json`, schema in docs/json.md)."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, TextIO

from ruru.inbox import Item, read_viewed
from ruru.model import Paper

_CODES = {"bold": "1", "dim": "2", "cyan": "36", "green": "32", "yellow": "33", "red": "31"}


def color_enabled(stream: TextIO | None = None) -> bool:
    stream = stream or sys.stdout
    return stream.isatty() and not os.environ.get("NO_COLOR")


def style(text: str, *names: str, stream: TextIO | None = None) -> str:
    if not names or not color_enabled(stream):
        return text
    return f"\033[{';'.join(_CODES[n] for n in names)}m{text}\033[0m"


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def paper_json(p: Paper, *, path: str, has_pdf: bool, full: bool = False) -> dict[str, Any]:
    out = {k: _iso(v) for k, v in p.front_matter().items()}
    out["path"] = path
    out["has_pdf"] = has_pdf
    if full:
        out["notes"] = p.notes
        out["agent_notes"] = p.agent_notes
    return out


def emit_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def warn(message: str) -> None:
    print(f"ruru: {message}", file=sys.stderr)


_STATUS_COLOR = {"to-read": "yellow", "later": "dim", "reading": "cyan", "read": "green"}


def paper_line(p: Paper) -> str:
    key = style(f"{p.key:<24}", "bold")
    status = style(f"{p.status:<8}", _STATUS_COLOR.get(p.status, "dim"))
    tags = style(" ".join(f"#{t}" for t in p.tags), "dim") if p.tags else ""
    return f"{key} {p.year or '----'}  {status} {p.title}  {tags}".rstrip()


def item_json(item: Item, root: Path, viewed: set[str] | None = None) -> dict[str, Any]:
    out = {k: _iso(v) for k, v in item.front_matter().items()}
    out.setdefault("tags", [])  # stable keys: the file leaves empty tags out
    if out.get("suggestion"):
        out["suggestion"] = {k: _iso(v) for k, v in out["suggestion"].items()}
    out["path"] = str(item.path.relative_to(root)) if item.path else None
    out["viewed"] = item.id in (read_viewed(root) if viewed is None else viewed)
    return out


def item_line(item: Item) -> str:
    p = item.paper
    feed = style(f"[{p.source.removeprefix('feed:')}]", "dim")
    line = f"{style(f'{item.id:<32}', 'bold')} {p.year or '----'}  {p.title}  {feed}"
    if s := item.suggestion:
        line += "  " + style(f"→ {s.get('action')}: {s.get('reason')}", "yellow")
    return line
