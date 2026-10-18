import json
import sys
import time

import pytest

from conftest import git_log
from httpfix import FIXTURES, Route, Router
from ruru import cli, http, model
from ruru.http import Client, HttpError, NotFound
from ruru.model import Paper
from ruru.resolve import (
    AmbiguousTitle,
    Ident,
    Resolver,
    arxiv,
    crossref,
    fill_gaps,
    merge,
    openalex,
    parse_identifier,
)

FRAGMENT_PREPRINT = ("Automated fragment identification for electron ionisation mass spectrometry: "
                     "application to atmospheric measurements of halocarbons")
FRAGMENT_PUBLISHED = ("Automated fragment formula annotation for electron ionisation, high resolution mass "
                      "spectrometry: application to atmospheric measurements of halocarbons")


@pytest.fixture
def resolver(net):
    return Resolver(Client(email="test@example.invalid"))


@pytest.mark.parametrize(("text", "ident"), [
    ("10.1038/nature14539", Ident("doi", "10.1038/nature14539")),
    ("https://doi.org/10.1038/NATURE14539", Ident("doi", "10.1038/nature14539")),
    ("doi:10.1021/acs.analchem.5b00001.", Ident("doi", "10.1021/acs.analchem.5b00001")),
    ("https://pubs.acs.org/doi/10.1021/ac0518219?ref=x", Ident("doi", "10.1021/ac0518219")),
    ("https://link.springer.com/content/pdf/10.1007/s00216-020-02889-0.pdf",
     Ident("doi", "10.1007/s00216-020-02889-0")),
    ("10.48550/arXiv.2103.13807", Ident("arxiv", "2103.13807")),
    ("2103.13807v2", Ident("arxiv", "2103.13807")),
    ("arXiv:2103.13807", Ident("arxiv", "2103.13807")),
    ("https://arxiv.org/pdf/2103.13807v1", Ident("arxiv", "2103.13807")),
    ("hep-th/9901001", Ident("arxiv", "hep-th/9901001")),
    ("https://openalex.org/W3202291011", Ident("openalex", "W3202291011")),
    ("w123", Ident("openalex", "W123")),
    ("  Deep learning ", Ident("title", "Deep learning")),
])
def test_parse_identifier(text, ident):
    assert parse_identifier(text) == ident


@pytest.mark.parametrize("text", ["https://example.org/some/page", "   "])
def test_parse_identifier_rejects(text):
    with pytest.raises(cli.UsageError):
        parse_identifier(text)


def test_arxiv_with_published_doi_resolves_to_journal_version(resolver):
    p = resolver.resolve("2103.13807")
    assert p.title == FRAGMENT_PUBLISHED
    assert (p.type, p.venue, p.year) == ("article", "Journal of Cheminformatics", 2021)
    assert (p.volume, p.issue) == ("13", "1")
    assert p.pages == "78"  # Crossref has none; OpenAlex fills it
    assert p.ids == {"doi": "10.1186/s13321-021-00544-w", "arxiv": "2103.13807", "openalex": "W3202291011",
                     "s2": None}
    assert p.pdf_url == "https://jcheminf.biomedcentral.com/track/pdf/10.1186/s13321-021-00544-w"
    assert p.authors[2] == "Martin K. Vollmer"
    assert p.abstract and "<jats" not in p.abstract


def test_arxiv_preprint_without_doi(resolver):
    p = resolver.resolve("1706.03762")
    assert (p.type, p.year, p.ids["arxiv"], p.ids["doi"]) == ("preprint", 2017, "1706.03762", None)
    assert p.authors[0] == "Ashish Vaswani" and p.pdf_url == "https://arxiv.org/pdf/1706.03762"


def test_doi_merges_crossref_and_openalex(resolver):
    p = resolver.resolve("https://doi.org/10.1038/nature14539")
    assert (p.title, p.year, p.venue) == ("Deep learning", 2015, "Nature")
    assert p.authors == ["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"]
    assert (p.volume, p.issue, p.pages) == ("521", "7553", "436-444")
    assert p.ids["openalex"] == "W2919115771" and p.pdf_url is None


def test_unknown_ids(resolver):
    with pytest.raises(NotFound):
        resolver.resolve("10.9999/does-not-exist")
    with pytest.raises(NotFound):
        resolver.resolve("2699.99999")


def test_title_exact_and_unique(resolver):
    p = resolver.resolve(FRAGMENT_PREPRINT)
    assert (p.type, p.ids["arxiv"], p.ids["doi"]) == ("preprint", "2103.13807", None)
    assert p.venue is None  # arXiv is a repository, not a venue


def test_title_ambiguous_is_refused(resolver):
    with pytest.raises(AmbiguousTitle) as exc:
        resolver.resolve("Attention Is All You Need")
    assert "doi:10.65215/2q58a426" in str(exc.value) and "doi:10.1201/9781003561460-19" in str(exc.value)


def test_title_year_filter_and_no_match(resolver):
    with pytest.raises(NotFound, match="near 2017"):
        resolver.resolve("Attention Is All You Need", year=2017)  # the 2017 paper is not in these results


def test_openalex_outage_degrades_to_crossref(net, capsys):
    router = Router().add("openalex", status=503).add("crossref", "crossref_nature14539.json")
    p = Resolver(Client(transport=router, retries=0)).by_doi("10.1038/nature14539")
    assert p.title == "Deep learning" and p.ids["openalex"] is None
    assert "continuing without OpenAlex" in capsys.readouterr().err


def test_datacite_doi_falls_back_to_openalex():
    work = {"id": "https://openalex.org/W9", "doi": "https://doi.org/10.5281/zenodo.1", "title": "Data",
            "type": "dataset", "publication_year": 2024, "authorships": []}
    router = Router().add("crossref", status=404).add("openalex", body=json.dumps(work))
    p = Resolver(Client(transport=router)).by_doi("10.5281/zenodo.1")
    assert (p.title, p.type, p.ids["doi"], p.ids["openalex"]) == ("Data", "misc", "10.5281/zenodo.1", "W9")


def test_by_openalex_enriches_from_crossref(net):
    body = (FIXTURES / "openalex_doi_s13321.json").read_bytes()
    net.routes.insert(0, Route(r"api\.openalex\.org/works/W3202291011", 200, body, {}, None))
    p = Resolver(Client()).resolve("W3202291011")
    assert p.publisher == "Springer Science and Business Media LLC"  # from Crossref
    assert p.ids["openalex"] == "W3202291011"


def test_by_openalex_without_crossref_record():
    work = {"id": "https://openalex.org/W5", "doi": "https://doi.org/10.5281/zenodo.5", "title": "T",
            "type": "article", "publication_year": 2020}
    router = Router().add("openalex", body=json.dumps(work)).add("crossref", status=404)
    assert Resolver(Client(transport=router)).resolve("W5").title == "T"


def test_title_match_with_doi_uses_crossref():
    works = {"results": [{"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1038/nature14539",
                          "title": "Deep learning", "type": "article", "publication_year": 2015}]}
    router = (Router().add(r"openalex\.org/works\?", body=json.dumps(works))
              .add("crossref", "crossref_nature14539.json"))
    p = Resolver(Client(transport=router)).resolve("Deep learning", year=2015)
    assert p.authors[0] == "Yann LeCun" and p.ids["openalex"] == "W1"
    router = Router().add(r"openalex\.org/works\?", body=json.dumps(works)).add("crossref", status=404)
    assert Resolver(Client(transport=router)).resolve("Deep learning").ids["openalex"] == "W1"


def test_arxiv_parsing_edge_cases():
    with pytest.raises(HttpError, match="malformed"):
        arxiv.parse_feed("<html><body>Service unavailable")
    error_feed = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id>'
                  "<summary>incorrect id format for 1234</summary></entry></feed>")
    with pytest.raises(NotFound, match="incorrect id format"):
        arxiv.parse_feed(error_feed)
    papers = arxiv.parse_feed((FIXTURES / "arxiv_search_chem.xml").read_text())
    assert len(papers) == 30 and all(p.type == "preprint" and p.ids["arxiv"] for p in papers)


def test_arxiv_query(net):
    net.add(r"search_query=cat", "arxiv_search_chem.xml")
    papers = arxiv.query(Client(), "cat:physics.chem-ph AND abs:chromatography", max_results=30)
    assert len(papers) == 30 and all(d is not None for _, d in papers)
    assert "sortBy=submittedDate" in net.urls()[-1]


def test_crossref_markup_and_types():
    assert crossref.strip_markup("<jats:title>Abstract</jats:title><jats:p>Fish &amp; <i>oil</i></jats:p>") == \
        "Fish & oil"
    assert crossref.strip_markup("") is None
    m = {"type": "posted-content", "title": [], "author": [{"name": "HUPO"}], "created": {"date-parts": [[2020]]},
         "DOI": "10.1/X"}
    p = crossref.to_paper(m)
    assert (p.type, p.title, p.authors, p.year, p.ids["doi"]) == ("preprint", "", ["HUPO"], 2020, "10.1/x")
    assert crossref.to_paper({"type": "other"}).year is None


def test_openalex_helpers():
    assert openalex.abstract({"b": [1], "a": [0, 2]}) == "a b a"
    assert openalex.abstract(None) is None
    assert openalex._pages({"first_page": "5", "last_page": "5"}) == "5"
    assert openalex._pages({}) is None
    w = {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.48550/arxiv.2103.13807", "type": "article",
         "title": "T", "primary_location": {"source": {"type": "repository", "display_name": "arXiv"},
                                            "landing_page_url": "https://arxiv.org/abs/2103.13807"}}
    p = openalex.to_paper(w)
    assert (p.type, p.ids["arxiv"], p.ids["doi"], p.venue, p.url) == (
        "preprint", "2103.13807", None, None, "https://arxiv.org/abs/2103.13807")


def test_merge_fills_gaps_only():
    a = Paper(title="A", type="misc")
    a.ids["doi"] = "10.1/a"
    b = Paper(title="B", venue="V", type="article")
    b.ids.update(doi="10.1/b", arxiv="1")
    m = merge(a, b)
    assert (m.title, m.venue, m.type, m.ids["doi"], m.ids["arxiv"]) == ("A", "V", "article", "10.1/a", "1")
    assert a.venue is None  # inputs untouched


# --- CLI -----------------------------------------------------------------------------------------

def test_cli_add_by_ids(lib, run, net):
    r = run("add", "2103.13807", "https://doi.org/10.1038/nature14539", "-t", "gc-ms")
    assert r.code == 0
    assert "added guillevic2021automated: Automated fragment formula annotation" in r.out
    assert "added lecun2015deep: Deep learning (2015)" in r.out
    assert git_log(lib)[0] == "ruru: add guillevic2021automated lecun2015deep (tags: gc-ms)"
    p = json.loads(run("show", "guillevic2021automated", "--json").out)
    assert p["source"] == "add" and p["tags"] == ["gc-ms"] and p["ids"]["arxiv"] == "2103.13807"
    # Known ids answer from the library, without any request.
    before = len(net.requests)
    r = run("add", "arXiv:2103.13807v1", "10.1186/S13321-021-00544-W", "-q")
    assert (r.code, r.out) == (0, "guillevic2021automated\nguillevic2021automated\n")
    assert len(net.requests) == before


def test_cli_add_failures_and_exit_codes(lib, run, net):
    r = run("add", "10.9999/does-not-exist")
    assert r.code == cli.USAGE and "not found" in r.err
    r = run("add", "Attention Is All You Need")
    assert r.code == cli.USAGE and "3 works" not in r.err and "2 works are titled" in r.err
    r = run("add", "1706.03762", "10.9999/does-not-exist", "-q")
    assert (r.code, r.out) == (cli.PARTIAL, "vaswani2017attention\n")
    assert run("add", "1706.03762", "--title", "x").code == cli.USAGE


def test_cli_add_network_failure_is_partial(lib, run, monkeypatch):
    monkeypatch.setattr(http, "default_transport", Router().add(".", status=503))
    r = run("add", "10.1038/nature14539")
    assert r.code == cli.PARTIAL and "503" in r.err
    assert run("list").code == cli.NO_MATCH


def test_cli_refresh_preprint_to_published(lib, run, net):
    r = run("add", "--title", FRAGMENT_PREPRINT, "--author", "Myriam Guillevic", "--year", "2021",
            "--type", "preprint", "--arxiv", "2103.13807", "-t", "gc-ms", "-q")
    assert r.out == "guillevic2021automated\n"
    run("note", "guillevic2021automated", "--agent", "-m", "Keep me.")
    r = run("refresh", "guillevic2021automated", "--dry-run")
    assert "(dry run)" in r.out and "type: preprint -> article" in r.out
    assert model.loads((lib / "papers/guillevic2021automated/paper.md").read_text()).type == "preprint"
    rows = json.loads(run("refresh", "--json").out)
    assert rows[0]["key"] == "guillevic2021automated" and rows[0]["error"] is None
    assert rows[0]["changes"]["title"] == {"old": FRAGMENT_PREPRINT, "new": FRAGMENT_PUBLISHED}
    p = json.loads(run("show", "guillevic2021automated", "--json").out)
    assert (p["key"], p["type"], p["ids"]["doi"], p["tags"]) == (
        "guillevic2021automated", "article", "10.1186/s13321-021-00544-w", ["gc-ms"])
    assert "Keep me." in p["agent_notes"] and p["source"] == "add"
    assert git_log(lib)[0] == "ruru: refresh guillevic2021automated"
    assert run("refresh").out == "guillevic2021automated: up to date\n"
    assert run("refresh", "-q").out == ""


def test_cli_refresh_unpublished_preprint_and_failures(lib, run, net):
    run("add", "1706.03762")
    assert run("refresh").out == "vaswani2017attention: up to date\n"  # no published version found
    run("add", "--title", "Unfindable", "--year", "2020")
    net.add(r"title\.search%3AUnfindable", body='{"results": []}')
    r = run("refresh")
    assert r.code == cli.PARTIAL and "unfindable" in r.err.lower()
    r = run("refresh", "anon2020unfindable", "--json")
    assert r.code == cli.USAGE and json.loads(r.out)[0]["error"]


def test_cli_refresh_by_openalex_id(lib, run, net):
    body = (FIXTURES / "openalex_doi_nature14539.json").read_bytes()
    net.routes.insert(0, Route(r"api\.openalex\.org/works/W2919115771", 200, body, {}, None))
    run("add", "--title", "Deep learning (draft)", "--year", "2015", "--no-commit")
    key = run("list", "-q").out.strip()
    path = lib / "papers" / key / "paper.md"
    paper = model.loads(path.read_text())
    paper.ids["openalex"] = "W2919115771"
    path.write_text(model.dumps(paper))
    run("refresh", key)
    assert json.loads(run("show", key, "--json").out)["title"] == "Deep learning"


PDF = b"%PDF-1.4\n% synthetic test bytes\n"


def test_cli_pdf(lib, run, net, monkeypatch):
    run("add", "10.1186/s13321-021-00544-w", "-q")
    key = "guillevic2021automated"
    assert run("pdf", key).code == cli.NO_MATCH
    net.add(r"jcheminf\.biomedcentral\.com/track/pdf/", body=PDF)
    r = run("pdf", key, "--fetch")
    assert r.code == 0 and r.out.strip().endswith(f"papers/{key}/paper.pdf")
    assert (lib / "papers" / key / "paper.pdf").read_bytes() == PDF
    assert "paper.pdf" not in " ".join(git_log(lib))  # git-ignored
    n = len(net.requests)
    assert run("pdf", key, "--fetch").code == 0 and len(net.requests) == n  # already there
    opened = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, check: opened.append(cmd))
    assert run("pdf", key, "--open").code == 0 and opened[0][1].endswith("paper.pdf")
    assert json.loads(run("show", key, "--json").out)["has_pdf"] is True


def test_cli_pdf_finds_url_by_refreshing(lib, run, net):
    run("add", "--title", FRAGMENT_PUBLISHED, "--year", "2021", "--doi", "10.1186/s13321-021-00544-w", "-q")
    key = run("list", "-q").out.strip()
    net.add(r"jcheminf\.biomedcentral\.com/track/pdf/", body=PDF)
    assert run("pdf", key, "--fetch").code == 0
    assert json.loads(run("show", key, "--json").out)["pdf_url"].startswith("https://jcheminf")
    assert git_log(lib)[0] == f"ruru: pdf_url {key}"


def test_cli_pdf_failures(lib, run, net):
    run("add", "10.1038/nature14539", "-q")
    r = run("pdf", "lecun2015deep", "--fetch")
    assert r.code == cli.NO_MATCH and "no open-access PDF" in r.err
    run("add", "10.1186/s13321-021-00544-w", "-q")
    net.add(r"jcheminf.*", body="<html>Sign in</html>", times=1)
    r = run("pdf", "guillevic2021automated", "--fetch")
    assert r.code == cli.PARTIAL and "not a PDF" in r.err
    net.add(r"jcheminf.*", status=403, times=1)
    assert run("pdf", "guillevic2021automated", "--fetch").code == cli.PARTIAL


def test_fill_gaps_never_changes_what_is_there():
    mine = Paper(title="Deep Learning (my casing)", authors=["Y. LeCun"], type="misc", venue="Nature")
    mine.ids["doi"] = "10.1038/nature14539"
    found = Paper(title="Deep learning", authors=["Yann LeCun", "Yoshua Bengio"], type="article",
                  venue="Nature (London)", volume="521", abstract="Deep learning allows…")
    found.ids.update(doi="10.1038/other", openalex="W1")
    out = fill_gaps(mine, found)
    assert (out.title, out.authors, out.type, out.venue) == (mine.title, mine.authors, "misc", "Nature")
    assert (out.volume, out.abstract) == ("521", "Deep learning allows…")
    assert out.ids["doi"] == "10.1038/nature14539" and out.ids["openalex"] == "W1"
    assert mine.abstract is None  # a copy


IMPORT_BIB = rf"""
@article{{lecun2015, author = {{LeCun, Yann and Bengio, Yoshua and Hinton, Geoffrey}},
  title = {{Deep learning}}, journal = {{Nature}}, year = {{2015}}, doi = {{10.1038/nature14539}}}}
@article{{guillevic2021, author = {{Guillevic, Myriam}}, title = {{{FRAGMENT_PREPRINT}}}, year = {{2021}}}}
@misc{{nowhere2020, title = {{Unfindable software}}, year = {{2020}}}}
"""


def test_cli_add_bib_enriches_in_background(lib, run, spawned, tmp_path):
    bib = tmp_path / "refs.bib"
    bib.write_text(IMPORT_BIB)
    r = run("add", "--bib", str(bib), "-q")  # offline: the empty router would fail any request
    keys = r.out.split()
    assert r.code == cli.OK and len(keys) == 3 and "in the background" in r.err
    assert len(spawned) == 1 and spawned[0][2:4] == ["ruru", "enrich"] and spawned[0][-3:] == keys
    assert run("add", "--bib", str(bib)).code == cli.OK and len(spawned) == 1  # nothing new, nothing started
    bib.write_text("@misc{x, title = {Another}, year = {2020}}")
    run("add", "--bib", str(bib), "--no-enrich", "--no-commit")
    assert len(spawned) == 1
    run("add", "10.1038/nature14539")  # already in the library; ids never start it
    assert len(spawned) == 1


def test_cli_enrich_fills_gaps_only(lib, run, net, tmp_path):
    bib = tmp_path / "refs.bib"
    bib.write_text(IMPORT_BIB)
    run("add", "--bib", str(bib), "--no-enrich")
    net.add(r"title\.search%3AUnfindable", body='{"results": []}')
    r = run("enrich", "--dry-run")
    assert r.code == cli.PARTIAL and "unfindable" in r.err.lower()
    assert "guillevic2021automated: url, pdf_url, abstract, ids (dry run)" in r.out
    assert json.loads(run("show", "guillevic2021automated", "--json").out)["abstract"] is None
    rows = {row["key"]: row for row in json.loads(run("enrich", "--json").out)}
    assert rows["anon2020unfindable"]["error"] and rows["anon2020unfindable"]["filled"] == {}
    assert "abstract" not in rows["lecun2015deep"]["filled"]  # neither Crossref nor OpenAlex has one
    g = json.loads(run("show", "guillevic2021automated", "--json").out)
    assert g["abstract"] and (g["title"], g["type"]) == (FRAGMENT_PREPRINT, "article")  # the .bib's, kept
    assert g["ids"]["arxiv"] == "2103.13807" and g["ids"]["doi"] is None
    assert json.loads(run("show", "lecun2015deep", "--json").out)["ids"]["openalex"]
    assert git_log(lib)[0] == "ruru: enrich guillevic2021automated lecun2015deep"
    r = run("enrich", "-q")  # papers with an abstract are no longer looked up
    assert r.code == cli.PARTIAL and r.out == "" and "guillevic" not in r.err
    r = run("enrich", "guillevic2021automated")  # its arXiv record now leads to the retitled article
    assert r.code == cli.USAGE and "titled 'Automated fragment formula" in r.err
    assert json.loads(run("show", "guillevic2021automated", "--json").out)["venue"] is None
    assert run("enrich", "lecun2015deep").out == "lecun2015deep: nothing found to fill\n"


def test_cli_enrich_refuses_another_papers_record(lib, run, net):
    body = (FIXTURES / "openalex_doi_nature14539.json").read_bytes()
    net.routes.insert(0, Route(r"api\.openalex\.org/works/W2919115771", 200, body, {}, None))
    run("add", "--title", "Deep learning", "--year", "2015", "--doi", "10.1038/nature14539",
        "--abstract", "Here already.")
    run("add", "--title", "Deep learning", "--year", "2012", "--no-commit")  # same title, too far apart
    key = "anon2012deep"
    path = lib / "papers" / key / "paper.md"
    paper = model.loads(path.read_text())
    paper.ids["openalex"] = "W2919115771"  # wrongly: that work is the first paper
    path.write_text(model.dumps(paper))
    r = run("enrich")
    assert r.code == cli.USAGE and "anon2015deep" in r.err and "same id" in r.err
    assert model.loads(path.read_text()).ids.get("doi") is None


def test_cli_enrich_nothing_to_do(lib, run):
    assert run("enrich").code == cli.OK


REAL_SPAWN = cli.spawn  # captured at import, before conftest replaces it


def test_spawn_detaches_and_logs(tmp_path):
    log = tmp_path / "logs" / "enrich.log"
    REAL_SPAWN([sys.executable, "-c", "print('hello from the background')"], log)
    deadline = time.monotonic() + 10
    while "hello" not in (log.read_text() if log.exists() else "") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert log.read_text() == "hello from the background\n"
