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

Inbox items, `check` reports and `export json` are documented here as they land (M3–M4).
