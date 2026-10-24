# ruru JSON output

`--json` output is a public API: keys are only ever added, never renamed or removed, without a
major version bump. Dates are ISO 8601 (`YYYY-MM-DD`), ids are given in full, and missing
values are `null` (never omitted). stdout carries only the JSON document; warnings go to stderr. `ruru schema` prints all of this as
JSON Schema (`ruru schema inbox` for one command, `ruru schema paper` for one object, `-q` lists the
names); the tests validate real output against it. A command that finds nothing prints `[]` and exits 2.

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
| `ruru search QUERY --json` | array of paper objects, each with `score` (int, higher is better), `in_pdf` (bool: the words are in the PDF's text; only with `--fulltext`) and `snippet` (string \| null: where in the PDF), best first; `[]` (exit 2) if none match. `--fulltext` also finds papers by the text of their PDFs: such a hit scores 1, and a paper found both ways scores one more |
| `ruru show KEY --json` | one paper object with `notes` and `agent_notes`; exit 1 if the key is unknown |
| `ruru enrich --json` | array, one object per paper attempted: `key`, `error` (string \| null), `filled` (`{field: new value}`; `ids` is the whole new ids object; empty when nothing was found to fill). Exit 3 if some failed. With `--dry-run` nothing is written. |
| `ruru refresh --json` | array, one object per paper attempted: `key`, `error` (string \| null), `changes` (`{field: {"old": …, "new": …}}`, empty when up to date). Exit 3 if any failed. With `--dry-run` nothing is written. |

| `ruru inbox --json` | array of inbox item objects, most relevant first (`--sort date`: newest fetch first; `inbox_sort` in ruru.toml sets the default; while the library has fewer than 5 papers the order is by date); `[]` (exit 2) if empty |
| `ruru related KEY\|ID --json` | array, closest first (`-n`, default 10; `--in library\|inbox\|all`): `id` (a library key or an inbox id), `kind` (`library` \| `inbox`), `similarity` (0–1), `title`, `authors`, `year`, `venue`, `status` (a library paper's, else null); `[]` (exit 2) if nothing resembles it |
| `ruru dupes --json` | array of near-duplicate pairs, most similar first: `a` (a library key, or the earlier inbox id), `b` (an inbox id, or a second key), `similarity`, `same` (`ruru`'s dedupe rule already calls them one paper), `a_title`, `b_title`; `[]` (exit 2) if none |
| `ruru pdf text KEY --json` | `{"key", "pages", "page" (the `--page` asked for, else null), "text"}`; pages are separated by a form feed in the whole text |
| `ruru index --json` | the index's state: `path`, `exists`, `bytes`, `papers`, `inbox` (documents counted), `pdfs` (PDFs read), `pdfs_failed`, `full_text_search` |
| `ruru index update --json` | `{"papers", "inbox", "pdfs": null \| {"extracted": [keys], "failed": {key: why}, "current"}}`; exit 3 if a PDF failed |
| `ruru undo --json` | array of `{"sha", "date", "message", "files", "reverted_as"}` for the commits undone (`--list`, `--dry-run`: `reverted_as` is null); `[]` (exit 2) if the newest commit is not ruru's |
| `ruru batch -F FILE` | array, one per input line: `{"argv", "code" (that command's exit code), "stdout", "stderr", "data" (stdout parsed as JSON, else null)}`; exit 3 if some commands failed (exit 2 "no match" results are answers, not failures). Only commands agents may run one by one are allowed |
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
| `relevance` | number \| null | 0–1: how close the item is to the library (TF-IDF cosine to the three nearest papers, less a penalty for resembling skipped titles). null while the library has fewer than 5 papers |
| `similar_to` | [object] | the nearest library papers, closest first: `{"key", "similarity"}`; empty when unranked |
| `possible_duplicate_of` | string \| null | a library key, or an earlier inbox id, this item is very likely the same work as (`ruru dupes`) |

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
                "own": false,             // Jesse's own work (`ruru own`): a label only
                "kind": "paper",          // paper | draft | software ([cited]) | null (neither)
                "read": ["/…/papers/huber/paper.pdf"],   // what to check it against: the PDF, a draft's
                                          // .tex files, software's README/CITATION.cff; [] if nothing yet
                "version": null,          // draft `tex:<hash>`, software `git:<commit>[-dirty]`: now
                "state": "unchecked",     // the verdict, unchecked, or outdated
                "verdict": null}]         // or a verdict object
  }],
  "stale": [ … ]                          // verdict objects whose sentence is no longer in the tex
}
```

`--verdict` filters on a cited key's state (a verdict, `unchecked`, `outdated`) or `own`.
`--unchecked` is `--verdict unchecked outdated`.

`ruru claims PROJECT --summary --json` (filters apply first):

```json
{
  "project": "joint-align", "claims": 31, "keys": 21,
  "counts": {"supported": 22, "partial": 0, "unsupported": 0, "not-found": 0, "background": 0,
             "unchecked": 0, "outdated": 0,       // over (claim, key) pairs
             "own": 13},                          // pairs citing Jesse's own work (also counted above)
  "problems": [{"claim", "key", "file", "line", "text", "verdict"}],  // unsupported, partial, not-found
  "no_pdf": ["kovats"],                         // cited, in the library, no PDF
  "not_in_library": [],                         // neither in the library nor a cited draft or software
  "stale": 0                                    // verdicts on sentences no longer in the tex
}
```

A verdict object: `claim`, `key`, `verdict` (`supported` \| `partial` \| `unsupported` \|
`not-found` \| `background`), `source` (`pdf` \| `abstract` \| `other`), `page`, `quote`, `note`
(string \| null), `by` (string \| null), `date`, `text` (the claim as it read when judged), and
`version` (string \| null: the draft's or software's version it was judged on). `source` may also
be `draft` or `software`.

| `ruru own --json` | array of `{"key", "in_library", "kind"}` (`paper` \| `draft` \| `software` \| `unknown`: the old `own` list): library papers tagged `own-work` and every cited draft and software, sorted; `[]` (exit 2) if none |
| `ruru cited --json` | array of `{"key", "kind", "project", "path", "url", "link" (url, else CITATION.cff's), "version", "read"}` sorted by key; `[]` (exit 2) if none |
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
