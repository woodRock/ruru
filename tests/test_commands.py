import io
import json
import sys

import pytest

from conftest import SAMPLE_BIB, git_log
from ruru import cli, model
from ruru.fsutil import library_lock

PAPER_KEYS = set(model.FIELDS) | {"path", "has_pdf"}


@pytest.fixture
def stocked(lib, run, tmp_path):
    bib = tmp_path / "refs.bib"
    bib.write_text(SAMPLE_BIB, encoding="utf-8")
    assert run("add", "--bib", str(bib), "-t", "Dim Reduction").code == 0
    r = run("add", "--title", "Joint Retention-Time Alignment of Many GC–MS Batches", "--author", "Jesse Wood",
            "--author", "Bing Xue", "--year", "2026", "--venue", "IEEE TAI", "--abstract", "Aligns many batches.",
            "-t", "gc-ms", "-q")
    assert r.out == "wood2026joint\n"
    return lib


def test_init(tmp_path, run):
    root = tmp_path / "lib"
    r = run("init", str(root))
    assert r.code == 0 and "contact_email" in r.err
    assert (root / "ruru.toml").exists() and (root / "seen.jsonl").exists()
    assert "*.pdf" in (root / ".gitignore").read_text()
    assert git_log(root) == ["ruru: init"]
    assert f'library = "{root.resolve()}"' in (tmp_path / "config" / "config.toml").read_text()
    assert run("init", str(root)).code == cli.USAGE  # already a library


def test_init_uses_configured_library(tmp_path, run, monkeypatch):
    monkeypatch.setenv("RURU_LIBRARY", str(tmp_path / "env-lib"))
    assert run("init", "--bib-encoding", "utf8").code == 0
    assert 'bib_encoding = "utf8"' in (tmp_path / "env-lib" / "ruru.toml").read_text()


def test_not_a_library(tmp_path, run):
    r = run("-L", str(tmp_path / "nowhere"), "list")
    assert r.code == cli.USAGE and "ruru init" in r.err


def test_bad_settings(lib, run):
    (lib / "ruru.toml").write_text('bib_encoding = "latin1"\n')
    assert "bib_encoding" in run("list").err
    (lib / "ruru.toml").write_text("not = [toml\n")
    assert run("list").code == cli.USAGE


def test_add_writes_paper_and_bib_and_commits(stocked, run):
    d = stocked / "papers" / "wood2026joint"
    p = model.loads((d / "paper.md").read_text(encoding="utf-8"))
    assert p.tags == ["gc-ms"] and p.source == "add" and p.added == model.today()
    assert "@article{wood2026joint," in (d / "paper.bib").read_text()
    assert git_log(stocked)[:2] == ["ruru: add wood2026joint (tags: gc-ms)",
                                    "ruru: add maaten2008visualizing nunez2025survey (tags: dim-reduction)"]
    assert run("show", "nunez2025survey", "--json").out.count("dim-reduction") == 1


def test_add_duplicate_returns_existing_key(stocked, run):
    r = run("add", "--title", "joint retention time alignment of many GC-MS batches", "--year", "2027", "-q")
    assert (r.code, r.out) == (0, "wood2026joint\n")
    assert "already in the library" in r.err
    assert run("add", "--title", "Other", "--doi", "https://doi.org/10.5555/fake.1").err.count("maaten2008") == 1


def test_add_key_clash_gets_suffix(stocked, run):
    r = run("add", "--title", "Joint Something Else", "--author", "A. Wood", "--year", "2026", "-q")
    assert r.out == "wood2026jointa\n"


def test_add_keep_key_and_stdin(lib, run, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(SAMPLE_BIB))
    assert run("add", "--bib", "-", "--keep-key", "-q").out == "vandermaaten2008\nnunez2025\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(SAMPLE_BIB.replace("10.5555/FAKE.1", "10.1/other")
                                                  .replace("Visualizing", "Seeing")))
    assert run("add", "--bib", "-", "--keep-key").code == cli.PARTIAL  # vandermaaten2008 taken: skipped


@pytest.mark.parametrize("args", [
    ["add"], ["add", "--bib", "/no/such.bib"], ["add", "--title", "X", "-p", "nope"],
])
def test_add_errors(lib, run, args, tmp_path):
    assert run(*args).code == cli.USAGE


def test_add_empty_bib(lib, run, tmp_path):
    (tmp_path / "e.bib").write_text("% nothing\n")
    assert "no BibTeX entries" in run("add", "--bib", str(tmp_path / "e.bib")).err


def test_list_filters_and_order(stocked, run):
    assert run("list", "-q").out.split() == ["wood2026joint", "nunez2025survey", "maaten2008visualizing"]
    assert run("list", "-t", "dim-reduction", "-q").out.split() == ["nunez2025survey", "maaten2008visualizing"]
    assert run("list", "-s", "read").code == cli.NO_MATCH
    assert run("list", "-p", "x", "--json").out.strip() == "[]"
    assert run("list", "--since", "2999-01-01").code == cli.NO_MATCH
    assert run("list", "--since", "yesterday").code == cli.USAGE
    rows = json.loads(run("list", "--json").out)
    assert set(rows[0]) == PAPER_KEYS
    assert rows[0]["added"] == model.today().isoformat()
    assert rows[0]["path"] == "papers/wood2026joint/paper.md" and rows[0]["has_pdf"] is False


def test_list_empty_library(lib, run):
    r = run("list")
    assert r.code == cli.NO_MATCH and r.out == "" and "no papers" in r.err


def test_show(stocked, run):
    (stocked / "papers" / "wood2026joint" / "paper.pdf").write_bytes(b"%PDF")
    out = run("show", "wood2026joint").out
    assert "Jesse Wood, Bing Xue" in out and "Aligns many batches." in out and "(pdf)" in out
    full = json.loads(run("show", "wood2026joint", "--json").out)
    assert set(full) == PAPER_KEYS | {"notes", "agent_notes"} and full["has_pdf"] is True
    assert run("show", "wood2026joint", "-q").out == "wood2026joint\n"
    assert run("show", "nunez2025survey", "--bib").out.isascii()
    assert "Núñez" in run("show", "nunez2025survey", "--bib", "--bib-encoding", "utf8").out
    r = run("show", "nope", "--json")
    assert (r.code, r.out) == (cli.USAGE, "") and "no paper" in r.err


def test_search(stocked, run):
    rows = json.loads(run("search", "nunez", "pyrolysis", "--json").out)
    assert [r["key"] for r in rows] == ["nunez2025survey"] and rows[0]["score"] > 0
    assert run("search", "batches", "-q").out == "wood2026joint\n"  # abstract
    assert run("search", "GC-MS", "-q").out.split() == ["nunez2025survey", "wood2026joint"]
    r = run("search", "nothing-like-this")
    assert (r.code, r.out) == (cli.NO_MATCH, "")
    assert run("search", "nothing", "--json").out.strip() == "[]"
    assert "wood2026joint" in run("search", "jesse").out


def test_search_finds_notes(stocked, run):
    run("note", "maaten2008visualizing", "--agent", "-m", "Perplexity matters a lot.")
    assert run("search", "perplexity", "-q").out == "maaten2008visualizing\n"


def test_tag(stocked, run):
    r = run("tag", "wood2026joint", "maaten2008visualizing", "+Key Paper", "-dim-reduction")
    assert r.code == 0
    assert json.loads(run("show", "maaten2008visualizing", "--json").out)["tags"] == ["key-paper"]
    assert git_log(stocked)[0] == "ruru: tag wood2026joint maaten2008visualizing +key-paper -dim-reduction"
    assert run("tag", "wood2026joint", "+x", "-q").out == "wood2026joint\n"
    assert run("tag", "wood2026joint").code == cli.USAGE
    assert run("tag", "nope", "+x").code == cli.USAGE


def test_status(stocked, run):
    assert run("status", "wood2026joint", "nunez2025survey", "read").code == 0
    p = json.loads(run("show", "wood2026joint", "--json").out)
    assert (p["status"], p["read_on"]) == ("read", model.today().isoformat())
    assert run("status", "wood2026joint", "bogus").code == cli.USAGE
    assert git_log(stocked)[0] == "ruru: status wood2026joint nunez2025survey read"


def test_agent_note(stocked, run, tmp_path, monkeypatch):
    assert run("note", "wood2026joint", "--agent", "--kind", "summary", "-m", "Abstract only: X.").code == 0
    (tmp_path / "n.md").write_text("From a file.")
    monkeypatch.setenv("RURU_AGENT", "claude")
    assert run("note", "wood2026joint", "--agent", "-F", str(tmp_path / "n.md"), "--by", "claude").code == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO("From stdin."))
    assert run("note", "wood2026joint", "--agent", "-F", "-", "-q").out == "wood2026joint\n"
    p = model.loads((stocked / "papers" / "wood2026joint" / "paper.md").read_text(encoding="utf-8"))
    today = model.today().isoformat()
    assert p.notes == ""
    assert p.agent_notes == (f"### {today} — summary (agent)\n\nAbstract only: X.\n\n"
                             f"### {today} — note (claude)\n\nFrom a file.\n\n"
                             f"### {today} — note (claude)\n\nFrom stdin.")
    assert git_log(stocked)[2] == "ruru: note wood2026joint (summary, agent)"


@pytest.mark.parametrize("args", [
    ["--agent"], ["--agent", "-m", "  "], ["-m", "x"],
])
def test_note_errors(stocked, run, args):
    assert run("note", "wood2026joint", *args).code == cli.USAGE


def test_note_editor(stocked, run, tmp_path, monkeypatch):
    editor = tmp_path / "ed.sh"
    editor.write_text('#!/bin/sh\nprintf "Written in the editor.\\n" > "$1"\n')
    editor.chmod(0o755)
    monkeypatch.setenv("VISUAL", str(editor))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    assert run("note", "wood2026joint").code == 0
    assert json.loads(run("show", "wood2026joint", "--json").out)["notes"] == "Written in the editor."
    assert git_log(stocked)[0] == "ruru: notes wood2026joint"
    assert "unchanged" in run("note", "wood2026joint").err
    monkeypatch.setenv("VISUAL", "false")
    assert run("note", "wood2026joint").code == cli.USAGE


def test_note_editor_needs_tty(stocked, run, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    r = run("note", "wood2026joint")
    assert r.code == cli.USAGE and "--agent" in r.err


def test_no_commit(stocked, run):
    before = git_log(stocked)
    assert run("--no-commit", "tag", "wood2026joint", "+a").code == 0
    assert run("tag", "wood2026joint", "+b", "--no-commit").code == 0
    assert run("status", "wood2026joint", "reading", "--no-commit").code == 0
    assert git_log(stocked) == before
    (stocked / "ruru.toml").write_text((stocked / "ruru.toml").read_text().replace("auto_commit = true",
                                                                                  "auto_commit = false"))
    assert run("tag", "wood2026joint", "+c").code == 0
    assert git_log(stocked) == before


def test_lock_held_exits_4(stocked, run):
    with library_lock(stocked, timeout=0):
        r = run("tag", "wood2026joint", "+x")
        assert run("list", "-q").code == 0  # reads never lock
    assert r.code == cli.LOCKED and "locked" in r.err


def test_no_ansi_when_piped(stocked, run):
    for args in (["list"], ["show", "wood2026joint"], ["search", "gc"]):
        assert "\033[" not in run(*args).out


def test_colour_on_tty_unless_no_color(stocked, run, monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    from ruru import output
    assert output.color_enabled()
    assert "\033[1m" in output.style("x", "bold")
    monkeypatch.setenv("NO_COLOR", "1")
    assert output.style("x", "bold") == "x"


def test_config_path_fallbacks(tmp_path, monkeypatch):
    from ruru import config
    monkeypatch.delenv("RURU_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config.config_path() == tmp_path / "xdg" / "ruru" / "config.toml"
    assert config.read_user_config() == {}
    config.write_user_config(tmp_path / "lib")
    assert config.resolve_library() == tmp_path / "lib"
    assert config.resolve_library("~/x") == config.Path("~/x").expanduser()


def test_corrupt_paper_is_a_clear_error(stocked, run):
    (stocked / "papers" / "wood2026joint" / "paper.md").write_text("oops")
    r = run("list")
    assert r.code == cli.USAGE and "papers/wood2026joint/paper.md" in r.err


def test_rekey(stocked, run, monkeypatch):
    d = stocked / "papers"
    (d / "wood2026joint" / "paper.pdf").write_bytes(b"%PDF")
    run("note", "wood2026joint", "--agent", "-m", "Keep me.")
    r = run("rekey", "wood2026joint", "wood2026jointalign")
    assert (r.code, r.out) == (0, "wood2026joint -> wood2026jointalign\n")
    assert not (d / "wood2026joint").exists() and (d / "wood2026jointalign" / "paper.pdf").exists()
    p = json.loads(run("show", "wood2026jointalign", "--json").out)
    assert p["key"] == "wood2026jointalign" and "Keep me." in p["agent_notes"]
    assert "@article{wood2026jointalign," in (d / "wood2026jointalign" / "paper.bib").read_text()
    assert git_log(stocked)[0] == "ruru: rekey wood2026joint -> wood2026jointalign"
    assert run("show", "wood2026joint").code == cli.USAGE
    # Default: the key the metadata gives now.
    assert run("rekey", "wood2026jointalign", "-q").out == "wood2026joint\n"
    for bad in (["wood2026joint", "wood2026joint"], ["wood2026joint", "nunez2025survey"],
                ["wood2026joint", "has space"], ["nope"]):
        assert run("rekey", *bad).code == cli.USAGE


def test_rekey_warns_about_projects(stocked, run, tmp_path):
    run("project", "add", "p", "--path", str(tmp_path), "--bib", "r.bib")
    run("cite", "p", "wood2026joint")
    r = run("rekey", "wood2026joint", "wood2026x")
    assert "p: replace \\cite{wood2026joint} with \\cite{wood2026x}" in r.err


def test_import_merges_labels_and_skips_key_clashes(lib, run, tmp_path):
    run("project", "add", "a", "--path", str(tmp_path), "--bib", "a.bib")
    run("project", "add", "b", "--path", str(tmp_path), "--bib", "b.bib")
    (tmp_path / "a.bib").write_text(SAMPLE_BIB, encoding="utf-8")
    assert run("add", "--bib", str(tmp_path / "a.bib"), "--keep-key", "-p", "a", "-q").code == 0
    # The same papers again for project b, one under another key, plus a key clash with a different paper.
    other = SAMPLE_BIB.replace("{nunez2025,", "{nunez2025survey,") + (
        "\n@misc{vandermaaten2008, title = {A different paper entirely}, year = {2001}}\n"
        "@misc{fresh2020, title = {Fresh}, year = {2020}}\n")
    other = other.replace("@article{vandermaaten2008,", "@article{vdm2008,", 1)
    (tmp_path / "b.bib").write_text(other, encoding="utf-8")
    r = run("add", "--bib", str(tmp_path / "b.bib"), "--keep-key", "-p", "b")
    assert r.code == cli.PARTIAL
    assert "already in the library as nunez2025 (this file calls it nunez2025survey)" in r.err
    assert "vandermaaten2008: key 'vandermaaten2008' is already taken; not added" in r.err
    assert "added fresh2020" in r.out
    assert json.loads(run("show", "nunez2025", "--json").out)["projects"] == ["a", "b"]
    assert json.loads(run("show", "vandermaaten2008", "--json").out)["projects"] == ["a", "b"]
    assert git_log(lib)[0] == "ruru: add fresh2020 + labels on 2 existing (projects: b)"
    import subprocess
    assert subprocess.run(["git", "-C", str(lib), "status", "--porcelain"], capture_output=True,
                          text=True).stdout == ""
    # Again: nothing new, labels already there, no commit.
    before = git_log(lib)
    run("add", "--bib", str(tmp_path / "a.bib"), "--keep-key", "-p", "a")
    assert git_log(lib) == before


def test_rm(stocked, run, tmp_path):
    keys = run("list", "-q").out.split()
    other = next(k for k in keys if k != "wood2026joint")
    (stocked / "papers" / "wood2026joint" / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    assert run("rm", "wood2026joint", "nosuchkey").code == cli.USAGE  # nothing removed
    assert (stocked / "papers" / "wood2026joint" / "paper.md").exists()
    r = run("rm", "wood2026joint")
    assert r.code == 0 and r.out.startswith("removed wood2026joint: Joint")
    assert not (stocked / "papers" / "wood2026joint").exists()
    assert git_log(stocked)[0] == "ruru: rm wood2026joint"
    assert run("show", "wood2026joint").code == cli.USAGE
    seen = json.loads(run("seen", "-d", "remove", "--json").out)
    assert [(s["key"], s["title"]) for s in seen] == [("wood2026joint", "Joint Retention-Time Alignment of Many "
                                                                         "GC–MS Batches")]
    assert "remove wood2026joint" in run("seen").out
    run("project", "add", "demo", "--path", str(tmp_path), "--bib", "refs.bib")
    run("add", "--bib", str(tmp_path / "refs.bib"), "-p", "demo", "--no-enrich")  # labels the existing two
    r = run("rm", other, "-q")
    assert r.out == f"{other}\n" and "was in demo" in r.err
    assert run("rm").code == cli.USAGE


def test_list_sort_and_no_project(stocked, run, tmp_path):
    run("add", "--title", "Zebrafish lipids", "--author", "Ann Aardvark", "--year", "1999", "-q")
    run("status", "aardvark1999zebrafish", "read")
    run("status", "maaten2008visualizing", "reading")
    keys = lambda *a: run("list", "-q", *a).out.split()
    everything = set(keys())
    assert keys("--sort", "key") == sorted(everything, key=str.lower)
    assert keys("--sort", "key", "-r") == sorted(everything, key=str.lower, reverse=True)
    assert keys("--sort", "year") == ["wood2026joint", "nunez2025survey", "maaten2008visualizing",
                                      "aardvark1999zebrafish"]
    assert keys("--sort", "status") == ["nunez2025survey", "wood2026joint", "maaten2008visualizing",
                                        "aardvark1999zebrafish"]
    assert keys("--sort", "title")[0] == "nunez2025survey"  # "A Survey…"
    assert run("list", "--sort", "colour").code == cli.USAGE
    assert keys("-r") == keys()[::-1]
    run("project", "add", "demo", "--path", str(tmp_path), "--bib", "refs.bib")
    run("add", "--bib", str(tmp_path / "refs.bib"), "-p", "demo", "--no-enrich")  # labels SAMPLE_BIB's two
    assert set(keys("-p", "none")) == {"wood2026joint", "aardvark1999zebrafish"}
    assert set(keys("-p", "demo")) == everything - {"wood2026joint", "aardvark1999zebrafish"}
    r = run("project", "add", "none", "--path", str(tmp_path), "--bib", "x.bib")
    assert r.code == cli.USAGE and "in no project" in r.err


def test_same_day_papers_list_by_commit_time(lib, run):
    import os
    import subprocess
    for title, stamp in (("Zzz filed first", "@1760600000"), ("Aaa filed second", "@1760700000")):
        run("add", "--title", title, "--author", "Ann Aardvark", "--year", "2020", "-q")
        subprocess.run(["git", "-C", str(lib), "commit", "--amend", "--no-edit", "--quiet", "--date", stamp],
                       check=True, env={**os.environ, "GIT_COMMITTER_DATE": stamp})
    assert run("list", "-q").out.split() == ["aardvark2020aaa", "aardvark2020zzz"]  # not Z-A by key
    assert run("list", "-q", "--sort", "year").out.split() == ["aardvark2020aaa", "aardvark2020zzz"]
    run("add", "--title", "Mmm not committed", "--author", "Ann Aardvark", "--year", "2020", "-q", "--no-commit")
    assert run("list", "-q").out.split()[0] == "aardvark2020mmm"
