import tomllib
from datetime import date

import pytest

from httpfix import FEEDS
from ruru import feeds, inbox, tomledit
from ruru.errors import UsageError
from ruru.http import Client, HttpError
from ruru.inbox import Deduper, Item
from ruru.model import Paper


def _paper(title="Joint alignment", year=2026, **ids):
    p = Paper(title=title, year=year, authors=["Jesse Wood"], source="feed:x")
    p.ids.update(ids)
    return p


@pytest.mark.parametrize(("ids", "fallback", "expected"), [
    ({"arxiv": "2610.01234v2", "doi": "10.1/x"}, "", "arxiv-2610.01234"),
    ({"arxiv": "hep-th/9901001"}, "", "arxiv-hep-th_9901001"),
    ({"doi": "10.1021/ACS.AnalChem.6b01234"}, "", "doi-10.1021-acs.analchem.6b01234"),
    ({"openalex": "https://openalex.org/W42"}, "", "oa-W42"),
    ({}, "https://www.sciencedirect.com/science/article/pii/S0021967326008472?dgcid=rss", "rss-s0021967326008472"),
])
def test_item_id(ids, fallback, expected):
    assert inbox.item_id(_paper(**ids), fallback) == expected


def test_item_id_hash_fallback():
    a = inbox.item_id(_paper(), "https://example.org/feed/latest")
    assert a.startswith("rss-") and len(a) == 14 and a == inbox.item_id(_paper(), "https://example.org/feed/latest")


def test_item_round_trip():
    item = Item("doi-10.1-x", _paper(doi="10.1/x"), fetched=date(2026, 10, 17), published=date(2026, 10, 1),
                suggestion={"action": "keep", "reason": "relevant", "by": "claude", "date": date(2026, 10, 17)})
    text = inbox.dumps(item)
    assert text.startswith("---\nid: doi-10.1-x\ntitle: Joint alignment\n") and text.endswith("---\n")
    for absent in ("key:", "status:", "tags:", "## Notes"):
        assert absent not in text
    back = inbox.loads(text)
    assert inbox.dumps(back) == text
    assert (back.id, back.fetched, back.published, back.suggestion) == (item.id, item.fetched, item.published,
                                                                        item.suggestion)
    assert back.paper.front_matter() == item.paper.front_matter() and back.paper.body == ""


@pytest.mark.parametrize("text", ["nope", "---\ntitle: x\n---\n", "---\n- 1\n---\n"])
def test_item_loads_rejects(text):
    with pytest.raises(ValueError):
        inbox.loads(text)


def test_inbox_listing_and_get(tmp_path, capsys):
    box = inbox.Inbox(tmp_path)
    assert box.items() == [] and not box.exists("x")
    with pytest.raises(inbox.UnknownItem):
        box.get("x")
    older = Item("a", _paper("A"), fetched=date(2026, 10, 1))
    newer = Item("b", _paper("B"), fetched=date(2026, 10, 2), published=date(2026, 1, 1))
    for item in (older, newer):
        path = box.path_for(item)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inbox.dumps(item))
    (tmp_path / "inbox" / "2026-10-02" / "broken.md").write_text("garbage")
    assert [i.id for i in box.items()] == ["b", "a"]
    assert "skipping inbox/2026-10-02/broken.md" in capsys.readouterr().err
    assert box.get("a").paper.title == "A" and box.exists("b")


def test_seen_round_trip(tmp_path, capsys):
    assert inbox.read_seen(tmp_path) == []
    item = Item("doi-10.1-x", _paper(doi="10.1/x"), fetched=date(2026, 10, 17))
    (tmp_path / "seen.jsonl").write_text('{"id": "old"}\nnot json\n\n{"id": "no-newline"}')
    text = inbox.append_seen(tmp_path, [inbox.seen_record(item, "skip")])
    (tmp_path / "seen.jsonl").write_text(text)
    records = inbox.read_seen(tmp_path)
    assert [r["id"] for r in records] == ["old", "no-newline", "doi-10.1-x"]
    assert records[-1]["ids"] == {"doi": "10.1/x"} and records[-1]["decision"] == "skip"
    assert "line 2 is not JSON" in capsys.readouterr().err
    assert inbox.append_seen(tmp_path / "missing", []) == ""


def test_deduper():
    d = Deduper.everything(
        [_paper("In the library", doi="10.1/lib")],
        [Item("i", _paper("In the inbox", arxiv="2610.00001"), fetched=date(2026, 1, 1))],
        [{"ids": {"openalex": "W9"}, "title": "Skipped once", "year": 2025}, {"title": "", "ids": {}}],
    )
    assert d.seen(_paper("Other", doi="https://doi.org/10.1/LIB"))
    assert d.seen(_paper("Other", arxiv="2610.00001v3"))
    assert d.seen(_paper("Other", openalex="https://openalex.org/W9"))
    assert d.seen(_paper("skipped  once!", 2026)) and not d.seen(_paper("Skipped once", 2027))
    assert d.seen(_paper("In the library", None))
    assert not d.seen(_paper("Brand new"))


# --- ruru.toml editing ---------------------------------------------------------------------------

TOML = """\
# my comment
contact_email = "x@example.org"

[[feeds]]
name = "one"
kind = "rss"
url = "https://a.example/feed"

# a comment about two
[[feeds]]
name = "two"
kind = "arxiv"
query = "cat:cs.LG"

[projects.p]
path = "~/p"
"""


def test_tomledit_append_and_remove_preserve_comments():
    text = tomledit.append_table(TOML, "[[feeds]]", {"name": "three", "kind": "openalex", "search": 'say "hi"',
                                                     "flag": True, "n": 3, "list": ["a"], "none": None})
    data = tomllib.loads(text)
    assert [f["name"] for f in data["feeds"]] == ["one", "two", "three"]
    assert data["feeds"][2]["search"] == 'say "hi"' and "none" not in data["feeds"][2]
    assert text.startswith("# my comment\n")
    removed = tomledit.remove_table(text, "[[feeds]]", "two")
    data = tomllib.loads(removed)
    assert [f["name"] for f in data["feeds"]] == ["one", "three"] and data["projects"]["p"]["path"] == "~/p"
    assert "# a comment about two" in removed  # comments above a table belong to the previous lines
    assert tomllib.loads(tomledit.remove_table(TOML, "[projects.p]"))["feeds"][1]["name"] == "two"
    with pytest.raises(KeyError):
        tomledit.remove_table(TOML, "[[feeds]]", "nope")


# --- feeds ---------------------------------------------------------------------------------------

def test_feed_validation():
    assert feeds.Feed.from_dict({"name": "a", "kind": "rss", "url": "https://x"}).url == "https://x"
    for bad in ({"name": "A B", "kind": "rss", "url": "u"}, {"name": "a", "kind": "smoke"},
                {"name": "a", "kind": "arxiv"}, {"name": "a", "kind": "openalex"},
                {"name": "a", "kind": "rss", "url": "u", "colour": "red"}):
        with pytest.raises(UsageError):
            feeds.Feed.from_dict(bad)
    with pytest.raises(UsageError, match="duplicate"):
        feeds.load([{"name": "a", "kind": "rss", "url": "u"}] * 2)


def test_feed_describe():
    assert feeds.Feed("x", "openalex", filter="f", search="s").describe() == 'openalex: f + "s"'
    assert feeds.Feed("x", "crossref", issn="1", title="J").describe() == "crossref: 1  (J)"


def test_since_for():
    feed = feeds.Feed("a", "rss", url="u")
    today = date(2026, 10, 17)
    assert feeds.since_for(feed, {}, today) == date(2026, 10, 10)
    assert feeds.since_for(feed, {"a": {"last_fetch": "2026-10-15"}}, today) == date(2026, 10, 14)
    assert feeds.since_for(feed, {"a": {"last_fetch": "bad"}}, today) == date(2026, 10, 10)


def test_load_state_tolerates_garbage(tmp_path):
    assert feeds.load_state(tmp_path) == {}
    feeds.state_path(tmp_path).parent.mkdir()
    feeds.state_path(tmp_path).write_text("[1, 2]")
    assert feeds.load_state(tmp_path) == {}


def _feed(name):
    args = FEEDS[name]
    kw = {args[i].lstrip("-"): args[i + 1] for i in range(0, len(args), 2)}
    return feeds.Feed(name=name, **kw)


def test_fetch_arxiv_stops_at_since(net):
    got = feeds.fetch(Client(), _feed("arxiv-chem"), date(2026, 1, 1))
    assert got and all(c.published >= date(2026, 1, 1) for c in got)
    assert all(c.paper.type == "preprint" and not c.needs_resolve for c in got)
    assert len([u for u in net.urls() if "search_query" in u]) == 1


def test_fetch_arxiv_pages(monkeypatch):
    pages = [[(_paper(f"P{i}"), date(2026, 10, 1)) for i in range(2)], [(_paper("Q"), date(2026, 9, 1))]]
    calls = []

    def query(client, q, *, start, max_results):
        calls.append(start)
        return pages[len(calls) - 1]

    monkeypatch.setattr(feeds.arxiv, "query", query)
    got = feeds.fetch(Client(), _feed("arxiv-chem"), date(2026, 9, 15), limit=2)
    assert [c.paper.title for c in got] == ["P0", "P1"] and calls == [0]
    got = feeds._arxiv(Client(), _feed("arxiv-chem"), date(2026, 9, 15), 4)
    assert calls[1:] == [0]  # a short page ends the paging


def test_fetch_crossref_journal(net):
    got = feeds.fetch(Client(), _feed("anal-chem"), date(2026, 10, 12))
    assert len(got) == 5 and all(c.paper.venue == "Analytical Chemistry" for c in got)
    assert got[0].paper.ids["doi"] == "10.1021/acs.analchem.6c03760"
    assert "from-created-date%3A2026-10-12" in net.urls()[-1]


def test_fetch_openalex(net):
    got = feeds.fetch(Client(), _feed("xue"), date(2026, 9, 1))
    assert [c.paper.ids["doi"] for c in got] == ["10.1145/3847110"] and got[0].published == date(2026, 9, 30)
    assert "from_publication_date%3A2026-09-01" in net.urls()[-1]
    got = feeds.fetch(Client(), _feed("rt-alignment"), date(2026, 1, 1))
    assert len(got) == 5
    assert "title_and_abstract.search%3A%22retention+time+alignment%22" in net.urls()[-1]


def test_openalex_feed_cursor_paging():
    from httpfix import Router
    from ruru.resolve import openalex
    work = '{"id": "W%d", "title": "T%d", "type": "article", "publication_date": "2026-10-0%d"}'
    page1 = '{"meta": {"next_cursor": "abc"}, "results": [%s]}' % (work % (1, 1, 1))
    page2 = '{"meta": {"next_cursor": null}, "results": [%s]}' % (work % (2, 2, 2))
    router = Router().add("cursor=%2A", body=page1).add("cursor=abc", body=page2)
    got = openalex.works_feed(Client(transport=router), since=date(2026, 1, 1), search='"exact"', limit=5)
    assert [p.title for p, _ in got] == ["T1", "T2"]
    assert "title_and_abstract.search%3A%22exact%22" in router.urls()[0]


def test_crossref_cursor_paging():
    from httpfix import Router
    from ruru.resolve import crossref
    item = ('{"DOI": "10.1/%d", "title": ["T%d"], "type": "journal-article", '
            '"created": {"date-parts": [[2026, 10]]}}')
    page = '{"message": {"next-cursor": "n%d", "items": [%s]}}'
    router = (Router().add("cursor=%2A", body=page % (1, item % (1, 1)))
              .add("cursor=n1", body=page % (2, item % (2, 2))))
    got = crossref.journal_feed(Client(transport=router), "1234-5678", since=date(2026, 1, 1), limit=2,
                                page_size=1)
    assert [p.title for p, _ in got] == ["T1", "T2"] and got[0][1] == date(2026, 10, 1)


def test_fetch_rss_with_dois_needs_resolving(net):
    got = feeds.fetch(Client(), _feed("jat"), date(2026, 10, 1))
    assert [c.paper.ids["doi"] for c in got] == ["10.1002/jat.70464", "10.1002/jat.70468", "10.1002/jat.70478"]
    assert all(c.needs_resolve for c in got)
    assert got[0].paper.authors[:2] == ["Bazila Rehman", "Tariq Ahmad"]
    # 'Fri, 16 Oct 2026 22:15:44 -0700' is the 17th in UTC, which is what feedparser gives.
    assert got[0].paper.venue == "Journal of Applied Toxicology" and got[0].published == date(2026, 10, 17)
    assert feeds.fetch(Client(), _feed("jat"), date(2026, 10, 18)) == []


def test_fetch_rss_sciencedirect_without_dois(net):
    got = feeds.fetch(Client(), _feed("jca"), date(2026, 1, 1))
    assert len(got) == 3 and not any(c.needs_resolve for c in got)
    p = got[0].paper
    assert p.venue == "Journal of Chromatography A" and len(p.authors) > 1 and p.year == 2026
    assert p.url.startswith("https://www.sciencedirect.com/science/article/pii/") and "?" not in p.url
    assert got[0].published and got[0].published.year == 2026


SINCE = date(2026, 1, 1)


def test_fetch_rss_rejects_non_feeds(net):
    from httpfix import Router
    router = Router().add("blocked", status=403, body="<html>Just a moment...</html>").add("page", body="<html>hi")
    with pytest.raises(HttpError, match="403"):
        feeds.fetch(Client(transport=router), feeds.Feed("b", "rss", url="https://x.example/blocked"), SINCE)
    with pytest.raises(HttpError, match="not an RSS or Atom feed"):
        feeds.fetch(Client(transport=router), feeds.Feed("p", "rss", url="https://x.example/page"), SINCE)


def test_rss_entry_helpers():
    assert feeds._entry_doi({"link": "https://pubs.acs.org/doi/10.1021/acs.analchem.6b00001?af=R"}) == \
        "10.1021/acs.analchem.6b00001"
    assert feeds._entry_doi({"id": "tag:x"}) is None
    assert feeds._entry_date({"summary": "<p>Publication date: 31 Smarch 2026</p>"}) is None
    assert feeds._entry_date({}) is None
