"""Ranking the inbox, `related`, `dupes` and `index`, end to end in a small library."""

import json
from datetime import date, datetime

import pytest

from ruru import cli, gitops, inbox, model, pdftext
from ruru.library import Library

CHROMATOGRAPHY = [
    ("Retention time alignment of GC-MS chromatograms", "We align retention times of gas chromatography mass "
     "spectrometry peaks with a robust warping model.", ["Ada Lovelace", "Bo Chen"], 2023),
    ("Peak detection in gas chromatography", "Detecting peaks in gas chromatography signals with wavelets and "
     "baseline correction.", ["Cy Young", "Bo Chen"], 2022),
    ("Baseline correction for chromatograms", "Asymmetric least squares baseline correction of chromatograms "
     "before peak alignment.", ["Di Park", "Ed King"], 2021),
    ("Warping chromatograms with dynamic programming", "Correlation optimised warping aligns chromatograms; "
     "retention drift is removed before peak matching.", ["Fay Wu", "Gus Lee"], 2020),
    ("Wavelet denoising of chromatographic signals", "Wavelet transforms remove noise from chromatographic "
     "signals while keeping peak shapes.", ["Hal Ito", "Ivy Ng"], 2019),
    ("Mass spectrometry peak picking", "Picking peaks in mass spectrometry data after retention time "
     "alignment.", ["Jo Rae", "Kim Su"], 2018),
]


def add_paper(run, title, abstract, authors, year):
    args = ["add", "--title", title, "--abstract", abstract, "--year", str(year), "--no-enrich", "-q"]
    for a in authors:
        args += ["--author", a]
    r = run(*args)
    assert r.code == 0, r.err
    return r.out.strip()


def key_of(run, title):
    return next(r["key"] for r in json.loads(run("list", "--json").out) if r["title"] == title)


def write_item(lib, item_id, title, abstract, authors=("Zed Zhang",), year=2026, fetched=None, source="feed:test",
               commit=True):
    p = model.Paper(title=title, authors=list(authors), year=year, abstract=abstract, source=source)
    p.ids["doi"] = f"10.9999/{item_id}"
    item = inbox.Item(id=item_id, paper=p, fetched=fetched or datetime(2026, 10, 20, 8, 0, 0),
                      published=date(2026, 10, 19))
    path = inbox.Inbox(lib).path_for(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(inbox.dumps(item))
    if commit:  # as `ruru fetch` does: an inbox file that git knows can be committed away by `keep`/`skip`
        gitops.commit(lib, [path], f"ruru: fetch {item_id}")
    return item_id


@pytest.fixture
def lib6(lib, run):
    for title, abstract, authors, year in CHROMATOGRAPHY:
        add_paper(run, title, abstract, authors, year)
    return lib


@pytest.fixture
def mixed(lib6):
    """Inbox: an on-topic item fetched first, an unrelated one fetched last."""
    write_item(lib6, "near", "Aligning gas chromatography peaks by retention time",
               "A warping method for retention time alignment of chromatography peaks.",
               fetched=datetime(2026, 10, 18, 8, 0, 0))
    write_item(lib6, "far", "Quantum dots for photovoltaic cells", "Efficiency of quantum dot solar cells.",
               fetched=datetime(2026, 10, 21, 8, 0, 0))
    return lib6


def ids(r):
    return [row["id"] for row in json.loads(r.out)]


def test_the_inbox_is_ranked_by_relevance_by_default(mixed, run):
    rows = json.loads(run("inbox", "--json").out)
    assert [r["id"] for r in rows] == ["near", "far"]  # although "far" was fetched later
    assert rows[0]["relevance"] > 0.2 and rows[1]["relevance"] == 0.0
    assert rows[0]["similar_to"][0]["similarity"] > 0.2 and rows[0]["similar_to"][0]["key"]
    assert rows[1]["similar_to"] == [] and rows[0]["possible_duplicate_of"] is None
    assert run("inbox", "-q").out.split() == ["near", "far"]


def test_date_order_is_an_option_and_a_setting(mixed, run):
    assert ids(run("inbox", "--sort", "date", "--json")) == ["far", "near"]
    toml = (mixed / "ruru.toml").read_text()
    (mixed / "ruru.toml").write_text(toml.replace('inbox_sort = "relevance"', 'inbox_sort = "date"'))
    assert ids(run("inbox", "--json")) == ["far", "near"]
    assert ids(run("inbox", "--sort", "relevance", "--json")) == ["near", "far"]  # the flag wins
    (mixed / "ruru.toml").write_text(toml.replace('inbox_sort = "relevance"', 'inbox_sort = "random"'))
    r = run("inbox")
    assert r.code == cli.USAGE and "inbox_sort must be" in r.err


def test_lines_show_the_score_and_the_nearest_paper(mixed, run):
    lines = run("inbox").out.splitlines()
    assert lines[0].split()[0].replace(".", "").isdigit() and "near" in lines[0] and "~ " in lines[0]
    assert "far" in lines[1] and "~" not in lines[1]


def test_a_small_library_falls_back_to_the_date(lib, run):
    add_paper(run, "Only paper", "about chromatography", ["A B"], 2024)
    write_item(lib, "old", "Chromatography peaks", "peaks", fetched=datetime(2026, 10, 18, 8, 0, 0))
    write_item(lib, "new", "Quantum dots", "solar", fetched=datetime(2026, 10, 21, 8, 0, 0))
    r = run("inbox", "--json")
    assert ids(r) == ["new", "old"] and "ranking needs" in r.err
    assert [row["relevance"] for row in json.loads(r.out)] == [None, None]
    assert "ranking needs" not in run("inbox", "-q").err
    assert "ranking needs" not in run("inbox", "--sort", "date").err


def test_skipping_pushes_similar_items_down(lib6, run):
    write_item(lib6, "prot1", "Peak alignment in proteomics workflows", "proteomics peak alignment",
               fetched=datetime(2026, 10, 20, 8, 0, 0))
    write_item(lib6, "prot2", "Proteomics peak alignment benchmark", "benchmark of proteomics peak alignment",
               fetched=datetime(2026, 10, 19, 8, 0, 0))
    write_item(lib6, "gc", "Peak alignment in gas chromatography", "gas chromatography peak alignment",
               fetched=datetime(2026, 10, 18, 8, 0, 0))
    before = {r["id"]: r["relevance"] for r in json.loads(run("inbox", "--json").out)}
    assert run("skip", "prot1", "--reason", "proteomics").code == 0
    after = {r["id"]: r["relevance"] for r in json.loads(run("inbox", "--json").out)}
    assert after["prot2"] < before["prot2"] and after["gc"] <= before["gc"]
    lost = {k: (before[k] - after[k]) / before[k] for k in ("prot2", "gc")}  # the share of its score each lost
    assert lost["prot2"] > 3 * lost["gc"] and after["gc"] > after["prot2"]


def test_show_an_inbox_item_with_its_relevance(mixed, run):
    out = run("show", "near").out
    assert "relevance: 0." in out and "like:" in out
    row = json.loads(run("show", "near", "--json").out)
    assert row["relevance"] > 0.2 and row["similar_to"] and row["possible_duplicate_of"] is None


def test_export_carries_the_ranking(mixed, run):
    doc = json.loads(run("export", "json").out)
    assert [i["id"] for i in doc["inbox"]] == ["far", "near"]  # the export is by date, as documented
    assert {i["id"]: i["relevance"] is not None for i in doc["inbox"]} == {"far": True, "near": True}


def test_related_finds_the_closest_papers(lib6, run, mixed):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    rows = json.loads(run("related", key, "-n", "3", "--json").out)
    assert len(rows) == 3 and all(r["kind"] == "library" and r["status"] == "to-read" for r in rows)
    assert [r["similarity"] for r in rows] == sorted((r["similarity"] for r in rows), reverse=True)
    assert key not in [r["id"] for r in rows]
    both = json.loads(run("related", key, "--in", "all", "--json").out)
    assert {"library", "inbox"} >= {r["kind"] for r in both} and any(r["id"] == "near" for r in both)
    only = json.loads(run("related", key, "--in", "inbox", "--json").out)
    assert only[0]["id"] == "near" and all(r["kind"] == "inbox" for r in only)
    assert run("related", key, "-q", "-n", "2").out.split() == [r["id"] for r in rows[:2]]
    first_line = run("related", key).out.splitlines()[0].split()
    assert first_line[0].startswith("0.") and first_line[1] == rows[0]["id"]  # score, key, year, title


def test_related_works_from_an_inbox_item(mixed, run):
    rows = json.loads(run("related", "near", "--json").out)
    assert rows and all(r["kind"] == "library" for r in rows)
    assert run("related", "nosuchkey").code == cli.USAGE


def test_related_with_nothing_to_compare(lib, run):
    key = add_paper(run, "Alone", "just one paper", ["A B"], 2024)
    r = run("related", key)
    assert r.code == cli.NO_MATCH and f"nothing resembles {key}" in r.err
    assert run("related", key, "--in", "inbox").code == cli.NO_MATCH


def test_dupes_flag_a_preprint_and_its_published_version(lib6, run):
    first = CHROMATOGRAPHY[0]
    write_item(lib6, "published", "Robust retention time alignment of gas chromatography mass spectrometry data",
               first[1], authors=first[2], year=2024)
    write_item(lib6, "unrelated", "Quantum dots", "solar cells", authors=first[2], year=2024)
    key = key_of(run, first[0])
    pairs = json.loads(run("dupes", "--json").out)
    assert len(pairs) == 1 and pairs[0]["a"] == key and pairs[0]["b"] == "published"
    assert 0.5 <= pairs[0]["similarity"] < 0.7 and pairs[0]["same"] is False  # same two authors: a lower bar
    assert pairs[0]["a_title"] == first[0] and pairs[0]["b_title"].startswith("Robust")
    assert run("dupes", "-q").out.split() == [key, "published"]
    assert "near" in run("dupes").out
    assert run("dupes", "--library-only").code == cli.NO_MATCH
    row = {r["id"]: r for r in json.loads(run("inbox", "--json").out)}
    assert row["published"]["possible_duplicate_of"] == key
    assert row["unrelated"]["possible_duplicate_of"] is None
    assert f"duplicate of {key}" in run("inbox").out
    assert "possible duplicate of" in run("show", "published").out


def test_dupes_need_shared_authors_and_close_years(lib6, run):
    first = CHROMATOGRAPHY[0]
    write_item(lib6, "strangers", first[0], first[1], authors=["Nobody Else"], year=2023)
    write_item(lib6, "decades", first[0], first[1], authors=first[2], year=2010)
    assert run("dupes").code == cli.NO_MATCH


def test_dupes_report_the_same_work_twice_in_the_inbox(lib6, run):
    for item_id in ("a-preprint", "b-journal"):
        write_item(lib6, item_id, "Fast alignment of mass spectra", "aligning spectra quickly",
                   authors=["Q R", "S T"])
    pair = json.loads(run("dupes", "--json").out)[0]
    assert (pair["a"], pair["b"], pair["same"]) == ("a-preprint", "b-journal", True)
    rows = {r["id"]: r for r in json.loads(run("inbox", "--json").out)}
    assert rows["b-journal"]["possible_duplicate_of"] == "a-preprint"
    assert rows["a-preprint"]["possible_duplicate_of"] is None


def test_index_commands(mixed, run):
    r = run("index")
    assert "0 papers" in r.out and not (mixed / ".ruru" / "index.sqlite").stat().st_size > 1_000_000
    assert "indexed 6 papers and 2 inbox items" in run("index", "update").out
    st = json.loads(run("index", "status", "--json").out)
    assert st["papers"] == 6 and st["inbox"] == 2 and st["exists"] and st["pdfs"] == 0
    assert run("index", "-q").out.strip().endswith("index.sqlite")
    assert "indexed 6 papers" in run("index", "rebuild").out
    assert json.loads(run("index", "update", "--json").out) == {"papers": 6, "inbox": 2, "pdfs": None}
    assert "indexed" not in run("index", "update", "-q").out


def test_index_update_reads_pdfs(lib6, run, monkeypatch):
    keys = [r["key"] for r in json.loads(run("list", "--json").out)]
    for k in keys[:2]:
        (lib6 / "papers" / k / "paper.pdf").write_bytes(b"%PDF-1.4 " + k.encode())
    monkeypatch.setattr(pdftext, "extract", lambda path: f"text of {path.parent.name}")
    r = run("index", "update", "--pdfs")
    assert "read 2 PDF(s), 0 already current" in r.out and r.err.count("reading the PDF of") == 2
    assert "read 0 PDF(s), 2 already current" in run("index", "update", "--pdfs").out
    data = json.loads(run("index", "update", "--pdfs", "--json").out)
    assert data["pdfs"] == {"extracted": [], "failed": {}, "current": 2}

    def boom(path):
        raise ValueError("encrypted")
    monkeypatch.setattr(pdftext, "extract", boom)
    (lib6 / "papers" / keys[2] / "paper.pdf").write_bytes(b"%PDF-1.4 third")
    r = run("index", "update", "--pdfs")
    assert r.code == cli.PARTIAL and "encrypted" in r.err
    assert run("index", "update", "--pdfs").code == cli.OK  # the failure is remembered, not retried
    assert run("index", "update", "--pdfs", "--retry").code == cli.PARTIAL


def test_doctor_reports_the_derived_data(mixed, run):
    out = run("doctor", "--offline").out
    assert "index" in out and "not built yet" in out and "near-duplicates" in out
    run("index", "update")
    assert "6 papers and 2 inbox items counted" in run("doctor", "--offline").out
    title, abstract, authors, year = CHROMATOGRAPHY[0]
    write_item(mixed, "twin", title, abstract, authors=authors, year=year)
    assert "1 possible: `ruru dupes`" in run("doctor", "--offline").out
    assert isinstance(Library.open(mixed).index.status().papers, int)
