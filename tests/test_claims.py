import json

import pytest

from conftest import git_log
from ruru import cli, ops
from ruru.claims import claim_id, extract, load
from ruru.errors import NothingMatched

TEX = r"""\documentclass{article}
\usepackage[citecolor=blue]{hyperref}  % "cite" in the preamble is not a claim
\begin{document}
\section{Introduction}\label{sec:intro}
Fish can be told apart by their fatty acids \cite{wood2026joint}. Smith et al.\ aligned
batches pairwise \citep[p.~3]{xue2025fitness}, e.g. with COW, as in Fig.~\ref{fig:a}.
% A commented claim \cite{zhang2024kept}.

\paragraph*{Statistics} We used the test of J. Wilcoxon \cite{xue2025fitness, smith2020missing}.
\begin{itemize}
\item One method \cite{wood2026joint}
\item Another, uncited
\end{itemize}
Nothing here. \nocite{zhang2024kept}
\input{sections/intro}
\end{document}
"""


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "paper-repo"
    (root / "sections").mkdir(parents=True)
    (root / "main.tex").write_text(TEX)
    (root / "sections" / "intro.tex").write_text("As shown before \\citet{wood2026joint}.\n")
    (root / "references.bib").write_text("")
    return root


@pytest.fixture
def proj(lib, run, repo):
    for title, author, year in [("Joint Retention-Time Alignment", "Jesse Wood", "2026"),
                                ("Fitness landscapes", "Bing Xue", "2025"),
                                ("Kept but never cited", "Mengjie Zhang", "2024")]:
        assert run("add", "--title", title, "--author", author, "--year", year, "-q").code == 0
    assert run("project", "add", "joint-align", "--path", str(repo), "--bib", "references.bib",
               "--tex", "main.tex").code == 0
    return lib


def _pdf(lib, key):
    (lib / "papers" / key / "paper.pdf").write_bytes(b"%PDF-1.4\n")


def _claims(run):
    return json.loads(run("claims", "joint-align", "--json").out)["claims"]


def test_extract_sentences(repo):
    claims = extract(repo / "main.tex")
    assert [(c.line, c.keys, c.text) for c in claims] == [
        (5, ["wood2026joint"], "Fish can be told apart by their fatty acids [@wood2026joint]."),
        (6, ["xue2025fitness"], r"Smith et al.\ aligned batches pairwise [@xue2025fitness], e.g. with COW, "
                                r"as in Fig.~\ref{fig:a}."),
        (9, ["xue2025fitness", "smith2020missing"],
         "We used the test of J. Wilcoxon [@xue2025fitness; @smith2020missing]."),
        (11, ["wood2026joint"], "One method [@wood2026joint]"),
    ]
    assert claims[0].id == claim_id(claims[0].text) and len(claims[0].id) == 10
    (repo / "main.tex").write_text(TEX.replace("by their fatty", "by  their\nfatty"))  # whitespace only
    assert extract(repo / "main.tex")[0].id == claims[0].id
    (repo / "main.tex").write_text(TEX.replace("by their fatty acids", "by their lipids"))
    assert extract(repo / "main.tex")[0].id != claims[0].id
    (repo / "plain.tex").write_text("No document environment \\cite{a}.\n")
    assert extract(repo / "plain.tex")[0].keys == ["a"]


def test_claims_command(proj, run, repo):
    _pdf(proj, "wood2026joint")
    rows = _claims(run)
    assert [(r["file"], r["line"]) for r in rows] == [("main.tex", 5), ("main.tex", 6), ("main.tex", 9),
                                                      ("main.tex", 11), ("sections/intro.tex", 1)]
    assert rows[2]["checks"] == [
        {"key": "xue2025fitness", "in_library": True, "has_pdf": False, "verdict": None},
        {"key": "smith2020missing", "in_library": False, "has_pdf": False, "verdict": None}]
    assert rows[0]["checks"][0]["has_pdf"]
    r = run("claims", "joint-align")
    assert r.code == 0 and "main.tex:9" in r.out and "smith2020missing (not in library)" in r.out
    assert "xue2025fitness (no PDF)" in r.out
    assert run("claims", "joint-align", "-q").out.split() == [row["id"] for row in rows]
    assert len(json.loads(run("claims", "joint-align", "-k", "smith2020missing", "--json").out)["claims"]) == 1
    assert run("claims", "joint-align", "--verdict", "supported").code == cli.NO_MATCH
    assert run("claims", "nope").code == cli.USAGE
    run("project", "add", "notex", "--path", str(repo), "--bib", "references.bib")
    assert "lists no tex files" in run("claims", "notex").err


def test_verdicts(proj, run, repo):
    _pdf(proj, "wood2026joint")
    first = _claims(run)[0]["id"]
    r = run("verdict", "joint-align", first, "wood2026joint", "supported", "-p", "3",
            "--quote", "fatty acid profiles separate species", "--by", "claude")
    assert r.code == 0 and r.out == f"{first} wood2026joint: supported\n"
    assert git_log(proj)[0] == "ruru: verdict joint-align wood2026joint:supported (claude)"
    v = _claims(run)[0]["checks"][0]["verdict"]
    assert v["verdict"] == "supported" and v["page"] == "3" and v["by"] == "claude" and v["source"] == "pdf"
    assert v["text"] == "Fish can be told apart by their fatty acids [@wood2026joint]."
    assert "✓ wood2026joint" in run("claims", "joint-align").out
    assert run("claims", "joint-align", "--unchecked", "-q").out.split()[0] != first

    # The rules: quotes, notes, PDFs, real claims and keys. Nothing is written on a refusal.
    second = _claims(run)[1]["id"]
    for argv, message in [
        ([first, "wood2026joint", "supported"], "needs --quote"),
        ([first, "wood2026joint", "unsupported"], "needs --note"),
        ([first, "wood2026joint", "maybe"], "verdict must be one of"),
        ([first, "xue2025fitness", "background"], "not xue2025fitness"),
        (["0000000000", "wood2026joint", "background"], "no claim '0000000000'"),
        ([second, "xue2025fitness", "not-found"], "has no PDF"),
        ([first], "usage: ruru verdict"),
    ]:
        r = run("verdict", "joint-align", *argv)
        assert r.code == cli.USAGE and message in r.err, (argv, r.err)
    assert run("verdict", "joint-align", second, "xue2025fitness", "partial", "--from", "abstract",
               "--quote", "pairwise", "--note", "says one pair, not batches").code == 0
    assert run("claims", "joint-align", "--verdict", "partial", "-q").out.split() == [second]

    # Many at once (JSON lines), one commit; then the sentence changes and the verdict goes stale.
    lines = "\n".join(json.dumps(d) for d in [
        {"claim": first, "key": "wood2026joint", "verdict": "background"},
        {"claim": _claims(run)[2]["id"], "key": "smith2020missing", "verdict": "background", "from": "other"}])
    (repo / "v.jsonl").write_text(lines)
    assert run("verdict", "joint-align", "-F", str(repo / "v.jsonl"), "-q").code == 0
    assert git_log(proj)[0] == "ruru: verdict joint-align wood2026joint:background smith2020missing:background"
    assert len(load(proj, "joint-align")) == 3
    assert run("verdict", "joint-align", first, "-F", "x").code == cli.USAGE
    (repo / "bad.json").write_text("{nope")
    assert "not JSON" in run("verdict", "joint-align", "-F", str(repo / "bad.json")).err
    (repo / "main.tex").write_text(TEX.replace("by their fatty acids", "by their lipids"))
    out = json.loads(run("claims", "joint-align", "--json").out)
    assert [s["claim"] for s in out["stale"]] == [first]
    assert "1 verdict(s) on sentences no longer in the tex" in run("claims", "joint-align").err

    # Dropping works on stale verdicts too.
    assert run("verdict", "joint-align", first, "--drop").out == f"dropped {first} wood2026joint (background)\n"
    assert run("verdict", "joint-align", first, "--drop").code == cli.USAGE
    assert run("verdict", "joint-align", first, "x", "supported", "--drop").code == cli.USAGE
    assert len(load(proj, "joint-align")) == 2


def test_pdf_missing_cited(proj, run, monkeypatch):
    _pdf(proj, "wood2026joint")
    r = run("pdf", "missing", "--cited", "joint-align", "-q")
    assert r.out.split() == ["xue2025fitness"] and "not in the library: smith2020missing" in r.err
    assert run("pdf", "missing", "wood2026joint", "--cited", "joint-align").code == cli.USAGE
    tried = []

    def fake_fetch(lib, client, key, force=False):
        tried.append(key)
        raise NothingMatched(f"{key}: no open-access PDF known")
    monkeypatch.setattr(ops, "fetch_pdf", fake_fetch)
    r = run("pdf", "missing", "--cited", "joint-align", "--fetch", "-q")
    assert tried == ["xue2025fitness"] and r.out.split() == ["xue2025fitness"] and "no open-access" in r.err

    def fetched(lib, client, key, force=False):
        _pdf(proj, key)
    monkeypatch.setattr(ops, "fetch_pdf", fetched)
    assert run("pdf", "missing", "--cited", "joint-align", "--fetch").code == cli.NO_MATCH
