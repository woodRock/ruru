# Recorded HTTP responses

Real responses from the arXiv, Crossref and OpenAlex APIs, recorded on 2026-10-17 with `curl`
(the OpenAlex ones with ruru's `select=` field list). Tests serve them through
`tests/httpfix.py`; nothing in the test suite touches the network.

| file | request |
|---|---|
| `arxiv_2103.13807.xml` | `export.arxiv.org/api/query?id_list=2103.13807` (preprint whose record carries the journal DOI) |
| `arxiv_1706.03762.xml` | `…?id_list=1706.03762` (no DOI in the arXiv record) |
| `arxiv_missing.xml` | `…?id_list=2699.99999` (empty feed) |
| `arxiv_search_chem.xml` | `…?search_query=cat:physics.chem-ph AND abs:chromatography&max_results=30&sortBy=submittedDate` |
| `crossref_s13321.json` | `api.crossref.org/works/10.1186/s13321-021-00544-w` (published version of 2103.13807, retitled) |
| `crossref_nature14539.json` | `api.crossref.org/works/10.1038/nature14539` |
| `crossref_missing.json` | `api.crossref.org/works/10.9999/does-not-exist` (404) |
| `openalex_doi_s13321.json` | `api.openalex.org/works/doi:10.1186/s13321-021-00544-w` (open-access PDF, pages) |
| `openalex_doi_nature14539.json` | `api.openalex.org/works/doi:10.1038/nature14539` (no OA PDF, no abstract) |
| `openalex_missing.json` | `api.openalex.org/works/doi:10.9999/does-not-exist` (404) |
| `openalex_title_attention.json` | `works?filter=title.search:attention is all you need` (two different works with that exact title) |
| `openalex_title_attention_article.json` | the same, `,type:article` |
| `openalex_title_fragment.json` | `works?filter=title.search:automated fragment identification for electron ionisation mass spectrometry` |

Synthetic responses (429s, 5xx, malformed bodies, PDF bytes) are built inline in the tests.
