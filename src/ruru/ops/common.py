"""Helpers shared by the operation modules."""

from __future__ import annotations

import tomllib

from ruru import cited as citedmod
from ruru import feeds as feedmod
from ruru import projects as projmod
from ruru.config import SETTINGS_FILE
from ruru.errors import UsageError
from ruru.library import Library


def summary_of(names: list[str], limit: int = 5) -> str:
    return " ".join(names) if len(names) <= limit else f"{' '.join(names[:limit])} +{len(names) - limit} more"


def rewrite_settings(lib: Library, text: str, message: str) -> None:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # pragma: no cover - our edits always produce valid TOML
        raise UsageError(f"refusing to write invalid ruru.toml: {e}") from None
    feedmod.load(list(raw.get("feeds", [])))
    projmod.load(dict(raw.get("projects", {})))
    citedmod.load(dict(raw.get("cited", {})))
    with lib.transaction(message) as tx:
        tx.write(lib.root / SETTINGS_FILE, text)
    lib.settings.feeds = list(raw.get("feeds", []))
    lib.settings.projects = dict(raw.get("projects", {}))
    lib.settings.cited = dict(raw.get("cited", {}))
    lib.settings.own = [str(k) for k in raw.get("own", [])]
