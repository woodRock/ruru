"""Text from PDFs: extraction, finding quotes, `pdf text`, `search --fulltext`, quote-checked verdicts."""

import json
import subprocess

import pytest
from pdffix import make_pdf

from ruru import cli, pdftext
from ruru.errors import UsageError

PAGES = ["Huber weights downweight outliers in robust regression.",
         "Correlation optimised warping (COW) aligns chromatograms; retention drift is removed."]


def test_normalise_undoes_typesetting():
    assert pdftext.normalise("Corre-\nlation  \u201coptimised\u201d \ufb01ts\nthe  data") == \
        'correlation "optimised" fits the data'
    assert pdftext.normalise("Naïve – baseline") == "naive - baseline"


def test_find_quote_gives_the_page():
    text = pdftext.PAGE_BREAK.join(["Page one text about alignment.",
                                    "Huber weights downweight\noutliers in robust\nregression."])
    assert pdftext.find_quote(text, "huber weights downweight outliers") == 2
    assert pdftext.find_quote(text, "text about alignment") == 1
    assert pdftext.find_quote(text, "“Huber weights downweight outliers in robust regression.”") == 2
    assert pdftext.find_quote(text, "alignment. Huber weights") == 1  # across a page break: its first page
    assert pdftext.find_quote(text, "not in this paper") is None
    assert pdftext.find_quote(text, "…") is None and pdftext.find_quote("", "x") is None
    assert pdftext.pages(text) == text.split("\f")


def test_extract_with_pypdf(tmp_path):
    pytest.importorskip("pypdf")
    path = tmp_path / "p.pdf"
    path.write_bytes(make_pdf(PAGES))
    text = pdftext.extract(path)
    pages = pdftext.pages(text)
    assert len(pages) == 2 and "Huber weights downweight outliers" in pages[0] and "warping" in pages[1]
    assert pdftext.find_quote(text, "retention drift is removed") == 2
    assert pdftext.have_extractor()


def test_a_broken_pdf_names_the_file(tmp_path):
    pytest.importorskip("pypdf")
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.4 not really")
    with pytest.raises(UsageError, match=r"bad\.pdf"):
        pdftext.extract(path)


def test_poppler_is_the_fallback(tmp_path, monkeypatch):
    path = tmp_path / "p.pdf"
    path.write_bytes(b"%PDF")
    monkeypatch.setitem(__import__("sys").modules, "pypdf", None)  # import pypdf fails
    monkeypatch.setattr(pdftext.shutil, "which", lambda name: "/usr/bin/pdftotext")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="from poppler\f", stderr="")
    monkeypatch.setattr(pdftext.subprocess, "run", fake_run)
    assert pdftext.extract(path) == "from poppler\f" and calls[0][0] == "/usr/bin/pdftotext"
    assert pdftext.have_extractor()
    monkeypatch.setattr(pdftext.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "bad"))
    with pytest.raises(UsageError, match="pdftotext failed: bad"):
        pdftext.extract(path)


def test_without_any_extractor(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "pypdf", None)
    monkeypatch.setattr(pdftext.shutil, "which", lambda name: None)
    assert not pdftext.have_extractor()
    with pytest.raises(pdftext.PdfTextUnavailable, match=r"ruru\[pdf\]"):
        pdftext.extract(tmp_path / "x.pdf")


# --- through the CLI -----------------------------------------------------------------------------

@pytest.fixture
def two_pdfs(lib, run):
    keys = {}
    papers = (("Robust regression with Huber weights", PAGES[0]), ("Chromatogram alignment methods", PAGES[1]))
    for name, text in papers:
        key = run("add", "--title", name, "--year", "2024", "--author", "A Author", "--no-enrich",
                  "-q").out.strip()
        (lib / "papers" / key / "paper.pdf").write_bytes(make_pdf([text, "Second page: " + name]))
        keys[name.split()[0]] = key
    return keys


def test_pdf_text_command(two_pdfs, run):
    pytest.importorskip("pypdf")
    key = two_pdfs["Robust"]
    r = run("pdf", "text", key)
    assert r.code == 0 and "Huber weights downweight outliers" in r.out and "Second page" in r.out
    assert "reading the PDF of" in r.err and "reading the PDF of" not in run("pdf", "text", key).err  # cached
    second = run("pdf", "text", key, "--page", "2").out.strip()
    assert second == "Second page: Robust regression with Huber weights"
    data = json.loads(run("pdf", "text", key, "--page", "1", "--json").out)
    assert data["pages"] == 2 and data["page"] == 1 and data["key"] == key and data["text"].startswith("Huber")
    assert run("pdf", "text", key, "--page", "3").code == cli.USAGE
    assert run("pdf", "text", "nosuchkey").code == cli.USAGE


def test_pdf_text_without_a_pdf_or_text(lib, run, monkeypatch):
    key = run("add", "--title", "No file", "--year", "2024", "--author", "A B", "--no-enrich", "-q").out.strip()
    assert run("pdf", "text", key).code == cli.NO_MATCH
    (lib / "papers" / key / "paper.pdf").write_bytes(b"%PDF-1.4 scan")
    monkeypatch.setattr(pdftext, "extract", lambda path: "\f\f")
    r = run("pdf", "text", key)
    assert r.code == cli.NO_MATCH and "no text layer" in r.err


def test_search_fulltext(two_pdfs, run):
    pytest.importorskip("pypdf")
    assert run("search", "warping").code == cli.NO_MATCH  # not in the metadata
    r = run("search", "--fulltext", "warping", "--json")
    rows = json.loads(r.out)
    assert [x["key"] for x in rows] == [two_pdfs["Chromatogram"]] and rows[0]["in_pdf"] is True
    assert "warping" in rows[0]["snippet"].lower() and rows[0]["score"] == 1
    plain = run("search", "--fulltext", "warping")
    assert "pdf: …" in plain.out and two_pdfs["Chromatogram"] in plain.out
    both = json.loads(run("search", "--fulltext", "robust", "huber", "--json").out)
    assert both[0]["key"] == two_pdfs["Robust"] and both[0]["score"] > 1  # in the title and in the PDF
    meta = json.loads(run("search", "robust", "--json").out)
    assert meta[0]["in_pdf"] is False and meta[0]["snippet"] is None
    assert run("search", "--fulltext", "nonexistentword").code == cli.NO_MATCH
    assert run("search", "--fulltext", "warping", "-q").out.strip() == two_pdfs["Chromatogram"]


def test_search_fulltext_needs_an_extractor_only_for_new_pdfs(two_pdfs, run, monkeypatch):
    monkeypatch.setattr(pdftext, "have_extractor", lambda: False)
    r = run("search", "--fulltext", "warping")
    assert r.code == cli.USAGE and "ruru[pdf]" in r.err
    monkeypatch.setattr(pdftext, "extract", lambda path: "warping text")
    monkeypatch.setattr(pdftext, "have_extractor", lambda: True)
    assert run("search", "--fulltext", "warping", "-q").code == 0
    monkeypatch.setattr(pdftext, "have_extractor", lambda: False)
    assert run("search", "--fulltext", "warping", "-q").code == 0  # everything is already read


def test_a_pdf_that_cannot_be_read_is_reported(two_pdfs, run, monkeypatch):
    def boom(path):
        raise UsageError("encrypted")
    monkeypatch.setattr(pdftext, "extract", boom)
    r = run("search", "--fulltext", "warping")
    assert r.code == cli.NO_MATCH and r.err.count("no text from the PDF: encrypted") == 2
    assert run("pdf", "text", two_pdfs["Robust"]).code == cli.USAGE


# --- verdict quotes ------------------------------------------------------------------------------

@pytest.fixture
def cited(lib, run, tmp_path):
    key = run("add", "--title", "Huber paper", "--year", "1964", "--author", "Peter Huber", "--no-enrich",
              "-q").out.strip()
    pages = ["Huber weights downweight outliers in robust regression.", "Thresholds of 1.345 sigma are used."]
    (lib / "papers" / key / "paper.pdf").write_bytes(make_pdf(pages))
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "main.tex").write_text(
        f"\\begin{{document}}\nWe use Huber weights \\cite{{{key}}}.\n\\end{{document}}\n")
    added = run("project", "add", "demo", "--path", str(draft), "--bib", "refs.bib", "--tex", "main.tex")
    assert added.code == 0
    claim = json.loads(run("claims", "demo", "--json").out)["claims"][0]["id"]
    return key, claim


def test_a_quote_must_be_in_the_pdf(cited, run):
    pytest.importorskip("pypdf")
    key, claim = cited
    bad = run("verdict", "demo", claim, key, "supported", "--quote", "Huber weights eliminate all noise")
    assert bad.code == cli.USAGE and "not in its PDF" in bad.err
    assert run("claims", "demo", "--summary", "--json").code == 0
    ok = run("verdict", "demo", claim, key, "supported", "--quote", "Huber weights downweight\noutliers in robust")
    assert ok.code == 0, ok.err
    skipped = run("verdict", "demo", claim, key, "supported", "--quote", "invented words", "--no-verify")
    assert skipped.code == 0


def test_the_quote_check_can_be_switched_off_per_record(cited, run, tmp_path):
    pytest.importorskip("pypdf")
    key, claim = cited
    records = tmp_path / "v.jsonl"
    records.write_text(json.dumps({"claim": claim, "key": key, "verdict": "supported", "quote": "not there",
                                   "verify": False}) + "\n")
    assert run("verdict", "demo", "-F", str(records)).code == 0
    records.write_text(json.dumps({"claim": claim, "key": key, "verdict": "supported", "quote": "not there"}))
    assert run("verdict", "demo", "-F", str(records)).code == cli.USAGE


def test_an_unreadable_pdf_warns_instead_of_blocking(cited, run, monkeypatch):
    key, claim = cited
    monkeypatch.setattr(pdftext, "extract", lambda path: "\f")
    r = run("verdict", "demo", claim, key, "supported", "--quote", "anything at all")
    assert r.code == 0 and "quote not checked against the PDF" in r.err and "no text layer" in r.err
