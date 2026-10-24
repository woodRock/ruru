"""Operations that edit ruru.toml: feeds, own work, cited drafts and software."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from ruru import cited as citedmod
from ruru import feeds as feedmod
from ruru import tomledit
from ruru.config import SETTINGS_FILE
from ruru.errors import UsageError
from ruru.feeds import Feed
from ruru.library import Library
from ruru.ops.common import rewrite_settings, summary_of
from ruru.ops.papers import tag
from ruru.ops.projects import get_project

OWN_TAG = "own-work"


_OWN_COMMENT = "Your own drafts and software outside the library: `ruru own add KEY`."


def cited_entries(lib: Library) -> dict[str, citedmod.Cited]:
    """`[cited.<key>]`: drafts and software a paper cites that aren't in the library."""
    return citedmod.load(lib.settings.cited)


def own_keys(lib: Library) -> set[str]:
    """Jesse's own work (a label: it is checked like anything else): library papers tagged own-work,
    every `[cited]` draft and software, and keys in the legacy `own` list."""
    return (set(lib.settings.own) | set(lib.settings.cited)
            | {p.key for p in lib.papers() if OWN_TAG in p.tags})


def set_own(lib: Library, add: Sequence[str] = (), remove: Sequence[str] = ()) -> None:
    """Tag (untag) library papers as Jesse's own work. Drafts and software go in `[cited]`
    (`add_cited`); `remove` also drops keys from the legacy `own` list."""
    if outside := [k for k in add if not lib.exists(k)]:
        raise UsageError(f"not in the library: {', '.join(outside)}. Drafts and software: "
                         f"`ruru cited add {outside[0]} --draft PROJECT` or `--software PATH`")
    if add:
        tag(lib, list(add), add=[OWN_TAG])
    if untagged := [k for k in remove if lib.exists(k)]:
        tag(lib, untagged, remove=[OWN_TAG])
    _drop_legacy_own(lib, [k for k in remove if not lib.exists(k)])


def _drop_legacy_own(lib: Library, keys: Sequence[str]) -> None:
    listed = [k for k in lib.settings.own if k not in keys]
    if listed == lib.settings.own:
        return
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    text = (tomledit.set_list(text, "own", listed, _OWN_COMMENT) if listed
            else tomledit.remove_key(text, "own", _OWN_COMMENT))
    rewrite_settings(lib, text, f"ruru: own remove {summary_of(list(keys))}")


def add_cited(lib: Library, key: str, kind: str, *, project: str | None = None, path: str | None = None,
              url: str | None = None) -> citedmod.Cited:
    """Add `[cited.<key>]`: a draft (its ruru project) or software (its repo and/or URL)."""
    values = {"kind": kind, "project": project, "path": path, "url": url}
    entry = citedmod.Cited.from_dict(key, values)
    if lib.exists(key):
        raise UsageError(f"{key} is in the library; `ruru own add {key}` marks it as your own work")
    if key in lib.settings.cited:
        raise UsageError(f"{key} is already cited as {lib.settings.cited[key].get('kind')}; "
                         f"`ruru cited remove {key}` first")
    if project:
        get_project(lib, project)
    if entry.path and not entry.path.is_dir():
        print(f"ruru: warning: {entry.path} does not exist (yet)", file=sys.stderr)
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    rewrite_settings(lib, tomledit.append_table(text, f"[cited.{key}]", values),
                      f"ruru: cited add {key} ({kind})")
    _drop_legacy_own(lib, [key])
    return entry


def remove_cited(lib: Library, key: str) -> None:
    if key not in lib.settings.cited:
        raise UsageError(f"no [cited.{key}] in ruru.toml")
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    rewrite_settings(lib, tomledit.remove_table(text, f"[cited.{key}]"), f"ruru: cited remove {key}")


def add_feed(lib: Library, feed: Feed) -> None:
    feed.validate()
    if feed.name in {f.get("name") for f in lib.settings.feeds}:
        raise UsageError(f"there is already a feed named {feed.name!r}")
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    rewrite_settings(lib, feedmod.add_to_toml(text, feed), f"ruru: feeds add {feed.name}")


def remove_feed(lib: Library, name: str) -> None:
    text = (lib.root / SETTINGS_FILE).read_text(encoding="utf-8")
    rewrite_settings(lib, feedmod.remove_from_toml(text, name), f"ruru: feeds remove {name}")
