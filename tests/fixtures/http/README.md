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

| `openalex_sources_analchem.json` | `api.openalex.org/sources?search=analytical chemistry` (wizard) |
| `openalex_authors_xue.json` | `api.openalex.org/authors?search=Bing Xue` (wizard; two namesakes at VUW) |
| `openalex_feed_author.json` | `works?filter=authorships.author.id:A5077569089,from_publication_date:2026-09-01` |
| `openalex_feed_search.json` | `works?filter=title_and_abstract.search:"retention time alignment",from_publication_date:2026-01-01` (trimmed to 5 results) |
| `openalex_find.json` | `works?search=retention time alignment gc-ms&per-page=5` |
| `crossref_journal_analchem.json` | `api.crossref.org/journals/0003-2700/works?filter=from-created-date:2026-10-12` (trimmed to 5 items, `reference` lists removed) |
| `rss_wiley_jat.xml` | Journal of Applied Toxicology RSS (Wiley; DOIs in `prism:doi`), trimmed to 3 items |
| `crossref_wiley_{1,2,3}.json` | Crossref records for those three DOIs |
| `rss_sciencedirect_jca.xml` | Journal of Chromatography A RSS (ScienceDirect; no DOIs), trimmed to 3 items |

ACS's RSS (`pubs.acs.org/action/showFeed…`) answers scripts with a Cloudflare challenge (403), so
it is not recorded; tests simulate that failure.

Synthetic responses (429s, 5xx, malformed bodies, PDF bytes) are built inline in the tests.
