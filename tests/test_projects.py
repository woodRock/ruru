import json
from pathlib import Path

import pytest

from conftest import git_log
from ruru import cli, model
from ruru.errors import UsageError
from ruru.projects import BEGIN, END, BibFile, Project, entry_keys, scan_tex, strip_comments

HAND = """\
% my own preamble comment
@string{tai = {IEEE Transactions on Artificial Intelligence}}

@article{hand2019written,
  author = {Hand, Some},
  title = {Written by hand},
  journal = tai,
  year = {2019}
}
"""


# --- bib files -----------------------------------------------------------------------------------

def test_entry_keys_skip_strings_and_comments():
    text = HAND + "@comment{ignored, x}\n@Book( paren2020,\n title={x})\n  @misc{indented2021,}\n"
    assert entry_keys(text) == ["hand2019written", "paren2020", "indented2021"]


def test_render_appends_block_and_never_touches_the_rest():
    bib = BibFile.parse(HAND)
    assert not bib.has_block and bib.managed == {} and bib.outside_keys == ["hand2019written"]
    out = bib.render({"b2020b": "@misc{b2020b,\n  title = {B}\n}\n",
                      "a2020a": "@misc{a2020a,\n  title = {A}\n}"})
    assert out.startswith(HAND.rstrip("\n") + "\n\n" + BEGIN + "\n@misc{a2020a,")  # sorted by key
    assert out.endswith("}\n" + END + "\n")
    again = BibFile.parse(out)
    assert again.has_block and list(again.managed) == ["a2020a", "b2020b"]
    assert again.render(again.managed) == out  # idempotent
    assert again.outside_keys == ["hand2019written"]


def test_render_keeps_text_after_the_block():
    text = f"@misc{{x,}}\n\n{BEGIN}\n@misc{{old,\n}}\n{END}\n\n% trailing notes\n@misc{{y,}}\n"
    bib = BibFile.parse(text)
    assert bib.outside_keys == ["x", "y"] and list(bib.managed) == ["old"]
    out = bib.render({"new": "@misc{new,\n}\n"})
    assert out == f"@misc{{x,}}\n\n{BEGIN}\n@misc{{new,\n}}\n{END}\n\n% trailing notes\n@misc{{y,}}\n"
    assert BibFile.parse("").render({"k": "@misc{k,}"}) == f"{BEGIN}\n@misc{{k,}}\n{END}\n"


def test_unterminated_block():
    with pytest.raises(UsageError, match="no '% <<< ruru managed' line"):
        BibFile.parse(f"{BEGIN}\n@misc{{a,}}\n")


# --- tex -----------------------------------------------------------------------------------------

def test_scan_tex(tmp_path):
    (tmp_path / "sections").mkdir()
    (tmp_path / "main.tex").write_text(r"""
\documentclass{article}
\addbibresource[label=x]{refs.bib}
Intro \cite{a2020} and \citep[see][p.~2]{b2021, c2022}. 50\% sure \citet*{d2023}.
% \cite{commented2020}
\parencite{e2024}\textcite[ch.~3]{f2025} \autocite{a2020}
\input{sections/one}
\include{sections/two.tex}
\input{sections/missing}
\nocite{g2019}
\bibliography{refs, extra}
""")
    (tmp_path / "sections" / "one.tex").write_text(r"\citeauthor{h2018} \input{main}")  # a cycle
    (tmp_path / "sections" / "two.tex").write_text(r"\cite{ i2017 ,j2016 }")
    scan = scan_tex([tmp_path / "main.tex"])
    assert scan.keys == ["a2020", "b2021", "c2022", "d2023", "e2024", "f2025", "h2018", "i2017", "j2016",
                         "g2019"]
    assert [f.name for f in scan.files] == ["main.tex", "one.tex", "two.tex"]
    assert [f.name for f in scan.missing_files] == ["missing.tex"]
    assert [b.name for b in scan.bibs] == ["refs.bib", "refs.bib", "extra.bib"]
    assert not scan.nocite_all


def test_nocite_all_and_comments(tmp_path):
    (tmp_path / "m.tex").write_text("\\nocite{*}\n")
    assert scan_tex([tmp_path / "m.tex"]).nocite_all
    assert strip_comments("a % b\n50\\% c % d") == "a \n50\\% c "


# --- projects ------------------------------------------------------------------------------------

def test_project_from_dict():
    p = Project.from_dict("p", {"path": "~/x", "bib": "r.bib", "tex": "main.tex"})
    assert p.tex == ["main.tex"] and p.path == Path("~/x").expanduser() and p.bib_path.name == "r.bib"
    for name, d in [("bad name", {"path": "x", "bib": "b"}), ("p", {"path": "x"}),
                    ("p", {"path": "x", "bib": "b", "bib_encoding": "latin1"})]:
        with pytest.raises(UsageError):
            Project.from_dict(name, d)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "paper-repo"
    (root / "sections").mkdir(parents=True)
    (root / "main.tex").write_text(
        "\\cite{wood2026joint} and \\citep{xue2025fitness, smith2020missing}\n\\input{sections/intro}\n")
    (root / "sections" / "intro.tex").write_text("\\citet{wood2026joint}\n")
    (root / "references.bib").write_text(HAND)
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


def test_project_add_list_remove(lib, run, repo, tmp_path):
    assert run("project").code == cli.NO_MATCH
    r = run("project", "add", "ghost", "--path", str(tmp_path / "nowhere"), "--bib", "r.bib", "--tex", "m.tex")
    assert r.code == 0 and "does not exist (yet)" in r.err
    assert run("project", "add", "ja", "--path", "~/Desktop/joint-align", "--bib", "refs.bib",
               "--bib-encoding", "utf8", "-q").out == "ja\n"
    rows = json.loads(run("project", "--json").out)
    assert rows[1] == {"name": "ja", "path": "~/Desktop/joint-align", "bib": "refs.bib", "tex": [],
                       "bib_encoding": "utf8"}
    assert "~/Desktop/joint-align" in (lib / "ruru.toml").read_text()  # kept as written
    assert run("project", "-q").out.split() == ["ghost", "ja"] and "bib: refs.bib" in run("project", "list").out
    assert run("project", "add", "ja", "--path", "x", "--bib", "y").code == cli.USAGE
    assert run("project", "remove", "ghost").code == 0
    assert run("project", "remove", "ghost").code == cli.USAGE
    assert git_log(lib)[0] == "ruru: project remove ghost"


def test_cite(proj, run, repo):
    r = run("cite", "joint-align", "wood2026joint", "xue2025fitness")
    assert r.code == 0 and "updated (added: wood2026joint, xue2025fitness)" in r.out
    text = (repo / "references.bib").read_text()
    assert text.startswith(HAND.rstrip("\n") + "\n\n" + BEGIN)
    assert list(BibFile.parse(text).managed) == ["wood2026joint", "xue2025fitness"]
    assert json.loads(run("show", "wood2026joint", "--json").out)["projects"] == ["joint-align"]
    assert git_log(proj)[0] == "ruru: cite joint-align wood2026joint xue2025fitness"
    # Idempotent: same file, no new commit.
    assert "unchanged (added: nothing new)" in run("cite", "joint-align", "wood2026joint").out
    assert (repo / "references.bib").read_text() == text
    assert git_log(proj)[0] == "ruru: cite joint-align wood2026joint xue2025fitness"
    assert run("cite", "joint-align", "wood2026joint", "-q").out == "wood2026joint\n"


def test_cite_regenerates_from_the_library(proj, run, repo):
    run("cite", "joint-align", "wood2026joint")
    path = proj / "papers" / "wood2026joint" / "paper.md"
    p = model.loads(path.read_text())
    p.venue = "IEEE Transactions on Artificial Intelligence"
    path.write_text(model.dumps(p))
    run("cite", "joint-align", "xue2025fitness")
    assert "journal = {IEEE Transactions on Artificial Intelligence}" in (repo / "references.bib").read_text()


def test_cite_edge_cases(proj, run, repo, tmp_path):
    assert run("cite", "joint-align", "nope2020x").code == cli.USAGE
    assert BEGIN not in (repo / "references.bib").read_text()  # nothing written
    r = run("cite", "joint-align", "wood2026joint", "-q")
    assert r.code == 0
    # An entry defined by hand outside the block is left there, not duplicated.
    (repo / "references.bib").write_text((repo / "references.bib").read_text() + "\n@misc{xue2025fitness,}\n")
    r = run("cite", "joint-align", "xue2025fitness")
    assert "outside the managed block" in r.err
    assert "xue2025fitness" not in BibFile.parse((repo / "references.bib").read_text()).managed
    # A managed entry the library doesn't know is kept verbatim.
    text = (repo / "references.bib").read_text().replace(END, "@misc{foreign2020,\n  note = {kept}\n}\n" + END)
    (repo / "references.bib").write_text(text)
    r = run("cite", "joint-align", "wood2026joint")
    assert "foreign2020 is in the managed block but not in the library" in r.err
    assert "note = {kept}" in (repo / "references.bib").read_text()
    assert run("cite", "nope", "wood2026joint").code == cli.USAGE
    run("project", "add", "far", "--path", str(tmp_path / "gone"), "--bib", "refs.bib")
    assert "does not exist" in run("cite", "far", "wood2026joint").err


def test_cite_creates_the_bib_and_honours_project_encoding(proj, run, repo):
    run("add", "--title", "Ngā taonga", "--author", "Āperahama Ōtākou", "--year", "2020", "-q")
    run("project", "add", "utf", "--path", str(repo), "--bib", "new.bib", "--bib-encoding", "utf8")
    run("cite", "utf", "otakou2020nga")
    assert "Ōtākou, Āperahama" in (repo / "new.bib").read_text()
    run("cite", "joint-align", "otakou2020nga")
    assert r"{\=O}t{\=a}kou" in (repo / "references.bib").read_text()


def test_check_reports_every_issue(proj, run, repo):
    run("cite", "joint-align", "wood2026joint", "zhang2024kept")
    run("tag", "zhang2024kept", "+x")
    (repo / "references.bib").write_text((repo / "references.bib").read_text()
                                         + "\n@article{written2026,\n title = {Joint Retention-Time Alignment},\n"
                                           " year = {2026}\n}\n")
    path = proj / "papers" / "wood2026joint" / "paper.md"
    p = model.loads(path.read_text())
    p.volume = "7"
    path.write_text(model.dumps(p))
    r = run("check", "joint-align")
    assert r.code == cli.NO_MATCH
    assert "3 cited keys in 2 tex files" in r.out
    assert "missing from the bib, in the library (1): xue2025fitness" in r.out
    assert "missing from the bib and the library (1): smith2020missing" in r.out
    assert "in the managed block, never cited (1): zhang2024kept" in r.out
    assert "kept for this project, not cited (1): zhang2024kept" in r.out
    assert "out of date with the library (1): wood2026joint" in r.out
    assert "duplicate: wood2026joint and written2026 are the same paper" in r.out
    report = json.loads(run("check", "joint-align", "--json").out)
    assert report["issues"] == {
        "missing": [{"key": "xue2025fitness", "in_library": True},
                    {"key": "smith2020missing", "in_library": False}],
        "unused": ["zhang2024kept"], "kept_unused": ["zhang2024kept"], "stale": ["wood2026joint"],
        "duplicates": [["wood2026joint", "written2026"]],
    }
    assert report["issue_count"] == 6 and report["fixed"] == {}


def test_check_fix_and_prune(proj, run, repo):
    run("cite", "joint-align", "zhang2024kept")
    r = run("check", "joint-align", "--fix", "--prune")
    assert "fixed: cited wood2026joint, xue2025fitness" in r.out  # in citation order
    assert "fixed: pruned zhang2024kept" in r.out
    managed = BibFile.parse((repo / "references.bib").read_text()).managed
    assert sorted(managed) == ["wood2026joint", "xue2025fitness"]
    report = json.loads(run("check", "joint-align", "--json").out)
    assert report["issues"]["missing"] == [{"key": "smith2020missing", "in_library": False}]
    assert report["issues"]["kept_unused"] == ["zhang2024kept"]  # still tagged: that's Jesse's call
    assert run("check", "joint-align", "--fix", "--json").code == cli.NO_MATCH


def test_check_clean_project(proj, run, repo):
    (repo / "main.tex").write_text("\\cite{wood2026joint}\n")
    (repo / "sections" / "intro.tex").unlink()
    run("cite", "joint-align", "wood2026joint", "xue2025fitness", "zhang2024kept")
    run("check", "joint-align", "--prune")
    run("tag", "xue2025fitness", "+other")
    for key in ("xue2025fitness", "zhang2024kept"):
        path = proj / "papers" / key / "paper.md"
        p = model.loads(path.read_text())
        p.projects = []
        path.write_text(model.dumps(p))
    r = run("check", "joint-align")
    assert (r.code, r.out.splitlines()[-1]) == (cli.OK, "no issues")


def test_check_file_mode(proj, run, repo):
    (repo / "main.tex").write_text("\\cite{wood2026joint, unknown2020}\n\\bibliography{references}\n")
    report = json.loads(run("check", str(repo / "main.tex"), "--json").out)
    assert report["project"] is None and report["bib"].endswith("references.bib")
    assert report["issues"]["missing"][1] == {"key": "unknown2020", "in_library": False}
    assert report["issues"]["kept_unused"] == []
    assert run("check", str(repo / "main.tex"), "--fix").code == cli.USAGE
    (repo / "plain.tex").write_text("\\cite{wood2026joint}\n")
    r = run("check", str(repo / "plain.tex"))
    assert "no bib file" in r.err and "missing from the bib, in the library (1)" in r.out


def test_check_errors_and_warnings(proj, run, repo, tmp_path):
    assert run("check", "nope").code == cli.USAGE
    run("project", "add", "notex", "--path", str(repo), "--bib", "references.bib")
    assert "lists no tex files" in run("check", "notex").err
    run("project", "add", "nobib", "--path", str(repo), "--bib", "absent.bib", "--tex", "main.tex")
    assert "bib file not found" in run("check", "nobib").err
    (repo / "references.bib").write_text("@article{broken,\n title = {unclosed\n")
    assert run("check", "joint-align").code == cli.NO_MATCH  # still reports, without duplicates
