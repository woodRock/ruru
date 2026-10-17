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

M0 (scaffold) done. Planned milestones: M1 library core, M2 resolvers, M3 feeds and inbox, M4
projects (`cite`, `check`), M5 skill and docs, M6 TUI. Update this section as each lands.

- `cli.py`: argparse entry point (`ruru = ruru.cli:main`), exit-code constants, `_examples()` epilogs.
