# ruru JSON output

`--json` output is a public API: keys are only ever added, never renamed or removed, without a
major version bump. Dates are ISO 8601 (`YYYY-MM-DD`), ids are given in full, and missing
values are `null` (never omitted). stdout carries only the JSON document; warnings go to stderr.

## Paper object

Every front matter field of `paper.md`, in this order, then two derived fields.

| key | type | notes |
|---|---|---|
| `key` | string | citation key, e.g. `wood2026joint`; never changes once assigned |
| `title` | string | |
| `authors` | string[] | `Given Family`, in author order |
| `year` | int \| null | |
| `venue` | string \| null | journal, conference, school (thesis) or publisher (book) |
| `type` | string | `article` \| `inproceedings` \| `preprint` \| `book` \| `thesis` \| `misc` |
| `volume`, `issue`, `pages`, `publisher` | string \| null | `pages` as `1-12` |
| `ids` | object | `doi` (lowercase, no URL prefix), `arxiv` (no version), `openalex` (`W…`), `s2`; each string \| null. Other id kinds may appear. |
| `url`, `pdf_url` | string \| null | `pdf_url`: an open-access PDF, if known |
| `abstract` | string \| null | |
| `status` | string | `to-read` \| `later` \| `reading` \| `read` |
| `tags`, `projects` | string[] | sorted |
| `added`, `read_on` | date \| null | |
| `source` | string | `feed:<name>` \| `add` \| `import` |
| … | any | unknown front matter fields are passed through after `source` |
| `path` | string | `paper.md`, relative to the library root |
| `has_pdf` | bool | `paper.pdf` exists next to it |

`ruru show KEY --json` adds:

| key | type | notes |
|---|---|---|
| `notes` | string | Jesse's `## Notes` section (Markdown) |
| `agent_notes` | string | the `## Agent notes` section: `### YYYY-MM-DD — kind (by)` entries |

## Commands

| command | output |
|---|---|
| `ruru list --json` | array of paper objects, newest `added` first; `[]` (exit 2) if none match |
| `ruru search QUERY --json` | array of paper objects, each with `score` (int, higher is better), best first; `[]` (exit 2) if none match |
| `ruru show KEY --json` | one paper object with `notes` and `agent_notes`; exit 1 if the key is unknown |
| `ruru enrich --json` | array, one object per paper attempted: `key`, `error` (string \| null), `filled` (`{field: new value}`; `ids` is the whole new ids object; empty when nothing was found to fill). Exit 3 if some failed. With `--dry-run` nothing is written. |
| `ruru refresh --json` | array, one object per paper attempted: `key`, `error` (string \| null), `changes` (`{field: {"old": …, "new": …}}`, empty when up to date). Exit 3 if any failed. With `--dry-run` nothing is written. |

| `ruru inbox --json` | array of inbox item objects, newest first; `[]` (exit 2) if empty |
| `ruru show ID --json` | one inbox item object, for an id still in the inbox |
| `ruru fetch --json` | `{"feeds": [{"name", "new", "seen", "error"}], "new": [inbox ids]}`; exit 3 if any feed failed |
| `ruru feeds --json` | array of feed objects (the `[[feeds]]` fields in ruru.toml) plus `last_fetch` (date \| null) |
| `ruru find QUERY --json` | array of found works: bibliographic fields as in a paper object (no `key`, `status`, `tags`, `projects`, `added`, `read_on`, `source`), plus `id` (`doi:…`, `arXiv:…` or an OpenAlex id: what to pass to `ruru add`) and `in_library` (key \| null) |

## Inbox item object

| key | type | notes |
|---|---|---|
| `id` | string | stable within the inbox: `arxiv-2610.01234`, `doi-10.1021-acs.analchem.6c03760`, `oa-W…`, `rss-…` |
| `title` … `abstract` | | the bibliographic fields of a paper object, same types |
| `tags` | [string] | empty except on a paper moved back with `ruru unkeep` |
| `source` | string | `feed:<name>` |
| `published` | date \| null | as the feed reports it |
| `fetched` | datetime \| date | when it was fetched, `2026-10-19T18:34:59`; items fetched before 2026-10-19 evening have a date only. `ruru inbox` lists the newest fetch first |
| `suggestion` | object \| null | `{"action": "keep" \| "skip" \| "later", "reason": string, "by": string, "date": date}` |
| `path` | string | relative to the library root |
| `viewed` | bool | Jesse has looked at it in `ruru tui` (only the TUI marks items; the CLI never does) |

| `ruru review --json` (and `review list`) | array of review objects: `new` ones first, then by `--sort` (`created`, newest first, by default; `title`; `papers`, most first); `[]` (exit 2) if none |
| `ruru review show ID --json` | one review object, with `body` (the Markdown) |
| `ruru pdf missing --json` | array of `{"key", "title", "link" (DOI or URL \| null), "save_as": "KEY.pdf"}`; `[]` (exit 2) when nothing is missing (`--cited PROJECT`: the papers its claims cite; `--fetch` first downloads open-access ones) |

## Review object

| key | type | notes |
|---|---|---|
| `id` | string | `<created>-<title slug>`; the file is `reviews/<id>.md` |
| `title` | string | |
| `papers` | [string] | the keys it covers, each cited in the body as `[@key]` |
| `tags` | [string] | |
| `status` | string | `new` \| `kept` |
| `created` | datetime \| date | when it was filed, `2026-10-18T16:45:12`; reviews filed before 2026-10-18 evening have a date only |
| `by` | string \| null | the agent that wrote it |
| `path` | string | relative to the library root |

| `ruru project --json` | array of `{"name", "path", "bib", "tex": [string], "bib_encoding": string \| null}` (`path` as written in ruru.toml) |
| `ruru check TARGET --json` | the check report below; exit 2 if `issue_count` > 0 |

## Check report

```json
{
  "project": "joint-align",            // null for `ruru check FILE.tex`
  "tex": ["/…/main.tex", "/…/sections/intro.tex"],   // every file read, \\input/\\include followed
  "bib": "/…/references.bib",          // null if none was found
  "cited": 34,                          // distinct keys cited
  "issues": {
    "missing": [{"key": "smith2020x", "in_library": false}],   // cited, not in the bib file
    "unused": ["old2019y"],             // in ruru's managed block, never cited
    "kept_unused": ["zhang2024z"],      // papers with this project in `projects`, never cited
    "stale": ["wood2026joint"],         // managed entry differs from what the library generates now
    "duplicates": [["a2020x", "b2020x"]] // the same paper under two keys anywhere in the bib file
  },
  "issue_count": 5,
  "warnings": ["tex file not found: /…/appendix.tex"],
  "fixed": {"cited": [], "regenerated": [], "pruned": []}   // only with --fix / --prune; {} otherwise
}
```

| `ruru claims PROJECT --json` | the claims document below; exit 2 if no claim matches the filters |

## Claims document

```json
{
  "project": "joint-align",
  "claims": [{
    "id": "04f06f26cd",                  // hash of the text: rewording gives a new id
    "file": "main.tex",                   // relative to the main .tex's directory
    "line": 150,                          // of the first cite in the sentence
    "keys": ["huber"],
    "text": "The first three rounds use Huber weights [@huber] with threshold $1.345\\hat\\sigma$.",
    "checks": [{"key": "huber", "title": "Robust estimation of a location parameter",  // null if not in the library
                "in_library": true, "has_pdf": true,
                "own": false,             // Jesse's own work (`ruru own`): needs no verdict
                "verdict": null}]         // or a verdict object
  }],
  "stale": [ … ]                          // verdict objects whose sentence is no longer in the tex
}
```

`--verdict` filters on a cited key's state: a verdict, `unchecked` or `own`.

`ruru claims PROJECT --summary --json` (filters apply first):

```json
{
  "project": "joint-align", "claims": 31, "keys": 21,
  "counts": {"supported": 22, "partial": 0, "unsupported": 0, "not-found": 0, "background": 0,
             "unchecked": 0, "own": 13},          // over (claim, key) pairs
  "problems": [{"claim", "key", "file", "line", "text", "verdict"}],  // unsupported, partial, not-found
  "no_pdf": ["kovats"],                         // cited, in the library, no PDF (own work aside)
  "not_in_library": [],                         // own work aside
  "stale": 0                                    // verdicts on sentences no longer in the tex
}
```

A verdict object: `claim`, `key`, `verdict` (`supported` \| `partial` \| `unsupported` \|
`not-found` \| `background`), `source` (`pdf` \| `abstract` \| `other`), `page`, `quote`, `note`
(string \| null), `by` (string \| null), `date`, and `text` (the claim as it read when judged).

| `ruru own --json` | array of `{"key", "in_library"}`: library papers tagged `own-work` and the keys in ruru.toml's `own`, sorted; `[]` (exit 2) if none |
| `ruru seen --json` | array of triage records from seen.jsonl, newest first: `id`, `decision` (`keep` \| `skip` \| `later` \| `remove`: `ruru rm`, keyed by the paper's key), `date`, `key` (string \| null), `ids`, `title`, `year`, and `by` and `reason` when an agent decided |
| `ruru doctor --json` | array of `{"name", "status": "ok" \| "warn" \| "fail", "detail"}`; exit 3 if any failed |

## Export document (`ruru export json`)

```json
{
  "schema_version": 1,
  "generated": "2026-10-17T08:29:52+00:00",
  "library": [ … ],     // paper objects, as `ruru show KEY --json` (with notes and agent_notes;
                        // without them under --no-notes), sorted by key
  "inbox": [ … ],       // inbox item objects, as `ruru inbox --json`
  "feeds": [ … ],       // as `ruru feeds --json`
  "projects": [ … ],    // as `ruru project --json`
  "activity": [{"date": "2026-10-17T20:31:02+13:00", "message": "ruru: keep wood2026joint"}]
                        // the library's last 100 commits, newest first
}
```

`schema_version` changes only when a key is renamed or removed; new keys may appear at any time.
