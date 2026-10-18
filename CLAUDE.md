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

M0–M6 done (v1). Later: `related`, `export site|pdf`, PDF text extraction for search,
inbox ranking by similarity to the library.
Update this section as each lands.

Additions beyond the brief: `ruru find` (OpenAlex search for papers not yet in the library), `ruru show`
accepts inbox ids, `crossref` feeds (journal by ISSN; more reliable than publisher RSS, which ACS
blocks with a bot check), OpenAlex `search` feeds use the `title_and_abstract.search` phrase
filter (plain `search=` is too loose for a feed).

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
- `http.py`: `Client` (per-host spacing `SPACING`, retries on 429/5xx/transport errors honouring
  Retry-After, ETag/Last-Modified cache in `.ruru/http/`, `mailto=` and the OpenAlex key);
  `get_json`/`get_text` raise `NotFound` (exit 1) or `HttpError` (exit 3). Module globals
  `default_transport`/`default_sleep` are what tests replace.
- `resolve/`: `arxiv.py`, `crossref.py`, `openalex.py` (each `… -> Paper`; arXiv DataCite DOIs go
  to `ids.arxiv`, so `ids.doi` always means a published version) and `__init__.py`:
  `parse_identifier` (doi | arxiv | openalex | title; URLs reduced to an id or refused), `Resolver`
  (`by_doi` = Crossref + OpenAlex extras, `by_arxiv` follows the arXiv record's DOI, `by_title`
  accepts exactly one exact-title match, collapsing only records that share an id, `refresh` also
  looks for a same-title published article), `merge` (fill gaps), `apply_metadata` (only
  `METADATA` fields + ids; never erases), `changes`.
- `inbox.py`: `Item` (paper + id, fetched, published, suggestion), `dumps`/`loads` (front matter only,
  `ITEM_FIELDS` order), `item_id`, `Inbox` (`inbox/<fetched>/<id>.md`), seen.jsonl
  (`seen_record`, `read_seen`, `append_seen`), `Deduper` (the `same_paper` rule as set lookups over
  library + inbox + seen).
- `feeds.py`: `Feed` (validated `[[feeds]]` entry), kinds arxiv | openalex | crossref | rss,
  `fetch(client, feed, since) -> [Candidate]` (RSS entries with a DOI come back as stubs with
  `needs_resolve`), state in `.ruru/feeds.json` (`since_for`: first fetch 7 days, then last − 1 day).
- `tomledit.py`: append/remove one table in ruru.toml, keeping Jesse's comments.
- `ops.py`: operations shared by CLI and TUI: `fetch` (dedupe, resolve new RSS DOIs, one commit,
  state only for feeds that succeeded), `triage` (keep/later/skip + seen.jsonl), `suggest`,
  `add_feed`/`remove_feed`.
- `projects.py`: `Project` (`[projects.<name>]`), `BibFile` (split a .bib around the managed block;
  `managed` = key → entry text; `render` rewrites only the block, sorted by key), `entry_keys`,
  `scan_tex` (cite commands incl. optional args and `\nocite{*}`, follows `\input`/`\include`
  from the main file's directory, finds `\bibliography`/`\addbibresource`), `Report`,
  `duplicate_keys`. `ops.cite` regenerates every managed entry from the library, leaves keys
  defined outside the block and unknown managed keys alone, writes the paper repo's .bib without
  committing there, and adds the project to the papers' `projects`. `ops.check(fix, prune)`.
- `export/`: `export_document` (`ruru export json`: library, inbox, feeds, projects, git activity,
  in the `--json` shapes; `schema_version`), `export_bib`. Exporters only read; keep it that way.
- `doctor.py`: `run(root, offline)` → `Check(name, status ok|warn|fail, detail)`; includes
  paper.bib-in-step-with-paper.md (ruru has no index, so that is the consistency check).
- Agent decisions: agents may `skip` (never `keep`/`later`/`cite`: adding to the library or a paper
  is Jesse's). `ops.triage(by=, reason=)` records them in seen.jsonl (an agent applying its own
  suggestion inherits its reason); `ruru seen` lists decisions; `ops.unskip` restores an item's
  inbox file from the commit that deleted it (`gitops.deleted_file`) and drops the skip record.
- `ops.enrich` (`ruru enrich`): `Resolver.lookup` (best id, else exact title) then
  `resolve.fill_gaps` (empty `FILLABLE` fields and missing ids only; never title/type), refusing a
  record with a different normalised title or one whose new id belongs to another paper.
  `ruru add --bib` stays offline and then `cli.spawn`s `python -m ruru enrich KEYS` detached (log
  `.ruru/enrich.log`, `--no-enrich` to skip); conftest replaces `cli.spawn` (`spawned` fixture).
- `ops.remove` (`ruru rm`, `x` in the TUI): deletes paper.md/paper.bib (and the git-ignored PDF) in
  one commit and appends a `remove` record to seen.jsonl so feeds don't bring the paper back.
- `ops.unkeep` (`ruru unkeep`): library → inbox as a new item (today's fetch date, the original
  `published` from git history, tags kept in the item); refuses notes, a PDF or a project; drops
  the keep/later record from seen.jsonl. Inbox items carry `tags` only when non-empty.
- Viewed: `inbox.read_viewed`/`write_viewed` (`.ruru/viewed.json`, git-ignored, pruned to the
  inbox's ids). Only the TUI marks (cursor rests `VIEW_SECONDS` on an item, or `u`); unbolded rows
  are viewed. `unkeep`/`unskip` make an item new again. `--json` items have `viewed`.
- `reviews.py` + `ops.add_review`/`triage_reviews`: `reviews/<id>.md` (front matter `REVIEW_FIELDS`,
  Markdown body citing `[@key]`; `problems()` for self-consistency). `add_review` refuses papers not
  in `model.UNREAD` or without a PDF; status `new` → `kept`, or discarded (deleted). Rendered on
  demand by `export/review_html.py` (markdown-it, raw HTML off, no scripts; `$…$`/`$$…$$` via
  mdit-py-plugins' dollarmath and latex2mathml to MathML) to `.ruru/reviews/` via
  `cli.render_review_file`. TUI tab 3 (`y` keep, `n` discard, `o` open).
- PDFs: `ruru pdf` is a group (`show`, `add`, `missing`); `main` rewrites `ruru pdf KEY …` to
  `pdf show KEY …`. `ops.add_pdfs` moves `KEY.pdf` files (or `--key`) into `papers/KEY/` after a
  `%PDF` check; `cli.downloads_dir()` (`downloads` in the user config, default ~/Downloads).
  `cli.browse_for_pdf` opens `ops.paper_link` and puts `KEY.pdf` on the clipboard; conftest
  replaces `webbrowser.open` and `cli.copy_to_clipboard` (`browsed`, `clipboard` fixtures).
- `ops.rekey`: the only way a key changes; moves the directory (and the git-ignored PDF), commits,
  and reports the projects whose .tex/.bib still use the old key.
- `skills/ruru/SKILL.md`: the agent-facing workflows and rules. `tests/test_skill_docs.py` checks
  that every `ruru …` it (and the README, docs, this file) shows as code exists, and that the
  README's permission rules deny keep/skip/cite. Update the skill when commands change.
- `tui.py`: Textual app (`RuruApp`), only imported by `cli.cmd_tui`. Tabs Inbox / Reading list
  (`model.UNREAD` statuses) / Reviews / Library (read) / Projects; `y` keeps as read in the inbox and marks read
  on the reading list or a paper screen (`check_action` filters `triage` by its parameter); every action calls `ops` (`triage`, `suggest`, `tag`, `set_status`, `set_notes`,
  `fetch_pdf`, `fetch`, `check`); network work runs in `@work(thread=True)` workers. `fingerprint`
  (mtimes of `.git/index`, settings, seen.jsonl, papers/, inbox/ dirs) is polled every 2 s to reload
  on outside changes. `open_file`/`webbrowser.open`/`cli.edit_text` are the seams tests patch.
  Pilot tests in `tests/test_tui.py` (run synchronously via `asyncio.run`; no pytest plugin).
- `logo.py`: the mascot, as rows of `(text, role)` with a 256-colour palette. `banner()`/`ansi()`
  for the CLI (bare `ruru` prints it to stderr, colour only on a TTY), `rich_text()` for the TUI's
  empty panes (Rich imported lazily), `python -m ruru.logo --svg docs/ruru.svg` regenerates the
  README image. Change the art here only; everything else renders from it.
- `wizard.py`: `ruru feeds wizard` (the only prompting command; refuses without a TTY). Injected
  `ask`/`say` for tests; every feed goes through `ops.add_feed`.
- `search.py`: linear, accent-insensitive AND search with field weights; `SORTS`/`sort_papers` and
  `in_project` (`NO_PROJECT = "none"`, a reserved project name) shared by `ruru list` and the TUI.
- `output.py`: colour (`style`), `paper_json` (the docs/json.md schema), `paper_line`.
- `cli.py`: network work happens before `lib.transaction()` (never hold the lock across HTTP), and
  writes after it re-read the paper and apply only metadata. `_exit_code` maps per-item failures to
  1 (all failed alike) or 3 (mixed). argparse; one `cmd_*` per command; `common`/`write`/`read` parent parsers give every
  subcommand `-L`, `--no-commit`, `-q`, `--json`. `main` maps `RuruError` to exit codes.

Tests: `tests/conftest.py` isolates every test (`RURU_CONFIG`, git identity, short lock timeout) and
provides `run(*argv) -> Result(code, out, err)` and a `lib` fixture (an initialised library). It
also installs an empty `httpfix.Router` as the HTTP transport, so any unplanned request fails the
test; the `net` fixture serves the recorded responses in `tests/fixtures/http/` (see its README),
and `sleeps` records backoff/spacing sleeps.
