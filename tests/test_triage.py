import json
import subprocess
import sys
import tomllib
from datetime import date

import pytest

from conftest import git_log
from httpfix import FEEDS, Router
from ruru import cli, http, inbox, model
from ruru.feeds import Feed
from ruru.wizard import Wizard, slug

SINCE = "2026-01-01"


@pytest.fixture
def feeds_lib(lib, run):
    for name in ("anal-chem", "xue", "jat"):
        assert run("feeds", "add", name, *FEEDS[name]).code == 0
    return lib


@pytest.fixture
def filled(feeds_lib, run, net):
    r = run("fetch", "--since", SINCE)
    assert r.code == 0, r.err
    return feeds_lib


def test_feeds_add_list_remove(lib, run):
    r = run("feeds")
    assert r.code == cli.NO_MATCH and "wizard" in r.err
    for name, args in FEEDS.items():
        assert run("feeds", "add", name, *args, "-q").out == f"{name}\n"
    settings = tomllib.loads((lib / "ruru.toml").read_text())
    assert [f["name"] for f in settings["feeds"]] == list(FEEDS)
    assert "# Sent to OpenAlex and Crossref" in (lib / "ruru.toml").read_text()  # comments kept
    assert git_log(lib)[0] == "ruru: feeds add jat"
    rows = json.loads(run("feeds", "list", "--json").out)
    assert rows[1] == {"name": "anal-chem", "kind": "crossref", "title": "Analytical Chemistry",
                       "issn": "0003-2700", "last_fetch": None}
    assert "never" in run("feeds").out
    assert run("feeds", "-q").out.split() == list(FEEDS)
    assert run("feeds", "add", "jat", "--kind", "rss", "--url", "u").code == cli.USAGE  # taken
    assert run("feeds", "add", "Bad Name", "--kind", "rss", "--url", "u").code == cli.USAGE
    assert run("feeds", "add", "x", "--kind", "arxiv").code == cli.USAGE  # no query
    assert run("feeds", "remove", "xue").out == "removed feed xue\n"
    assert "xue" not in run("feeds", "-q").out.split()
    assert run("feeds", "remove", "xue").code == cli.USAGE


def test_fetch_requires_feeds(lib, run):
    r = run("fetch")
    assert r.code == cli.USAGE and "ruru feeds wizard" in r.err
    run("feeds", "add", *["one", *FEEDS["jat"]])
    assert run("fetch", "-f", "nope").code == cli.USAGE


def test_fetch_fills_inbox_and_commits(feeds_lib, run, net, monkeypatch):
    monkeypatch.setattr(model, "today", lambda: date(2026, 10, 17))
    r = run("fetch", "--since", SINCE)
    assert r.code == 0
    assert "anal-chem                5 new, 0 already seen" in r.out and "9 new items" in r.out
    ids = run("inbox", "-q").out.split()
    assert len(ids) == 9 and "doi-10.1002-jat.70464" in ids and "doi-10.1145-3847110" in ids
    assert git_log(feeds_lib)[0] == "ruru: fetch 9 new (anal-chem 5, xue 1, jat 3)"
    files = sorted(p.name for p in (feeds_lib / "inbox" / "2026-10-17").iterdir())
    assert len(files) == 9 and all(f.endswith(".md") for f in files)
    state = json.loads((feeds_lib / ".ruru" / "feeds.json").read_text())
    assert state["jat"] == {"last_fetch": "2026-10-17", "start": SINCE}
    # RSS entries with a DOI were resolved through Crossref.
    item = json.loads(run("show", "doi-10.1002-jat.70464", "--json").out)
    assert item["source"] == "feed:jat" and item["publisher"] == "Wiley"  # EarlyView: no volume yet
    # A second fetch finds nothing new: everything is already in the inbox.
    r = run("fetch", "--json")
    assert json.loads(r.out)["new"] == [] and json.loads(r.out)["feeds"][0]["seen"] == 5


def test_fetch_dedupes_against_library_and_seen(feeds_lib, run, net):
    net.add(r"api\.crossref\.org/works/10\.1145/3847110", body=json.dumps(
        {"message": {"DOI": "10.1145/3847110", "title": ["Fitness Landscape Compression"],
                     "type": "journal-article"}}))
    net.add(r"api\.openalex\.org/works/doi:10\.1145", status=404)
    assert run("add", "10.1145/3847110", "-q").code == 0  # already kept
    run("fetch", "--since", SINCE)
    assert "doi-10.1145-3847110" not in run("inbox", "-q").out
    run("skip", "doi-10.1002-jat.70464")
    for p in (feeds_lib / "inbox").glob("*/*.md"):
        p.unlink()  # forget the inbox: only the library and seen.jsonl remain
    r = json.loads(run("fetch", "--since", SINCE, "--json").out)
    assert "doi-10.1002-jat.70464" not in r["new"] and len(r["new"]) == 7


def test_fetch_partial_failure(feeds_lib, run, monkeypatch):
    router = Router().add("crossref.org/journals", "crossref_journal_analchem.json").add(".", status=503)
    monkeypatch.setattr(http, "default_transport", router)
    r = run("fetch", "--since", SINCE)
    assert r.code == cli.PARTIAL
    assert "xue: api.openalex.org: HTTP 503" in r.err and "failed" in r.out and "5 new items" in r.out
    state = json.loads((feeds_lib / ".ruru" / "feeds.json").read_text())
    assert set(state) == {"anal-chem"}  # failed feeds are retried from their old position


def test_fetch_keeps_rss_stub_when_doi_lookup_fails(lib, run, net):
    run("feeds", "add", "jat", *FEEDS["jat"])
    net.routes.insert(0, Router().add(r"jat\.70464", status=404).routes[0])
    net.routes.insert(0, Router().add(r"openalex.*jat\.70464", status=404).routes[0])
    r = run("fetch", "--since", SINCE)
    assert r.code == 0 and "kept the feed's details" in r.err
    item = json.loads(run("show", "doi-10.1002-jat.70464", "--json").out)
    assert item["title"].startswith("Toxicity of Organophosphate") and item["authors"][0] == "Bazila Rehman"


def test_inbox_listing(filled, run):
    rows = json.loads(run("inbox", "--json").out)
    assert len(rows) == 9
    assert set(rows[0]) == set(inbox.ITEM_FIELDS) | {"path", "viewed"}
    assert rows[0]["path"].startswith("inbox/") and rows[0]["suggestion"] is None
    assert len(run("inbox", "-n", "2", "-q").out.split()) == 2
    assert run("inbox", "-f", "xue", "-q").out == "doi-10.1145-3847110\n"
    assert run("inbox", "--suggested", "keep").code == cli.NO_MATCH
    assert len(run("inbox", "--suggested", "none", "-q").out.split()) == 9
    assert "[anal-chem]" in run("inbox").out


def test_empty_inbox(lib, run):
    r = run("inbox")
    assert r.code == cli.NO_MATCH and "empty" in r.err
    assert run("inbox", "--json").out.strip() == "[]"


def test_suggest(filled, run, monkeypatch):
    monkeypatch.setenv("RURU_AGENT", "claude")
    r = run("suggest", "doi-10.1145-3847110", "keep", "--reason", "GP fitness landscapes; Bing's group", "--by",
            "claude")
    assert r.out == "doi-10.1145-3847110: suggested keep\n"
    item = json.loads(run("show", "doi-10.1145-3847110", "--json").out)
    assert item["suggestion"] == {"action": "keep", "reason": "GP fitness landscapes; Bing's group",
                                  "by": "claude", "date": model.today().isoformat()}
    assert "→ keep: GP fitness landscapes" in run("inbox", "--suggested", "keep").out
    assert "suggested: keep (claude)" in run("show", "doi-10.1145-3847110").out
    assert git_log(filled)[0] == "ruru: suggest keep doi-10.1145-3847110 (claude)"
    assert run("suggest", "doi-10.1145-3847110", "keep", "--reason", " ").code == cli.USAGE
    assert run("suggest", "nope", "skip", "--reason", "x").code == cli.USAGE
    assert run("suggest", "doi-10.1145-3847110", "maybe", "--reason", "x").code == cli.USAGE


def test_keep_later_skip(filled, run):
    r = run("keep", "doi-10.1145-3847110", "doi-10.1002-jat.70464", "-t", "GP", "-q")
    keys = r.out.split()
    assert r.code == 0 and len(keys) == 2
    p = json.loads(run("show", keys[0], "--json").out)
    assert (p["status"], p["tags"], p["source"]) == ("read", ["gp"], "feed:xue") and p["read_on"]
    assert "## Notes" in (filled / p["path"]).read_text()
    assert git_log(filled)[0] == f"ruru: keep {keys[0]} {keys[1]} (tags: gp)"
    r = run("later", "doi-10.1002-jat.70468")
    assert r.out.startswith("later doi-10.1002-jat.70468 -> ")
    assert json.loads(run("list", "-s", "later", "--json").out)[0]["source"] == "feed:jat"
    assert run("skip", "doi-10.1002-jat.70478").out == "skip doi-10.1002-jat.70478\n"
    assert len(run("inbox", "-q").out.split()) == 5
    seen = inbox.read_seen(filled)
    assert [(r["decision"], bool(r["key"])) for r in seen] == [("keep", True), ("keep", True), ("later", True),
                                                               ("skip", False)]
    r = run("show", "doi-10.1002-jat.70478")
    assert r.code == cli.USAGE and "triaged on" in r.err and ": skip" in r.err
    assert f"-> {keys[0]}" in run("show", "doi-10.1145-3847110").err


def test_triage_errors(filled, run):
    assert run("keep", "doi-10.1145-3847110", "nope").code == cli.USAGE
    assert "doi-10.1145-3847110" in run("inbox", "-q").out  # nothing happened
    assert run("keep", "doi-10.1145-3847110", "-p", "unknown").code == cli.USAGE
    assert run("show", "doi-10.1145-3847110", "--bib").code == cli.USAGE
    assert run("show", "doi-10.1145-3847110", "-q").out == "doi-10.1145-3847110\n"


def test_keep_item_already_in_library_adds_labels(filled, run, net):
    item = inbox.Inbox(filled).get("doi-10.1145-3847110")
    run("add", "--title", item.paper.title, "--year", str(item.paper.year), "-q")
    key = run("list", "-q").out.split()[0]
    assert run("keep", "doi-10.1145-3847110", "-t", "dup", "-q").out == f"{key}\n"
    assert json.loads(run("show", key, "--json").out)["tags"] == ["dup"]
    assert len(run("list", "-q").out.split()) == 1


def test_find(lib, run, net):
    r = run("find", "retention", "time", "alignment", "gc-ms", "-n", "5")
    assert r.code == 0 and "GC/MS data reduction using retention time alignment" in r.out
    assert "doi:10.1186/1471-2105-13-214" in r.out
    ids = run("find", "retention time alignment gc-ms", "-q").out.split()
    assert len(ids) == 5 and ids[1] == "doi:10.1186/1471-2105-13-214"
    run("add", "--title", "Combining peak- and chromatogram-based retention time alignment algorithms for "
        "multiple chromatography-mass spectrometry datasets", "--year", "2012", "-q")
    rows = json.loads(run("find", "retention time alignment gc-ms", "--json").out)
    assert rows[1]["in_library"] and rows[0]["in_library"] is None
    assert set(rows[0]) >= {"id", "title", "authors", "year", "ids", "in_library"} and "key" not in rows[0]
    assert "[in library:" in run("find", "x").out
    net.routes.insert(0, Router().add(r"works\?search=", body='{"results": []}').routes[0])
    assert run("find", "nothing").code == cli.NO_MATCH


def test_end_to_end(tmp_path, run, net, monkeypatch):
    """init → feeds add → fetch → inbox --json → suggest → keep → cite (check --fix) → check, on git."""
    root = tmp_path / "e2e"
    monkeypatch.setenv("RURU_LIBRARY", str(root))
    assert run("init", "--email", "test@example.invalid").code == 0
    assert run("feeds", "add", "jat", *FEEDS["jat"]).code == 0
    assert run("fetch", "--since", SINCE).code == 0
    first = json.loads(run("inbox", "--json").out)[0]
    assert run("suggest", first["id"], "keep", "--reason", "toxicology methods").code == 0
    key = run("keep", first["id"], "-t", "tox", "-q").out.strip()
    assert run("show", key, "--bib").out.startswith(f"@article{{{key},")
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(f"As in \\cite{{{key}}}.\n")
    r = run("project", "add", "tox-review", "--path", str(paper), "--bib", "refs.bib", "--tex", "main.tex")
    assert r.code == 0
    assert run("check", "tox-review").code == cli.NO_MATCH  # cited, not yet in refs.bib
    assert run("check", "tox-review", "--fix").code == cli.OK
    assert f"@article{{{key}," in (paper / "refs.bib").read_text()
    assert git_log(root) == [f"ruru: cite tox-review {key}", "ruru: project add tox-review",
                             f"ruru: keep {key} (tags: tox)", f"ruru: suggest keep {first['id']} (agent)",
                             "ruru: fetch 3 new (jat 3)", "ruru: feeds add jat", "ruru: init"]
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True,
                            text=True).stdout
    assert status == ""  # every change committed; .ruru/ ignored


# --- wizard --------------------------------------------------------------------------------------

def scripted(*answers):
    queue = list(answers)

    def ask(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)
    return ask


def run_wizard(net, *answers, existing=()):
    added: list[Feed] = []
    said: list[str] = []
    Wizard(http.Client(), list(existing), added.append, ask=scripted(*answers), say=said.append).run()
    return added, "\n".join(said)


def test_wizard_journal(net):
    added, said = run_wizard(net, "2", "analytical chemistry", "9", "1", "", "", "q")
    assert "Analytical Chemistry  (ISSN 0003-2700, 140,854 works" in said
    assert "enter a number from 1 to 8" in said
    assert added == [Feed(name="analytical-chemistry", kind="crossref", issn="0003-2700",
                          title="Analytical Chemistry")]


def test_wizard_journal_without_issn(net):
    net.routes.insert(0, Router().add(r"sources\?", body=json.dumps({"results": [
        {"id": "https://openalex.org/S1", "display_name": "Tiny Journal", "issn_l": None, "type": "journal"}]})
    ).routes[0])
    added, _ = run_wizard(net, "2", "tiny", "1", "tiny", "y")
    assert added[0].kind == "openalex" and added[0].filter == "primary_location.source.id:S1"


def test_wizard_authors_two_people(net):
    added, said = run_wizard(net, "3", "Bing Xue", "1", "Bing Xue", "x", "2", "", "supervisors", "y", "q")
    assert "Bing Xue  (845 works; Beijing University of Posts and Telecommunications, Victoria University" in said
    assert added[0].name == "supervisors"
    assert added[0].filter == "authorships.author.id:A5077569089|A5080779869"


def test_wizard_single_author_and_name_clash(net):
    existing = [Feed(name="author-xue", kind="rss", url="u")]
    added, said = run_wizard(net, "3", "Bing Xue", "1", "", "author-xue", "", "", "q", existing=existing)
    assert "already a feed named 'author-xue'" in said
    assert added[0].name == "author-xue-2" and added[0].title == "Bing Xue"


def test_wizard_arxiv(net):
    added, said = run_wizard(net, "1", "1, q-bio.QM, bad cat!", "chromatography, mass spectrometry", "", "", "q")
    assert "skipping 'bad cat!'" in said
    assert added[0].query == ('(cat:physics.chem-ph OR cat:q-bio.QM) AND '
                              '(abs:chromatography OR abs:"mass spectrometry")')
    assert added[0].name == "arxiv-chem-ph-chromatography"
    added, _ = run_wizard(net, "1", "4", "", "", "")
    assert added[0].query == "cat:cs.LG" and added[0].name == "arxiv-lg"
    added, _ = run_wizard(net, "1", "4", "GC-MS", "", "")
    assert added[0].query == "cat:cs.LG AND abs:GC-MS"
    assert run_wizard(net, "1", "")[0] == []


def test_wizard_topic_rss_list_and_declines(net):
    added, said = run_wizard(net, "l", "4", "retention time alignment", "", "n", "5", "ftp://x", "5",
                             "https://www.example.org/feed.xml", "", "", "7", "q")
    assert "no feeds yet" in said and "not an http(s) URL" in said and "pick 1-5, l or q" in said
    assert [f.name for f in added] == ["example-org-feed-xml"]
    assert added[0].url == "https://www.example.org/feed.xml"
    assert "Done. Run `ruru fetch`" in said


def test_wizard_handles_errors_and_empty_results(net):
    net.routes.insert(0, Router().add(r"authors\?", body='{"results": []}').routes[0])
    net.routes.insert(0, Router().add(r"sources\?", status=500).routes[0])
    added, said = run_wizard(net, "3", "Nobody", "", "2", "x", "2", "", "q")
    assert "nobody found" in said and "error: api.openalex.org: HTTP 500" in said and added == []


def test_wizard_command(lib, run, net, monkeypatch):
    assert "needs a terminal" in run("feeds", "wizard").err
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", scripted("4", "fish oil authentication", "", "", "q"))
    assert run("feeds", "wizard").code == 0
    assert run("feeds", "-q").out == "fish-oil-authentication\n"
    assert git_log(lib)[0] == "ruru: feeds add fish-oil-authentication"


def test_slug():
    assert slug("Analytical Chemistry: A Journal!") == "analytical-chemistry-a-journal"
    assert slug("!!!") == "feed"


def test_agent_skip_is_attributed_and_listed(filled, run, monkeypatch):
    ids = run("inbox", "-q").out.split()
    run("suggest", ids[0], "skip", "--reason", "biosensor; off topic", "--by", "claude")
    monkeypatch.setenv("RURU_AGENT", "claude")
    r = run("skip", ids[0], ids[1], "--reason", "explicit reason")
    assert r.code == 0
    assert git_log(filled)[0] == f"ruru: skip {ids[0]} {ids[1]} (by claude)"
    monkeypatch.delenv("RURU_AGENT")
    run("skip", ids[2])  # Jesse, by hand
    # An agent applying its own suggestion inherits the suggestion's reason.
    run("suggest", ids[3], "skip", "--reason", "off topic: food processing", "--by", "claude")
    run("skip", ids[3], "--by", "claude")
    rows = json.loads(run("seen", "--json").out)
    assert [r["id"] for r in rows] == [ids[3], ids[2], ids[1], ids[0]]  # newest first
    assert rows[0]["by"] == "claude" and rows[0]["reason"] == "off topic: food processing"
    assert "by" not in rows[1] and "reason" not in rows[1]
    assert rows[2]["reason"] == "explicit reason"
    assert run("seen", "--by", "claude", "-q").out.split() == [ids[3], ids[1], ids[0]]
    assert run("seen", "-d", "keep").code == cli.NO_MATCH
    assert run("seen", "--since", "2999-01-01", "--json").out.strip() == "[]"
    assert len(run("seen", "-n", "2", "-q").out.split()) == 2
    assert "(claude: off topic: food processing)" in run("seen").out


def test_unskip_restores_from_git(filled, run, net):
    ids = run("inbox", "-q").out.split()
    original = (filled / inbox.Inbox(filled).get(ids[0]).path.relative_to(filled)).read_text()
    run("skip", ids[0], ids[1], "--by", "claude", "--reason", "off topic")
    run("keep", ids[2])
    assert ids[0] not in run("inbox", "-q").out
    r = run("unskip", ids[0])
    assert r.code == 0 and r.out.startswith(f"restored {ids[0]}: ")
    restored = inbox.Inbox(filled).get(ids[0])
    assert restored.path.read_text() == original
    assert git_log(filled)[0] == f"ruru: unskip {ids[0]}"
    seen = [s["id"] for s in inbox.read_seen(filled)]
    assert ids[0] not in seen and ids[1] in seen  # only the undone skip is forgotten
    assert run("unskip", ids[0]).code == cli.USAGE  # already back in the inbox
    r = run("unskip", ids[2])
    assert r.code == cli.USAGE and "was not skipped: keep" in r.err
    assert "no skipped inbox item" in run("unskip", "doi-10.9999-never").err
    assert run("unskip", ids[1], "-q").out == f"{ids[1]}\n"


def test_unskip_needs_git(tmp_path, run):
    root = tmp_path / "plain"
    run("init", str(root))
    import shutil
    shutil.rmtree(root / ".git")
    r = run("-L", str(root), "unskip", "x")
    assert r.code == cli.USAGE and "not a git repository" in r.err
