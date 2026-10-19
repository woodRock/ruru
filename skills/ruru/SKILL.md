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
  `ruru fetch`, `ruru skip`/`ruru unskip` for inbox items (see "Weekly triage"), `ruru review add`
  (see "Reviews"), `ruru verdict` (see "Checking citations") and `ruru pdf add` (filing PDFs Jesse
  downloaded).
- **Never add to the library or a paper: never run `ruru keep`, `ruru later` or `ruru cite`**, and
  never `ruru check --fix`/`--prune` (they cite). `later` counts: it puts the paper in the library.
  Those are Jesse's decisions; recommend them with `ruru suggest`, or tell him the exact command to
  run. Never remove papers (`ruru rm`, `ruru unkeep`) either. Don't change feeds or projects
  (`ruru feeds add/remove`, `ruru project add/remove`) unless he asks.
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
   - **Possibly relevant: suggest, never skip.** `ruru suggest ID keep --reason "…"` only for papers
     Jesse has evidently read already (`keep` files a paper as read); `ruru suggest ID later
     --reason "…"` for papers to read (`later` puts them on his reading list). Tie each reason to a project or method. When unsure between skip and later, suggest
     later: a wrong skip hides a paper, a wrong suggestion costs him a glance.
   - Reading the inbox never marks items as seen: only Jesse's TUI does (`viewed` in the JSON), so
     your suggestions sit on items that stay bold for him until he looks.
   - Say what each judgement rests on: end reasons with "(title only)" when there was no abstract.
4. Summarise for Jesse: the suggested keeps with their reasons, the suggested laters, how many you
   skipped (and the main reasons), and anything surprising. Remind him he can audit and undo:
   `ruru seen -d skip --by claude` lists your skips, `ruru unskip ID` restores one. He accepts
   suggestions with `ruru keep $(ruru inbox -q --suggested keep)` (likewise `later`), or `a` in
   `ruru tui`.

## Finding and reading papers

- `ruru search WORDS --json`: every word must match somewhere in metadata, notes or agent notes.
- `ruru list -t TAG -p PROJECT -s STATUS --json` to filter (`-p none`: in no project;
  `--sort added|key|year|status|title`, `-r` to reverse).
- `ruru show KEY --json` for one paper, with Jesse's `notes` and the `agent_notes`; read the
  `paper.md` the `path` points to if you need it verbatim.
- `ruru find WORDS --json` searches OpenAlex for papers not yet in the library; each result's `id`
  is what `ruru add` takes, and `in_library` gives the key if Jesse already has it.

## Reviews

A review is a short synthesis of a few related papers on Jesse's **reading list** (status to-read,
later or reading; never inbox items, never papers he has read), written for him: why they matter
to his work and how they compare. It lands in the TUI's Reviews tab as new, and he keeps or
discards it. `ruru review add` refuses a paper that isn't on the reading list or has no PDF.

1. **Pick the group.** Related reading-list papers, usually 2–6: `ruru list -s to-read -t TAG --json`
   (also `-s later`, `-s reading`), a project (`-p PROJECT`), or what Jesse names. Read
   `ruru project list --json` and his `own-work` papers (`ruru list -t own-work --json`) so you can
   say how the group bears on his work.
2. **Get every PDF; the review needs each full text.** `ruru pdf missing KEY… --json` lists the
   missing ones. Try `ruru pdf KEY --fetch` (open access) for each. For the rest, stop and give
   Jesse the list with links: he runs `ruru pdf missing KEY… --open`, signs in with his institution,
   saves each as `KEY.pdf` in Downloads, and tells you; then run `ruru pdf add` (it files every
   `KEY.pdf` in ~/Downloads into the library) and check `ruru pdf missing KEY…` is empty.
3. **Read the full texts** (`ruru pdf KEY` prints the path; read the PDF with your file tool, in
   page ranges for long papers). Quote numbers and claims from the paper, not from memory.
4. **Write the review** as Markdown with front matter, citing papers only as `[@key]` or
   `[@a; @b]` (each listed paper at least once, nothing unlisted):

   ```markdown
   ---
   title: Robust estimation for retention-time alignment
   papers: [huber, olson2013, sunderhauf2012]
   tags: [robust-estimation]
   ---

   ## Why this matters to you
   (2–4 sentences tied to a named project or own-work paper.)

   ## The papers
   (One short paragraph each: the problem, the method, the evidence, with the numbers.)

   ## How they compare
   (A table: approach, assumptions, data, results, limits. Then where they agree and disagree.)

   ## For your work
   (What to borrow, what to test, what to cite and where. Concrete.)

   ## Reading order
   (Which to read first and why; which sections matter.)
   ```

   Write mathematics as TeX: `$\theta_i - \theta_j$` inline, `$$ … $$` on its own lines for
   display (it renders as MathML on the page; never write `theta_i` or `R_i^-1` as plain text).
   Keep it under ~1200 words; be specific, never generic. Don't put summaries in paper notes:
   Jesse's `## Notes` are for what he has read and understood himself.
5. **File it:** `ruru review add draft.md --by claude` (write the draft to a scratch file first).
   A review still `new` can be rewritten with `--replace`. Tell Jesse its id; he reads it with `o`
   in the TUI's Reviews tab or `ruru review open ID`.

## Agent notes

- `ruru note KEY --agent --kind KIND -m "…"` (or `-F file.md`, `-F -` for stdin) appends a dated,
  attributed entry under `## Agent notes`. It never touches Jesse's `## Notes`; never try to.
- Keep agent notes to short facts Jesse asked for (`methods`, `relevance` to a named project);
  syntheses belong in a review. Say what a note rests on: "Abstract only." or "Full text."
- Tag sparingly with `ruru tag KEY +tag`, reusing tags that exist (`ruru list --json` shows them).

## Before writing a section

1. `ruru list -p PROJECT --json` (papers Jesse kept for this paper) and `ruru search …` for the
   topic. Read the `paper.md` files (or at least abstracts and notes) of what you plan to cite.
2. Cite only keys that exist. If you need a paper the library lacks, stop and tell Jesse.
3. After editing the `.tex`, run `ruru check PROJECT --json` and report the issues: keys missing
   from the bib (`in_library: true` means `ruru check PROJECT --fix` would add them; that's Jesse's
   command to run), unused or stale entries, duplicates.

## Checking citations

Does each cited paper say what the sentence citing it claims? You read; ruru lists the claims and
keeps the verdicts (`claims/<project>.json` in the library, one commit per `ruru verdict`).

1. `ruru claims PROJECT --unchecked --json`: every sentence of the .tex that cites something, with a
   stable `id`, `file`:`line`, the `text` (cites written `[@key]`) and, per key, `in_library`,
   `has_pdf` and any `verdict`. `-k KEY` narrows it to one paper.
2. **PDFs first.** `ruru pdf missing --cited PROJECT --json` lists the cited papers without one.
   Don't download anything yourself: give Jesse one command to run,
   `ruru pdf missing --cited PROJECT --fetch --open` (it downloads the open-access ones, then opens
   the publisher page of each of the rest, at most 10 at a time), saving each as `KEY.pdf` in
   Downloads; then `ruru pdf add`. Keys `in_library: false` need `ruru add` first (Jesse's call).
3. **Read each PDF once** (`ruru pdf KEY` prints the path) and judge every claim citing it
   (`ruru claims PROJECT -k KEY --json`). Judge what the sentence attributes to that paper, not the
   rest of the sentence (often Jesse's own result).
4. **Record** with `ruru verdict PROJECT CLAIM KEY VERDICT --by claude`, or many at once as JSON
   lines with `-F file.jsonl` (`claim`, `key`, `verdict`, `from`, `page`, `quote`, `note`):
   - `supported`: `--quote` the paper's own words, verbatim, with `-p PAGE`;
   - `partial`: the quote plus `--note` on what is overstated or narrower;
   - `unsupported`: `--note` saying what the paper does say;
   - `not-found`: you read it and couldn't find the passage either way;
   - `background`: a general pointer ("see [x]", software, a dataset), nothing specific to check.

   `--from pdf` (the default) needs the PDF. Without one, either wait for Jesse, or say what the
   verdict rests on with `--from abstract` (abstract only) or `--from other` (a README, a web page);
   never `supported` from memory.
5. **Never edit the .tex.** Report to Jesse: the `unsupported` and `partial` claims with file:line,
   quote and note first, then `not-found`, then what is still unchecked and why (no PDF, not in the
   library). Rewording a sentence gives it a new id; old verdicts then show up as `stale` in
   `ruru claims PROJECT --json`, and `ruru verdict PROJECT CLAIM --drop` forgets one.

## Other things you may be asked

- `ruru refresh KEY` re-resolves metadata (a preprint published since); keys never change.
- `ruru enrich` fills empty abstracts and ids (BibTeX imports have none; `ruru add --bib` already
  starts it in the background). A paper still without an abstract afterwards has none upstream:
  say so, and don't write a summary from the title.
- `ruru pdf KEY --fetch` downloads an open-access PDF when one exists; it never bypasses paywalls.
  For paywalled papers Jesse downloads them himself (see "Reviews", step 2).
- `ruru export json` writes the whole library as one JSON document; `ruru export bib -p PROJECT`
  writes a project's papers as BibTeX.
- `ruru doctor` checks setup and API access when something seems wrong.
