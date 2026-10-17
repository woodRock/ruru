# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`ruru` (the morepork; plain ASCII, no macron): a local, plain-text literature library kept in a git
repository. Papers **arrive** from feeds (arXiv, OpenAlex, Crossref, journal RSS), are **triaged**
in an inbox like email, **live** as `paper.md` files (YAML front matter + notes), and **flow** into
paper repos' `.bib` files through `ruru cite` / `ruru check`. The CLI is the primary interface and
agents use exactly the same commands Jesse does; the Textual TUI is an optional extra.

Out of scope, by decision: Ionic Scholar (no import, no sync), an MCP server, a daemon, any
database as the source of truth, scraping Google Scholar.

## Commands

```bash
make venv                                   # python3.11 -m venv .venv + editable install [dev,tui]
make check                                  # lint + types + tests with coverage (≥ 90 %)
make test-fast                              # not slow
.venv/bin/python -m pytest tests/test_x.py::test_name
```

Outside the venv, always call `python3.11` explicitly (`python3` is a zsh alias that is not
expanded under `nohup`).

## Rules

- **`ruru` never imports Textual.** Only `ruru tui` imports `ruru.tui`, lazily; CI's package job
  asserts `textual` is absent from `sys.modules`.
- **No network in tests.** Every HTTP response is a recorded fixture under `tests/fixtures/`.
- **The agent contract is a public API** (README, `docs/json.md`): no prompts, no stdin unless a
  command asks for it (`-F -`); colour only on a TTY with `NO_COLOR` unset; `--json` on every read
  command with stable keys, ISO dates and full ids; `-q` prints keys only; warnings and progress go
  to stderr. Exit codes: 0 OK, 1 usage / unknown key, 2 nothing matched, 3 partial failure, 4 lock
  held. argparse's own exit 2 on usage errors is overridden to 1. Every `--help` ends with examples
  (a test enforces it).
- **Front matter is the source of truth.** `paper.bib` is regenerated from it, deterministically,
  fields in a stable order. Keys never change once assigned (`ruru rekey` is explicit).
- **Every write is atomic** (temp file + rename) and under `.ruru/lock`; a TUI and an agent may
  write at once.
- **Auto-commit** stages only the files a command touched; never push.
- The TUI calls the same functions as the CLI; it never reimplements an action.
- Exporters live in `ruru/export/` and never touch library code.
- `skills/ruru/SKILL.md` ships with the code; a test checks every command it names exists.

## Architecture

M0–M1 done. Next: M2 resolvers (DOI/arXiv/OpenAlex/Crossref, `add ID|URL|TITLE`, `refresh`,
`pdf --fetch`), M3 feeds and inbox (incl. `ruru feeds wizard`), M4 projects (`ruru project add`,
`cite`, `check`), M5 skill and docs, M6 TUI. Update this section as each lands.

Decisions beyond the brief (Jesse, 2026-10-17): `bib_encoding = "ascii"` by default (he uses Overleaf
with classic BibTeX styles); feeds are set up by an interactive `ruru feeds wizard`, the one command
that prompts (it refuses without a TTY and is built on the non-interactive `ruru feeds add`); a
library starts with no projects, added with `ruru project add`. `platformdirs` is not a dependency:
the user config is `~/.config/ruru/config.toml` on macOS too.

- `errors.py`: exit codes and `RuruError` subclasses carrying them (`UnknownKey`, `LockHeld`, …).
- `text.py`: `unaccent`, `words`, `norm_title`, `fold` (search), `split_name` (particles go with the
  family name).
- `keys.py`: `base_key(authors, year, title)`, `unique_key(base, taken)`.
- `model.py`: `Paper` dataclass; `loads`/`dumps` (front matter in `FIELDS` order, lists and `ids` in
  flow style, unknown keys kept, body verbatim); body sections `get_notes`/`set_notes`/
  `get_agent_notes`/`append_agent_note`; `norm_id`, `same_paper` (the dedupe rule).
- `bibtex.py`: `to_bibtex(paper, encoding)` (fixed field order, title words with inner capitals
  braced, `ascii` = LaTeX escapes), `parse_bibtex(text)` (bibtexparser v1 → `Paper`s).
- `fsutil.py`: `atomic_write`, `library_lock` (flock on `.ruru/lock`, waits `$RURU_LOCK_TIMEOUT`
  seconds, default 10, then `LockHeld`).
- `gitops.py`: `commit(root, paths, message)` stages and commits exactly those paths.
- `config.py`: user config + library resolution, `Settings` from `ruru.toml`, `settings_template`.
- `library.py`: `Library` (`open`, `init`, `get`, `papers`, `find_duplicate`, `add`, `set_status`) and
  `Transaction` (`with lib.transaction(msg) as tx:` locks, `tx.save(paper)` writes paper.md +
  paper.bib, commits touched files at the end; set `tx.message` inside to describe the change).
- `search.py`: linear, accent-insensitive AND search with field weights.
- `output.py`: colour (`style`), `paper_json` (the docs/json.md schema), `paper_line`.
- `cli.py`: argparse; one `cmd_*` per command; `common`/`write`/`read` parent parsers give every
  subcommand `-L`, `--no-commit`, `-q`, `--json`. `main` maps `RuruError` to exit codes.

Tests: `tests/conftest.py` isolates every test (`RURU_CONFIG`, git identity, short lock timeout) and
provides `run(*argv) -> Result(code, out, err)` and a `lib` fixture (an initialised library).
