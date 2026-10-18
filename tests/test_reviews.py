import json

import pytest

from conftest import git_log
from ruru import cli, reviews
from ruru.library import Library

PDF = b"%PDF-1.4\n% test\n"

DRAFT = """---
title: Robust estimation for retention-time alignment
papers: [huber1964robust, wood2026joint]
tags: [Robust-Estimation]
---

## Why this matters to you

Both bear on the outlier handling in joint-align [@wood2026joint]. Huber's loss [@huber1964robust]
is the classical answer; they differ on breakdown point [@huber1964robust; @wood2026joint].

| paper | idea |
|---|---|
| [@huber1964robust] | M-estimation |

<script>alert("x")</script>
"""


@pytest.fixture
def lib(lib, run, tmp_path):
    run("add", "--title", "Robust Estimation of a Location Parameter", "--author", "Peter J. Huber",
        "--year", "1964", "--doi", "10.1214/aoms/1177703732", "--abstract", "A classic.")
    run("add", "--title", "Joint retention-time alignment", "--author", "Jesse Wood", "--year", "2026",
        "-t", "gc-ms")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    for key in ("huber1964robust", "wood2026joint"):
        (downloads / f"{key}.pdf").write_bytes(PDF)
    assert run("pdf", "add", str(downloads)).code == 0
    return lib


def draft(tmp_path, text=DRAFT):
    path = tmp_path / "draft.md"
    path.write_text(text)
    return str(path)


def test_cited_and_round_trip():
    assert reviews.cited("a [@x] b [@y; @x] c [not] [@z]") == ["x", "y", "z"]
    r = reviews.loads(DRAFT)
    assert r.papers == ["huber1964robust", "wood2026joint"] and r.body.startswith("## Why")
    r.id, r.created = "x", None
    assert reviews.loads(reviews.dumps(r)) == r
    with pytest.raises(ValueError):
        reviews.loads("no front matter")
    with pytest.raises(ValueError):
        reviews.loads("---\npapers: x\n---\n")


def test_problems():
    r = reviews.loads("---\nid: Bad Id\nstatus: maybe\npapers: [a, b]\n---\n[@a] [@c]\n")
    found = " | ".join(reviews.problems(r))
    for expected in ("needs a title", "id 'Bad Id'", "status must be", "cites c", "lists b"):
        assert expected in found
    assert "needs `papers:`" in " | ".join(reviews.problems(reviews.loads("---\ntitle: T\nid: t\n---\n")))


def test_add_list_show_keep_discard(lib, run, tmp_path):
    r = run("review", "add", draft(tmp_path), "--by", "claude")
    assert r.code == 0, r.err
    rid = r.out.split()[2]
    assert rid.endswith("-robust-estimation-for-retention-time-alignment")
    assert git_log(lib)[0] == f"ruru: review {rid} (claude)"
    rows = json.loads(run("review", "--json").out)
    assert [(x["id"], x["status"], x["by"], x["tags"]) for x in rows] == [
        (rid, "new", "claude", ["robust-estimation"])]
    assert "●" in run("review").out and run("review", "list", "-s", "kept").code == cli.NO_MATCH
    shown = json.loads(run("review", "show", rid, "--json").out)
    assert shown["papers"] == ["huber1964robust", "wood2026joint"] and "## Why" in shown["body"]
    assert run("review", "show", rid).out.startswith("# Robust estimation")
    assert "already a review" in run("review", "add", draft(tmp_path)).err
    assert run("review", "add", draft(tmp_path), "--replace", "-q").out.strip() == rid
    assert run("review", "keep", rid).code == 0
    assert json.loads(run("review", "show", rid, "--json").out)["status"] == "kept"
    assert "kept: give a new id" in run("review", "add", draft(tmp_path), "--replace").err
    assert run("review", "discard", rid, "nope").code == cli.USAGE  # nothing discarded
    assert run("review", "discard", rid).code == 0 and run("review").code == cli.NO_MATCH
    assert git_log(lib)[0] == f"ruru: review discard {rid}"
    assert run("review", "show", rid).code == cli.USAGE


def test_reviews_only_cover_the_reading_list_with_pdfs(lib, run, tmp_path):
    run("status", "huber1964robust", "read")
    r = run("review", "add", draft(tmp_path))
    assert r.code == cli.USAGE and "huber1964robust (read) not on the reading list" in r.err
    run("status", "huber1964robust", "reading")
    (lib / "papers" / "wood2026joint" / "paper.pdf").unlink()
    r = run("review", "add", draft(tmp_path))
    assert r.code == cli.USAGE and "no PDF for wood2026joint" in r.err and "ruru pdf missing" in r.err
    r = run("review", "add", draft(tmp_path, DRAFT.replace("wood2026joint", "ghost2020")))
    assert "no paper with key ghost2020" in r.err
    assert run("review").code == cli.NO_MATCH


def test_open_renders_html(lib, run, tmp_path, browsed):
    rid = run("review", "add", draft(tmp_path), "-q").out.strip()
    run("note", "huber1964robust", "--agent", "-m", "ignored: agent notes are not shown")
    r = run("review", "open", rid)
    path = lib / ".ruru" / "reviews" / f"{rid}.html"
    assert r.out.strip() == str(path) and browsed == [path.as_uri()]
    html = path.read_text()
    assert "<title>Robust estimation for retention-time alignment</title>" in html
    assert '<a href="#p-huber1964robust" title="Robust Estimation of a Location Parameter">Huber 1964</a>' in html
    assert 'id="p-wood2026joint"' in html and "https://doi.org/10.1214/aoms/1177703732" in html
    assert "<script>" not in html and "&lt;script&gt;" in html  # raw HTML is shown, never run
    assert '<div class="table-wrap"><table>' in html and "A classic." in html and "#gc-ms" in html
    assert run("review", "open", rid, "--no-open").code == 0 and len(browsed) == 1
    run("rm", "wood2026joint")
    assert run("review", "open", rid, "--no-open").code == 0
    assert "is no longer in the library" in path.read_text()


def test_pdf_add_and_missing(lib, run, tmp_path, browsed, clipboard, monkeypatch):
    run("add", "--title", "A third paper", "--author", "Ann Other", "--year", "2020", "--doi", "10.1/third")
    run("add", "--title", "No link anywhere", "--year", "2019")
    r = run("pdf", "missing")
    assert r.code == 0 and "other2020third" in r.out and "https://doi.org/10.1/third" in r.out
    assert json.loads(run("pdf", "missing", "--json").out)[0] == {
        "key": "anon2019no", "title": "No link anywhere", "link": None, "save_as": "anon2019no.pdf"}
    assert run("pdf", "missing", "-t", "gc-ms").code == cli.NO_MATCH  # wood2026joint has its PDF
    run("pdf", "missing", "--open")
    assert browsed == ["https://doi.org/10.1/third"]
    assert run("pdf", "other2020third", "--browser").out.strip() == "https://doi.org/10.1/third"
    assert clipboard == ["other2020third.pdf"]
    assert run("pdf", "anon2019no", "--browser").code == cli.NO_MATCH

    downloads = tmp_path / "saved"
    downloads.mkdir()
    monkeypatch.setenv("RURU_DOWNLOADS", str(downloads))
    assert run("pdf", "add").code == cli.NO_MATCH  # nothing named after a key
    (downloads / "Other2020Third.pdf").write_bytes(PDF)  # case aside
    (downloads / "holiday.pdf").write_bytes(PDF)  # not a key: ignored when scanning a folder
    (downloads / "anon2019no.pdf").write_bytes(b"<html>sign in</html>")
    r = run("pdf", "add")
    assert r.code == cli.PARTIAL and "other2020third:" in r.out and "not a PDF" in r.err
    assert Library.open(lib).has_pdf("other2020third") and (downloads / "holiday.pdf").exists()
    (downloads / "x.pdf").write_bytes(PDF)
    assert "already has a PDF" in run("pdf", "add", str(downloads / "x.pdf"), "--key", "other2020third").err
    assert run("pdf", "add", str(downloads / "x.pdf"), "--key", "other2020third", "--force", "-q").out == \
        "other2020third\n"
    assert "no paper with key 'holiday'" in run("pdf", "add", str(downloads / "holiday.pdf")).err
    assert run("pdf", "add", str(downloads / "gone.pdf")).code == cli.USAGE
    assert run("pdf", "add", str(downloads), "--key", "other2020third").code == cli.USAGE
    assert run("pdf", "add", "x.pdf", "--key", "nosuch").code == cli.USAGE
    assert run("pdf", "show", "other2020third").out.strip().endswith("other2020third/paper.pdf")
    assert run("pdf", "missing", "--all", "-q").out == "anon2019no\n"
    run("status", "anon2019no", "read")
    assert run("pdf", "missing").code == cli.NO_MATCH
    assert run("pdf", "-h").code == 0


def test_math_renders_as_mathml(lib, run, tmp_path):
    text = DRAFT.replace("Huber's loss [@huber1964robust]",
                         "Huber's loss $\\rho(r)=r^2/2$ for $|r|<k$ [@huber1964robust], about $5 a run")
    text += "\n$$\n\\min_f \\sum_j w_j r_j^2 + \\lambda \\|D_2 f\\|^2\n$$\n\nBroken: $\\frac{1}{$.\n"
    rid = run("review", "add", draft(tmp_path, text), "-q").out.strip()
    html = (lib / ".ruru" / "reviews" / f"{rid}.html")
    run("review", "open", rid, "--no-open")
    page = html.read_text()
    assert page.count('<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline">') == 2
    assert '<div class="math block">\n<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">' in page
    assert "about $5 a run" in page  # a price is not math
    assert '<code class="math-error" title="TeX not rendered">\\frac{1}{</code>' in page


def test_slug_cuts_at_a_word_boundary():
    from datetime import date
    on = date(2026, 10, 18)
    long = reviews.slug("What counts as an independent sample - validation in food authentication", on)
    assert long == "2026-10-18-what-counts-as-an-independent-sample-validation-in-food" and len(long) <= 80
    assert reviews.slug("Árvore & café", on) == "2026-10-18-arvore-cafe"
    exact = reviews.slug("a" * 69, on)  # 11 + 69 = 80 characters: kept whole
    assert exact == "2026-10-18-" + "a" * 69
