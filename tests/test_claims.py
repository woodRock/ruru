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
        {"key": "xue2025fitness", "title": "Fitness landscapes", "in_library": True, "has_pdf": False,
         "own": False, "kind": "paper", "read": [], "version": None, "state": "unchecked", "verdict": None},
        {"key": "smith2020missing", "title": None, "in_library": False, "has_pdf": False, "own": False,
         "kind": None, "read": [], "version": None, "state": "unchecked", "verdict": None}]
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


def test_own_work(proj, run, repo):
    assert run("own").code == cli.NO_MATCH
    r = run("own", "add", "wood2026joint")
    assert r.code == 0 and r.out == "own: wood2026joint (tagged own-work)\n"
    assert json.loads(run("show", "wood2026joint", "--json").out)["tags"] == ["own-work"]
    assert "ruru cited add smith2020missing" in run("own", "add", "smith2020missing").err  # not a library paper
    # Own work is a label: it is checked like anything else and still needs its PDF.
    rows = _claims(run)
    assert rows[0]["checks"][0]["own"] and rows[0]["id"] in run("claims", "joint-align", "--unchecked", "-q").out
    assert "★" in run("claims", "joint-align").out
    assert run("pdf", "missing", "--cited", "joint-align", "-q").out.split() == ["wood2026joint", "xue2025fitness"]
    assert run("claims", "joint-align", "--verdict", "own", "-q").out.split() == [
        r["id"] for r in rows if "wood2026joint" in r["keys"]]
    assert run("own", "remove", "nope2020").code == cli.USAGE
    assert run("own", "remove", "wood2026joint", "-q").out == "wood2026joint\n"
    assert json.loads(run("show", "wood2026joint", "--json").out)["tags"] == []


def test_legacy_own_list_moves_to_cited(proj, run, repo):
    text = (proj / "ruru.toml").read_text().replace(
        "\n[projects.", '\n# Your own drafts and software outside the library: `ruru own add KEY`.\n'
        'own = ["smith2020missing", "zzz2020"]\n\n[projects.', 1)
    (proj / "ruru.toml").write_text(text)
    assert "ruru.toml `own` (old form)" in run("own").out
    assert json.loads(run("own", "--json").out)[0] == {"key": "smith2020missing", "in_library": False,
                                                      "kind": "unknown"}
    assert run("cited", "add", "smith2020missing", "--url", "https://example.org/tool").code == 0
    assert 'own = ["zzz2020"]' in (proj / "ruru.toml").read_text()
    assert run("own", "remove", "zzz2020").code == 0
    assert "own =" not in (proj / "ruru.toml").read_text() and "outside the library" not in \
        (proj / "ruru.toml").read_text()
    assert run("own", "remove", "smith2020missing").code == cli.USAGE  # it's cited now: `ruru cited remove`
    (proj / "ruru.toml").write_text('own = "x"\n' + (proj / "ruru.toml").read_text())
    assert "own must be a list" in run("own").err


def test_tomledit_top_level_keys():
    from ruru.tomledit import remove_key, set_list
    assert set_list("", "own", ["a"], "c") == '# c\nown = ["a"]\n'
    assert set_list("x = 1\n", "own", [], "c") == 'x = 1\n\n# c\nown = []\n'
    assert remove_key("x = 1\n\n# c\nown = []\n\n[t]\n", "own", "c") == "x = 1\n\n[t]\n"
    assert remove_key("x = 1\n", "own") == "x = 1\n"


def _git(path, *args):
    import subprocess
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


@pytest.fixture
def software(tmp_path):
    repo = tmp_path / "tool"
    repo.mkdir()
    (repo / "README.md").write_text("# tool\n\nAligns batches pairwise.\n")
    (repo / "CITATION.cff").write_text(
        'cff-version: 1.2.0\ntitle: "tool: Pairwise alignment for GC-MS"\nversion: 1.2.0\n'
        "date-released: 2026-10-17\nrepository-code: https://github.com/someone/tool\ndoi: 10.5281/zenodo.1\n"
        "authors:\n  - family-names: Wood\n    given-names: Jesse\n  - family-names: Ōtākou\n"
        "    given-names: Āpera\n  - name: The Tool Team\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "v1")
    return repo


def test_cited_draft_and_software(proj, run, repo, software, tmp_path):
    draft = tmp_path / "draft-repo"
    draft.mkdir()
    (draft / "main.tex").write_text("\\begin{document}\nWe use 103 samples.\n\\end{document}\n")
    run("project", "add", "draft", "--path", str(draft), "--bib", "refs.bib", "--tex", "main.tex")
    assert run("cited").code == cli.NO_MATCH
    assert run("cited", "add", "smith2020missing", "--draft", "draft").out == \
        "cited draft: smith2020missing (project draft)\n"
    assert run("cited", "add", "tool", "--software", str(software)).code == 0
    assert git_log(proj)[0] == "ruru: cited add tool (software)"
    for argv, message in [(["x", "--draft", "draft", "--software", "y"], "give --draft PROJECT, or"),
                          (["wood2026joint", "--draft", "draft"], "is in the library"),
                          (["tool", "--url", "u"], "already cited as software"),
                          (["new", "--draft", "nope"], "unknown project"),
                          (["bad key", "--url", "u"], "use letters")]:
        r = run("cited", "add", *argv)
        assert r.code == cli.USAGE and message in r.err, (argv, r.err)
    assert "does not exist (yet)" in run("cited", "add", "later", "--software", str(tmp_path / "nope")).err
    run("cited", "remove", "later")
    assert run("cited", "remove", "later").code == cli.USAGE

    listed = {e["key"]: e for e in json.loads(run("cited", "--json").out)}
    assert listed["smith2020missing"]["version"].startswith("tex:")
    assert listed["smith2020missing"]["read"] == [str(draft / "main.tex")]
    assert listed["tool"]["version"].startswith("git:")
    assert listed["tool"]["link"] == "https://github.com/someone/tool"
    assert listed["tool"]["read"] == [str(software / "README.md"), str(software / "CITATION.cff")]
    assert "software  " in run("cited", "list").out
    assert run("cited", "-q").out.split() == ["smith2020missing", "tool"]
    assert "cited draft" in run("own").out  # cited work is Jesse's own

    # A draft is checked against its .tex; the verdict records the version and goes outdated with it.
    (repo / "main.tex").write_text(repo.joinpath("main.tex").read_text() + "Pairwise works \\cite{tool}.\n")
    rows = _claims(run)
    draft_claim = rows[2]["id"]
    assert rows[2]["checks"][1]["kind"] == "draft" and rows[2]["checks"][1]["own"]
    r = run("verdict", "joint-align", draft_claim, "smith2020missing", "supported", "-p", "main.tex:2",
            "--quote", "We use 103 samples.")
    assert r.code == 0
    v = _claims(run)[2]["checks"][1]
    assert v["state"] == "supported" and v["verdict"]["source"] == "draft"
    assert v["verdict"]["version"] == listed["smith2020missing"]["version"]
    assert run("verdict", "joint-align", draft_claim, "smith2020missing", "supported", "--quote", "x",
               "--from", "pdf").code == cli.USAGE
    assert "for a cited software" in run("verdict", "joint-align", draft_claim, "smith2020missing", "background",
                                         "--from", "software").err
    assert "for a cited draft" in run("verdict", "joint-align", rows[0]["id"], "wood2026joint", "background",
                                      "--from", "draft").err
    (draft / "main.tex").write_text("\\begin{document}\nWe use 90 samples.\n\\end{document}\n")
    assert _claims(run)[2]["checks"][1]["state"] == "outdated"
    assert draft_claim in run("claims", "joint-align", "--unchecked", "-q").out.split()
    assert run("claims", "joint-align", "--verdict", "outdated", "-q").out.split() == [draft_claim]
    assert "↻ smith2020missing" in run("claims", "joint-align").out
    assert json.loads(run("claims", "joint-align", "--summary", "--json").out)["counts"]["outdated"] == 1

    # Software: checked against its README at a commit.
    def tool() -> dict:
        return next(r for r in _claims(run) if r["keys"] == ["tool"])["checks"][0]
    tool_claim = next(r for r in _claims(run) if r["keys"] == ["tool"])["id"]
    assert run("verdict", "joint-align", tool_claim, "tool", "supported",
               "--quote", "Aligns batches pairwise.").code == 0
    assert tool()["state"] == "supported" and tool()["verdict"]["source"] == "software"
    (software / "README.md").write_text("# tool\n\nAligns batches jointly.\n")
    assert tool()["version"].endswith("-dirty") and tool()["state"] == "outdated"

    # BibTeX for software, from CITATION.cff.
    bib = run("cited", "bib", "tool").out
    assert bib.startswith("@misc{tool,\n  author       = "
                          "{Wood, Jesse and {\\=O}t{\\=a}kou, {\\=A}pera and The Tool Team},")
    assert "title        = {{tool}: Pairwise alignment for GC-MS}" in bib
    assert ("howpublished = {Software, version 1.2.0. [Online]. "
            "Available: \\url{https://github.com/someone/tool}}") in bib
    assert "year         = {2026}" in bib and "doi          = {10.5281/zenodo.1}" in bib
    assert "Ōtākou" in run("cited", "bib", "tool", "--bib-encoding", "utf8").out
    assert "is a draft" in run("cited", "bib", "smith2020missing").err
    assert "not a cited draft or software" in run("cited", "bib", "nope").err
    (software / "CITATION.cff").write_text("title: [not: valid")
    assert "no CITATION.cff with a title" in run("cited", "bib", "tool").err
    assert "pdf missing" not in run("pdf", "missing", "--cited", "joint-align").err  # cited work has no PDF


def test_summary_and_states(proj, run, repo):
    _pdf(proj, "wood2026joint")
    rows = _claims(run)
    run("cited", "add", "smith2020missing", "--url", "https://example.org/smith")
    run("verdict", "joint-align", rows[1]["id"], "xue2025fitness", "unsupported", "--from", "abstract",
        "--note", "about landscapes, not alignment")
    run("verdict", "joint-align", rows[0]["id"], "wood2026joint", "supported", "--quote", "fatty acids")
    out = json.loads(run("claims", "joint-align", "--summary", "--json").out)
    assert out["claims"] == 5 and out["keys"] == 3 and out["stale"] == 0
    assert out["counts"] == {"supported": 1, "partial": 0, "unsupported": 1, "not-found": 0, "background": 0,
                             "unchecked": 4, "outdated": 0, "own": 1}
    assert [(p["claim"], p["key"], p["file"], p["line"]) for p in out["problems"]] == [
        (rows[1]["id"], "xue2025fitness", "main.tex", 6)]
    assert out["no_pdf"] == ["xue2025fitness"] and out["not_in_library"] == []
    r = run("claims", "joint-align", "--summary")
    assert "5 claims citing 3 keys" in r.out and "unchecked    4" in r.out and "own work     1" in r.out
    assert f"✗ unsupported xue2025fitness main.tex:6 ({rows[1]['id']})\n    about landscapes" in r.out
    assert "no PDF (1): xue2025fitness" in r.out
    assert run("claims", "joint-align", "--summary", "-q").out.split() == [rows[1]["id"]]
    assert run("claims", "joint-align", "--verdict", "own", "-q").out.split() == [rows[2]["id"]]
    assert run("claims", "joint-align", "--verdict", "unchecked", "-q").out.split() == \
        run("claims", "joint-align", "--unchecked", "-q").out.split()
    assert run("claims", "joint-align", "--verdict", "maybe").code == cli.USAGE
    run("cited", "remove", "smith2020missing")
    r = run("claims", "joint-align", "--summary")
    assert "not in the library (1): smith2020missing" in r.out and "ruru cited add" in r.out
