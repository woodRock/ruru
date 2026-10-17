# ruru

A local, plain-text literature library with a feed inbox, a terminal UI and an agent-friendly CLI.

*Ruru* is the morepork, Aotearoa's native owl.

Papers arrive from feeds (arXiv, OpenAlex, Crossref, journal RSS), get triaged like email, live as
Markdown files with YAML front matter in a git repository, and flow into each paper repo's `.bib`.
Agents such as Claude Code use exactly the same CLI.

> **Status:** early development (M0, scaffold). Nothing below the install step works yet.

## Install

```bash
python3.11 -m pip install -e .          # CLI
python3.11 -m pip install -e ".[tui]"   # plus the optional terminal UI
```

## Quick start

*To come in M5.*

## The library on disk

*To come in M1.*

## Using ruru with agents

*To come in M5:* the skill in `skills/ruru/`, the `--json` schema in `docs/json.md`, and the Claude
Code permission rules.

## Development

```bash
make venv && make check
```

## Licence

MIT. See [LICENSE](LICENSE) and [CITATION.cff](CITATION.cff).
