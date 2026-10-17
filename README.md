# ruru

A local, plain-text literature library with a feed inbox, a terminal UI and an agent-friendly CLI.

*Ruru* is the morepork, Aotearoa's native owl.

Papers arrive from feeds (arXiv, OpenAlex, Crossref, journal RSS), get triaged like email, live as
Markdown files with YAML front matter in a git repository, and flow into each paper repo's `.bib`.
Agents such as Claude Code use exactly the same CLI.

> **Status:** early development. Working: the library (`init`, `add`, `list`, `show`, `search`,
> `tag`, `status`, `note`), metadata resolution (`add DOI|ARXIV|URL|"TITLE"`, `refresh`, `pdf`,
> `find`), and feeds with an inbox (`feeds`, `fetch`, `inbox`, `keep`, `later`, `skip`, `suggest`).
> Projects (`cite`, `check`) and the TUI come next.

## Install

With [uv](https://docs.astral.sh/uv/), as an isolated tool on your PATH (`~/.local/bin/ruru`):

```bash
uv tool install --python 3.11 --editable "/path/to/ruru[tui]"   # drop [tui] for the CLI only
```

`--editable` follows the checkout, so `git pull` is enough to update (re-run with `--reinstall`
when dependencies change). Without uv: `python3.11 -m pip install --user -e "/path/to/ruru[tui]"`.

## Quick start

*To come in M5.*

## The library on disk

A library is a git repository (default `~/papers`; set in `~/.config/ruru/config.toml`, or pass
`-L PATH`):

```
~/papers/
├── ruru.toml                 settings: contact email, BibTeX encoding, auto-commit, feeds, projects
├── papers/
│   └── wood2026joint/
│       ├── paper.md          YAML front matter (the source of truth) + your notes + agent notes
│       ├── paper.bib         generated from paper.md on every change; never edit it
│       └── paper.pdf         git-ignored
├── seen.jsonl                every inbox item ever triaged (dedupe)
└── .ruru/                    git-ignored: lock file, caches
```

- **Keys** look like `wood2026joint`: first author's surname, year, first significant title word.
  Clashes get `a`, `b`, …. A key never changes once assigned.
- **BibTeX** is ASCII with LaTeX escapes by default (`{\=a}`), which classic BibTeX on Overleaf
  needs; set `bib_encoding = "utf8"` for biber/biblatex.
- **Git:** every change is committed (only the files it touched), e.g. `ruru: tag wood2026joint
  +gc-ms`. Turn this off with `--no-commit` or `auto_commit = false`. ruru never pushes.
- **Duplicates:** the same DOI, arXiv id (any version), OpenAlex or Semantic Scholar id, or the same
  title within a year, is the same paper.

```bash
ruru init ~/papers --email you@example.org
ruru add --bib refs.bib -t gc-ms           # import BibTeX (keys regenerated; --keep-key to keep them)
ruru list -s to-read
ruru search retention time alignment
ruru show wood2026joint --bib
ruru tag wood2026joint +key-paper -todo
ruru status wood2026joint read
ruru note wood2026joint                    # your notes, in $EDITOR
```

## Feeds and the inbox

```bash
ruru feeds wizard                          # interactive: arXiv categories, journals, authors, topics
ruru fetch                                 # new items only: not in the library, inbox or seen.jsonl
ruru inbox                                 # newest first; `ruru show ID` for the abstract
ruru keep doi-10.1021-acs.analchem.6c03760 -t gc-ms
ruru later arxiv-2610.01234                # kept, with status `later`
ruru skip arxiv-2610.01235                 # remembered, never fetched again
```

Feed kinds, all declared in `ruru.toml` (the wizard writes them; so does `ruru feeds add`):

| kind | follows | example |
|---|---|---|
| `arxiv` | an arXiv API query | `query = "cat:physics.chem-ph AND abs:chromatography"` |
| `crossref` | a journal, by ISSN | `issn = "0003-2700"` (Analytical Chemistry) |
| `openalex` | authors, venues, or a phrase in titles and abstracts | `filter = "authorships.author.id:A5077569089"`, `search = "retention time alignment"` |
| `rss` | an RSS/Atom feed | `url = "https://rss.sciencedirect.com/publication/science/00219673"` |

Journals are best followed through `crossref` (some publishers, e.g. ACS, put their RSS behind a
bot check, which ruru reports as a failed feed). A feed's first fetch looks back 7 days; later
fetches start a day before the previous one. One failing feed never stops the others; it makes
the exit code 3. ruru has no daemon: schedule `ruru fetch` with cron, launchd or a Claude
scheduled task.

To look for papers outside your feeds: `ruru find retention time alignment gc-ms` (OpenAlex), then
`ruru add` the id it prints.

## Adding papers and keeping them current

```bash
ruru add 10.1186/s13321-021-00544-w        # DOI (Crossref, plus OpenAlex for ids, abstract, OA PDF)
ruru add 2103.13807 -t gc-ms               # arXiv: follows the journal DOI if the authors added one
ruru add https://pubs.acs.org/doi/10.1021/ac0518219     # any URL containing a DOI or arXiv id
ruru add "Deep learning" --year 2015       # an exact title, only if exactly one work matches
ruru refresh                               # re-resolve everything: preprints that got published, fixes
ruru pdf guillevic2021automated --fetch --open           # open-access PDFs only
```

ruru never guesses: a title that matches several works is refused with the candidates listed, and
a paper that can't be found is reported, not approximated. `refresh` updates metadata only;
keys, status, tags, projects and notes are never touched.

Sources are the arXiv, Crossref and OpenAlex APIs (no scraping). Requests are spaced (3 s for
arXiv), retried with backoff on 429/5xx, and cached by ETag under `.ruru/http/`. OpenAlex gives a
small free daily budget per IP address; for more, get a free key at
<https://openalex.org/settings/api> and put it in your **user** config (not `ruru.toml`, which is
committed):

```toml
# ~/.config/ruru/config.toml
library = "~/papers"
openalex_api_key = "…"        # or set RURU_OPENALEX_API_KEY
```

## Using ruru with agents

Every command runs without a terminal and never prompts. `--json` on read commands (schema in
[docs/json.md](docs/json.md)), `-q` prints keys only, warnings go to stderr, and colour appears only
on a TTY without `NO_COLOR`. Exit codes: 0 OK, 1 usage error or unknown key, 2 nothing matched,
3 partial failure, 4 library locked by another ruru process. Agents add notes with
`ruru note KEY --agent -m TEXT`, which never touches your `## Notes` section, and recommend triage
decisions with `ruru suggest ID keep|skip|later --reason TEXT`, which you see in `ruru inbox`.

*To come in M5:* the skill in `skills/ruru/` and the Claude Code permission rules.

## Development

```bash
make venv && make check
```

## Licence

MIT. See [LICENSE](LICENSE) and [CITATION.cff](CITATION.cff).
