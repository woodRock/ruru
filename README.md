# ruru

A local, plain-text literature library with a feed inbox, a terminal UI and an agent-friendly CLI.

*Ruru* is the morepork, Aotearoa's native owl.

Papers arrive from feeds (arXiv, OpenAlex, Crossref, journal RSS), get triaged like email, live as
Markdown files with YAML front matter in a git repository, and flow into each paper repo's `.bib`.
Agents such as Claude Code use exactly the same CLI.

> **Status:** early development. M1 (the library core) works: `init`, `add` (from BibTeX or by
> hand), `list`, `show`, `search`, `tag`, `status`, `note`. Feeds, resolvers and projects come next.

## Install

```bash
python3.11 -m pip install -e .          # CLI
python3.11 -m pip install -e ".[tui]"   # plus the optional terminal UI
```

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

## Using ruru with agents

Every command runs without a terminal and never prompts. `--json` on read commands (schema in
[docs/json.md](docs/json.md)), `-q` prints keys only, warnings go to stderr, and colour appears only
on a TTY without `NO_COLOR`. Exit codes: 0 OK, 1 usage error or unknown key, 2 nothing matched,
3 partial failure, 4 library locked by another ruru process. Agents add notes with
`ruru note KEY --agent -m TEXT`, which never touches your `## Notes` section.

*To come in M5:* the skill in `skills/ruru/` and the Claude Code permission rules.

## Development

```bash
make venv && make check
```

## Licence

MIT. See [LICENSE](LICENSE) and [CITATION.cff](CITATION.cff).
