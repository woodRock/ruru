"""`ruru batch` (many commands, one process, only agent-safe ones) and `ruru undo`."""

import json
import subprocess
from datetime import datetime

import pytest
from test_similar import CHROMATOGRAPHY, add_paper, key_of, write_item

from conftest import git_log
from ruru import cli
from ruru.commands.system import BATCHABLE


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def seeded(lib, run):
    for title, abstract, authors, year in CHROMATOGRAPHY[:3]:
        add_paper(run, title, abstract, authors, year)
    write_item(lib, "item-1", "Gas chromatography peaks", "peaks", fetched=datetime(2026, 10, 18, 8, 0, 0))
    return lib


def batch(run, tmp_path, text, *flags):
    f = tmp_path / "lines.txt"
    f.write_text(text)
    r = run("batch", "-F", str(f), *flags)
    return r.code, json.loads(r.out)


# --- batch ---------------------------------------------------------------------------------------

def test_batch_runs_each_line_and_captures_its_output(seeded, run, tmp_path):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    code, results = batch(run, tmp_path, f"""
# lookups
show {key} --json
ruru search chromatography -q

list --json -p nonesuch
""")
    assert code == 0 and len(results) == 3
    assert results[0]["data"]["key"] == key and results[0]["code"] == 0 and results[0]["stderr"] == ""
    assert results[1]["argv"] == ["search", "chromatography", "-q"] and key in results[1]["stdout"]
    assert results[1]["data"] is None  # not JSON
    assert results[2]["code"] == cli.NO_MATCH and results[2]["data"] == []


def test_batch_quotes_like_a_shell(seeded, run, tmp_path):
    _, results = batch(run, tmp_path, "search 'Retention time' --json\n")
    assert len(results[0]["data"]) == 1


def test_batch_exit_codes(seeded, run, tmp_path):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    assert batch(run, tmp_path, f"show {key}\nshow nosuchkey\n")[0] == cli.PARTIAL
    assert batch(run, tmp_path, "show nosuchkey\nshow another\n")[0] == cli.USAGE  # all failed alike
    assert batch(run, tmp_path, "search zzzzqq\n")[0] == cli.OK  # no match is an answer, not a failure
    assert batch(run, tmp_path, "")[1] == []


def test_batch_stops_on_error_when_asked(seeded, run, tmp_path):
    code, results = batch(run, tmp_path, "show nosuchkey\nlist -q\n", "--stop-on-error")
    assert len(results) == 1 and code == cli.USAGE
    assert len(batch(run, tmp_path, "show nosuchkey\nlist -q\n")[1]) == 2


@pytest.mark.parametrize("line", [
    "keep item-1", "later item-1", "cite demo a", "rm a", "unkeep a", "feeds add x --kind rss --url u",
    "feeds --json add x --kind rss --url u", "feeds remove x", "project add p --path . --bib b",
    "review keep r", "review discard r", "own add a", "cited add a --url u", "cited remove a", "add 10.1/x",
    "status a read", "refresh", "enrich", "pdf missing --fetch --open".replace("missing", "show a"),
    "undo", "batch -F x", "tui", "rekey a b", "init /tmp/x", "check demo --fix",
])
def test_batch_refuses_what_agents_may_not_run(seeded, run, tmp_path, line):
    code, results = batch(run, tmp_path, line + "\n")
    assert code == cli.USAGE and results[0]["code"] == cli.USAGE
    assert "not available in a batch" in results[0]["stderr"]
    assert git_log(seeded)[0] == "ruru: fetch item-1"  # nothing was written


def test_batch_refuses_global_options_and_garbage(seeded, run, tmp_path):
    _, results = batch(run, tmp_path, "-L /elsewhere list\n--no-commit list\nnot-a-command\n'unterminated\n")
    assert [r["code"] for r in results] == [cli.USAGE] * 4
    assert "start a line with the command" in results[0]["stderr"] and "invalid choice" in results[2]["stderr"]
    assert "No closing quotation" in results[3]["stderr"]


def test_batch_help_is_captured(seeded, run, tmp_path):
    _, results = batch(run, tmp_path, "list -h\n")
    assert results[0]["code"] == 0 and "usage: ruru list" in results[0]["stdout"]


def test_batch_commands_do_not_read_the_batch_input(seeded, run, tmp_path):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    _, results = batch(run, tmp_path, f"note {key} --agent -F -\n")
    assert results[0]["code"] == cli.USAGE and "empty note" in results[0]["stderr"]


def test_batch_may_write_what_agents_may_write(seeded, run, tmp_path):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    code, results = batch(run, tmp_path, f"tag {key} +reviewed\nskip item-1 --by claude --reason off-topic\n"
                                         f"note {key} --agent -m 'checked'\n")
    assert code == 0 and [r["code"] for r in results] == [0, 0, 0]
    log = git_log(seeded)
    assert any(m.startswith("ruru: skip item-1") for m in log) and any("tag" in m for m in log)
    assert "reviewed" in json.loads(run("show", key, "--json").out)["tags"]


def test_batch_no_commit_applies_to_every_line(seeded, run, tmp_path):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    before = git_log(seeded)
    f = tmp_path / "l.txt"
    f.write_text(f"tag {key} +x\n")
    assert run("--no-commit", "batch", "-F", str(f)).code == 0
    assert git_log(seeded) == before and "x" in json.loads(run("show", key, "--json").out)["tags"]


def test_batch_needs_a_file(seeded, run, tmp_path):
    assert run("batch").code == cli.USAGE
    assert run("batch", "-F", str(tmp_path / "missing.txt")).code == cli.USAGE


def test_the_batchable_commands_are_all_allowed_to_agents_individually():
    """Everything a batch can run is on the README's allow list, and none of it is on the deny list."""
    import re
    from pathlib import Path
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    block = re.search(r"```json\n(\{\n  \"permissions\".*?)\n```", readme, re.S)
    rules = json.loads(block.group(1))["permissions"]

    def covered(rule_list, command, action):
        for rule in rule_list:
            m = re.fullmatch(r"Bash\(ruru ([a-z]+)(?: ([a-z]+))?:\*\)", rule)
            if m.group(1) == command and (m.group(2) is None or m.group(2) == action or action is None):
                return True
        return False
    for command, action in sorted(BATCHABLE, key=str):
        name = f"{command} {action or ''}".strip()
        assert covered(rules["allow"], command, action), f"`ruru {name}` is not on the allow list"
        assert not [r for r in rules["deny"] if r == f"Bash(ruru {command}:*)"], command
        if action:
            assert f"Bash(ruru {command} {action}:*)" not in rules["deny"]


# --- undo ----------------------------------------------------------------------------------------

def test_undo_lists_and_reverts_the_last_commit(seeded, run):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    run("tag", key, "+extra")
    assert "extra" in json.loads(run("show", key, "--json").out)["tags"]
    listing = run("undo", "--list")
    assert listing.code == 0 and f"ruru: tag {key}" in listing.out.splitlines()[0]
    assert "extra" in json.loads(run("show", key, "--json").out)["tags"]  # listing changes nothing
    r = run("undo")
    assert r.code == 0 and r.out.startswith("undid ") and f"ruru: tag {key}" in r.out
    assert "extra" not in json.loads(run("show", key, "--json").out)["tags"]
    assert git_log(seeded)[0].startswith("ruru: undo tag")
    run("undo")  # undoing the undo is a redo
    assert "extra" in json.loads(run("show", key, "--json").out)["tags"]


def test_undo_a_keep_puts_the_item_back(seeded, run):
    assert run("keep", "item-1", "-q").code == 0
    assert "item-1" not in run("inbox", "-q").out
    assert run("undo").code == 0
    assert "item-1" in run("inbox", "-q").out
    assert run("seen", "-q").code == cli.NO_MATCH  # the decision was undone too


def test_undo_several_and_dry_run(seeded, run):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    run("tag", key, "+one")
    run("tag", key, "+two")
    rows = json.loads(run("undo", "-n", "2", "--dry-run", "--json").out)
    assert len(rows) == 2 and rows[0]["message"].startswith("ruru: tag")
    assert all(r["reverted_as"] is None and key in " ".join(r["files"]) for r in rows)
    assert json.loads(run("show", key, "--json").out)["tags"] == ["one", "two"]
    done = json.loads(run("undo", "-n", "2", "--json").out)
    assert len(done) == 2 and all(r["reverted_as"] for r in done)
    assert json.loads(run("show", key, "--json").out)["tags"] == []
    assert len(run("undo", "-n", "2", "-q", "--dry-run").out.split()) == 2


def test_undo_stops_at_a_commit_that_is_not_rurus(seeded, run):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    run("tag", key, "+mine")
    (seeded / "notes.txt").write_text("by hand")
    git(seeded, "add", "notes.txt")
    git(seeded, "commit", "-q", "-m", "wrote some notes")
    r = run("undo")
    assert r.code == cli.NO_MATCH and "not made by ruru" in r.err
    assert run("undo", "--list").code == cli.NO_MATCH
    git(seeded, "revert", "--no-edit", "HEAD")  # a Revert commit is not ruru's either
    assert run("undo").code == cli.NO_MATCH
    assert (seeded / "notes.txt").exists() is False


def test_undo_n_beyond_the_ruru_commits(seeded, run):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    (seeded / "x.txt").write_text("x")
    git(seeded, "add", "x.txt")
    git(seeded, "commit", "-q", "-m", "manual")
    run("tag", key, "+a")
    r = run("undo", "-n", "3")
    assert r.code == cli.NO_MATCH and "only the newest 1" in r.err and "ruru undo -n 1" in r.err
    assert "a" in json.loads(run("show", key, "--json").out)["tags"]  # nothing was undone
    assert run("undo", "-n", "1").code == 0
    assert run("undo").code == 0  # that undo is itself ruru's: undoing it re-applies the tag


def test_undo_refuses_when_the_files_have_unsaved_changes(seeded, run):
    key = key_of(run, CHROMATOGRAPHY[0][0])
    run("tag", key, "+one")
    path = seeded / "papers" / key / "paper.md"
    path.write_text(path.read_text() + "\nedited by hand\n")
    r = run("undo")
    assert r.code == cli.USAGE and "uncommitted changes" in r.err and "paper.md" in r.err
    assert "edited by hand" in path.read_text()  # untouched


def test_undo_needs_a_repository(tmp_path, run, monkeypatch):
    root = tmp_path / "plain"
    assert run("init", str(root)).code == 0
    monkeypatch.setenv("RURU_LIBRARY", str(root))
    subprocess.run(["rm", "-rf", str(root / ".git")], check=True)
    r = run("undo")
    assert r.code == cli.USAGE and "not a git repository" in r.err
    assert run("undo", "-n", "0").code == cli.USAGE


def test_undo_refuses_zero(seeded, run):
    r = run("undo", "-n", "0")
    assert r.code == cli.USAGE and "at least 1" in r.err
