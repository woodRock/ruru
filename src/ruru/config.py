"""Where the library is (user config), and the library's own settings (`ruru.toml`).

User config: `$RURU_CONFIG`, else `$XDG_CONFIG_HOME/ruru/config.toml`, else
`~/.config/ruru/config.toml` (on macOS too), holding `library = "~/papers"`. The library is
`-L PATH`, else `$RURU_LIBRARY`, else that setting, else `~/papers`.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruru.errors import UsageError

DEFAULT_LIBRARY = "~/papers"
SETTINGS_FILE = "ruru.toml"


def config_path() -> Path:
    if os.environ.get("RURU_CONFIG"):
        return Path(os.environ["RURU_CONFIG"]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "ruru" / "config.toml"


def read_user_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def resolve_library(arg: str | None = None) -> Path:
    raw = arg or os.environ.get("RURU_LIBRARY") or read_user_config().get("library") or DEFAULT_LIBRARY
    return Path(raw).expanduser()


def secret(name: str) -> str | None:
    """An API key: `$RURU_<NAME>`, else `<name>` in the user config (never in ruru.toml, which is in git)."""
    return os.environ.get(f"RURU_{name.upper()}") or read_user_config().get(name) or None


def toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a TOML basic string


def write_user_config(library: Path) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# ruru user settings\nlibrary = {toml_str(str(library))}\n", encoding="utf-8")
    return path


@dataclass
class Settings:
    contact_email: str = ""
    bib_encoding: str = "ascii"
    auto_commit: bool = True
    feeds: list[dict[str, Any]] = field(default_factory=list)
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    own: list[str] = field(default_factory=list)  # keys of Jesse's own work outside the library


def load_settings(root: Path) -> Settings:
    path = root / SETTINGS_FILE
    if not path.exists():
        raise UsageError(f"{root} is not a ruru library (no {SETTINGS_FILE}); create one with `ruru init {root}`")
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise UsageError(f"{path}: {e}") from None
    s = Settings(
        contact_email=str(raw.get("contact_email", "")),
        bib_encoding=str(raw.get("bib_encoding", "ascii")),
        auto_commit=bool(raw.get("auto_commit", True)),
        feeds=list(raw.get("feeds", [])),
        projects=dict(raw.get("projects", {})),
        own=[str(k) for k in raw.get("own", [])] if isinstance(raw.get("own", []), list) else [],
    )
    if not isinstance(raw.get("own", []), list):
        raise UsageError(f"{path}: own must be a list of keys, e.g. own = [\"warpalign\"]")
    if s.bib_encoding not in ("ascii", "utf8"):
        raise UsageError(f"{path}: bib_encoding must be 'ascii' or 'utf8', not {s.bib_encoding!r}")
    return s


def settings_template(contact_email: str = "", bib_encoding: str = "ascii") -> str:
    return f"""\
# ruru library settings: https://github.com/woodRock/ruru

# Sent to OpenAlex and Crossref for their polite pools.
contact_email = {toml_str(contact_email)}

# ascii: LaTeX escapes for classic BibTeX (Overleaf's IEEE/ACM styles). utf8: for biber/biblatex.
bib_encoding = {toml_str(bib_encoding)}

# Commit every change ruru makes (only the files it touched). Never pushes.
auto_commit = true

# Feeds: `ruru feeds wizard` (interactive) or `ruru feeds add …`.
# Projects: `ruru project add NAME --path … --bib … --tex …`.
"""
