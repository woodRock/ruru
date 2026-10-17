---
name: ruru
description: Use Jesse's ruru literature library from the command line. Use when triaging new papers from feeds, finding or reading papers in the library, summarising or annotating papers, choosing citations while writing a paper, or checking a paper repo's citations. Triggers include "inbox", "new papers", "triage", "what have I read on…", "find a citation for…", "summarise this paper", "check my citations", and any mention of ruru.
---

# ruru

ruru is Jesse's local, plain-text literature library: a git repository (default `~/papers`) of
`papers/<key>/paper.md` files, a feed inbox, and paper repos (projects) whose `.bib` files ruru
manages. You use the same CLI Jesse does. Run `ruru COMMAND -h` for exact flags; every help ends
with examples. This skill is about workflow and judgement.

## Ground rules

- **Read freely; write only through** `ruru note KEY --agent`, `ruru tag`, `ruru suggest`,
  `ruru fetch`, and `ruru skip`/`ruru unskip` for inbox items (see "Weekly triage").
- **Never add to the library or a paper: never run `ruru keep`, `ruru later` or `ruru cite`**, and
  never `ruru check --fix`/`--prune` (they cite). `later` counts: it puts the paper in the library.
  Those are Jesse's decisions; recommend them with `ruru suggest`, or tell him the exact command to
  run. Don't change feeds or projects (`ruru feeds add/remove`, `ruru project add/remove`) unless
  he asks.
- **Never edit library files directly.** Everything goes through the CLI, which locks, writes
  atomically and commits each change, so Jesse can see and revert what you did.
- **Use `--json`** for anything you parse, and `-q` for keys to chain. The schema is in the repo's
  `docs/json.md`. Warnings go to stderr.
- **Exit codes:** 0 OK, 1 usage error or unknown key, 2 nothing matched (an empty search, or
  `check` found issues), 3 partial failure (a feed or API failed), 4 library locked (wait and retry).
- Set `RURU_AGENT=claude` (or pass `--by claude`) so notes and suggestions are attributed to you.
- Put the subcommand first (`ruru list -L PATH …`, not `ruru -L PATH list …`): Jesse's permission
  rules match the start of the command.

## Honesty

- **Never invent a citation, a key, a DOI or a claim about a paper.** Cite only keys that
  `ruru show KEY` confirms exist. If a paper isn't in the library, say so; if it should be, find it
  (`ruru find`) and tell Jesse the identifier to add, or add it with `ruru add ID` when he has asked
  you to.
- **Say what you read.** A summary built from the abstract says "abstract only"; one from the PDF
  says "full text". `ruru show KEY --json` has `has_pdf`; `ruru pdf KEY` prints its path.
- When ruru can't find something (`ruru add` exits 1, a title is ambiguous), report that. Don't
  guess an identifier or pick a candidate yourself.

## Weekly triage

1. `ruru fetch` (exit 3 means some feed failed: mention which, carry on with the rest).
2. `ruru inbox --json` and read every title and abstract. Start with the targeted feeds
   (`ruru inbox -f rt-alignment --json` etc.); the journal feeds are high volume.
3. Decide each item against Jesse's interests (fish and food chemistry with AI, GC-MS, REIMS and
   other ambient MS, retention-time alignment, chemometrics; `ruru project list --json` and
   `ruru list --json` show his projects and what he keeps):
   - **Clearly off topic: skip it yourself**, in batches, always attributed and explained:
     `ruru skip ID… --by claude --reason "…"`. Group items that share a reason into one command
     (one commit). Skip only when you are confident from what you read; the journal feeds are
     mostly off-topic bulk.
   - **Possibly relevant: suggest, never skip.** `ruru suggest ID keep --reason "…"` for papers he
     would cite or read this month, `ruru suggest ID later --reason "…"` for relevant but not
     urgent. Tie each reason to a project or method. When unsure between skip and later, suggest
     later: a wrong skip hides a paper, a wrong suggestion costs him a glance.
   - Say what each judgement rests on: end reasons with "(title only)" when there was no abstract.
4. Summarise for Jesse: the suggested keeps with their reasons, the suggested laters, how many you
   skipped (and the main reasons), and anything surprising. Remind him he can audit and undo:
   `ruru seen -d skip --by claude` lists your skips, `ruru unskip ID` restores one. He accepts
   suggestions with `ruru keep $(ruru inbox -q --suggested keep)` (likewise `later`), or `a` in
   `ruru tui`.

## Finding and reading papers

- `ruru search WORDS --json`: every word must match somewhere in metadata, notes or agent notes.
- `ruru list -t TAG -p PROJECT -s STATUS --json` to filter.
- `ruru show KEY --json` for one paper, with Jesse's `notes` and the `agent_notes`; read the
  `paper.md` the `path` points to if you need it verbatim.
- `ruru find WORDS --json` searches OpenAlex for papers not yet in the library; each result's `id`
  is what `ruru add` takes, and `in_library` gives the key if Jesse already has it.

## Notes

- `ruru note KEY --agent --kind summary -m "…"` (or `-F file.md`, `-F -` for stdin) appends a dated,
  attributed entry under `## Agent notes`. It never touches Jesse's `## Notes`; never try to.
- Start every summary with what it is based on: "Abstract only." or "Full text (paper.pdf)."
- Useful kinds: `summary`, `methods`, `relevance` (to a named project), `critique`. Keep entries
  short and factual; quote numbers from the paper, not from memory.
- Tag sparingly with `ruru tag KEY +tag`, reusing tags that exist (`ruru list --json` shows them).

## Before writing a section

1. `ruru list -p PROJECT --json` (papers Jesse kept for this paper) and `ruru search …` for the
   topic. Read the `paper.md` files (or at least abstracts and notes) of what you plan to cite.
2. Cite only keys that exist. If you need a paper the library lacks, stop and tell Jesse.
3. After editing the `.tex`, run `ruru check PROJECT --json` and report the issues: keys missing
   from the bib (`in_library: true` means `ruru check PROJECT --fix` would add them; that's Jesse's
   command to run), unused or stale entries, duplicates.

## Other things you may be asked

- `ruru refresh KEY` re-resolves metadata (a preprint published since); keys never change.
- `ruru pdf KEY --fetch` downloads an open-access PDF when one exists; it never bypasses paywalls.
- `ruru export json` writes the whole library as one JSON document; `ruru export bib -p PROJECT`
  writes a project's papers as BibTeX.
- `ruru doctor` checks setup and API access when something seems wrong.
