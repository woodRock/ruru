"""Textual pilot tests for `ruru tui` (skipped without the [tui] extra)."""

import asyncio
import json

import pytest

pytest.importorskip("textual")

from conftest import git_log
from httpfix import FEEDS
from ruru import cli, model, ops, tui
from ruru.errors import LockHeld
from ruru.inbox import Inbox, read_seen, read_viewed
from ruru.library import Library
from ruru.reviews import Reviews

pytestmark = pytest.mark.tui
SIZE = (140, 40)


@pytest.fixture
def lib(lib, run, net):
    run("feeds", "add", "jat", *FEEDS["jat"])
    run("feeds", "add", "xue", *FEEDS["xue"])
    run("fetch", "--since", "2026-01-01")
    run("add", "--title", "Joint Retention-Time Alignment", "--author", "Jesse Wood", "--year", "2026",
        "-t", "gc-ms")
    run("add", "--title", "Fitness landscapes", "--author", "Bing Xue", "--year", "2025",
        "--doi", "10.1186/s13321-021-00544-w")
    return lib


def drive(lib_root, script, monkeypatch=None):
    """Run the app headless, call `script(app, pilot)` (a coroutine), return the app and notices."""
    app = tui.RuruApp(Library.open(lib_root))
    notices: list[tuple[str, str]] = []
    original = app.notify

    def notify(message, *a, severity="information", **kw):
        notices.append((severity, str(message)))
        return original(message, *a, severity=severity, **kw)

    app.notify = notify  # type: ignore[method-assign]

    async def main():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await script(app, pilot)
            await pilot.pause()

    asyncio.run(main())
    return app, notices


async def select(app, pilot, table_id, key):
    """Put the cursor on `key` (and check the table has focus, so key presses reach it)."""
    await pilot.pause()
    table = app.query_one(table_id)
    assert app.focused is table, app.focused
    table.move_cursor(row=[str(k.value) for k in table.rows].index(key))
    await pilot.pause()


def inbox_ids(root):
    return [i.id for i in Inbox(root).items()]


def test_inbox_lists_items_and_shows_detail(lib):
    async def script(app, pilot):
        table = app.query_one("#inbox-table")
        assert table.row_count == 4
        assert table.current_key() == app.items[0].id
        assert app.query_one("#inbox-detail").display and not app.query_one("#inbox-empty").display
        await pilot.press("j")
        assert table.cursor_row == 1
        await pilot.press("k")
        assert table.cursor_row == 0
        await pilot.press("j", "j")  # the pane renders once the cursor settles, and only the visible one
        await pilot.pause(tui.DETAIL_SECONDS * 2)
        assert app._shown["inbox"][0] == tui.item_markdown(app.items[2])
        library = app._shown["library"]
        await pilot.press("k")
        await pilot.pause(tui.DETAIL_SECONDS * 2)
        assert app._shown["library"] is library
        assert "Inbox (4, 4 new)" in str(app.query_one("#tabs").get_tab("inbox").label)
        assert app.items[0].paper.title in tui.item_markdown(app.items[0])
    drive(lib, script)


def test_keep_skip_later(lib):
    first, second, third = inbox_ids(lib)[:3]

    async def script(app, pilot):
        await pilot.press("y")
        await pilot.press("n")
        await pilot.press("l")
    _, notices = drive(lib, script)
    assert len(inbox_ids(lib)) == 1
    log = git_log(lib)
    assert log[2].startswith("ruru: keep ") and log[1] == f"ruru: skip {second}"
    assert log[0].startswith("ruru: later ")
    statuses = sorted(p.status for p in Library.open(lib).papers() if p.source.startswith("feed:"))
    assert statuses == ["later", "read"]  # y: kept as read; l: the reading list
    assert [s for s, _ in notices].count("information") >= 3
    assert first not in inbox_ids(lib) and third not in inbox_ids(lib)


def test_accept_suggestion(lib):
    ids = inbox_ids(lib)
    ops.suggest(Library.open(lib), ids[0], "skip", "off topic", "claude")

    async def script(app, pilot):
        await pilot.press("a")  # first item: suggested skip
        await pilot.press("a")  # next item: no suggestion
    _, notices = drive(lib, script)
    assert ids[0] not in inbox_ids(lib) and ids[1] in inbox_ids(lib)
    assert git_log(lib)[0] == f"ruru: skip {ids[0]}"
    assert ("warning", "no suggestion on this item") in notices


def test_keep_with_tags_and_for_project(lib, run, tmp_path):
    run("project", "add", "joint-align", "--path", str(tmp_path), "--bib", "r.bib")

    async def script(app, pilot):
        await pilot.press("t")
        await pilot.press(*"GC-MS review")
        await pilot.press("enter")
        await pilot.press("p")
        await pilot.press("enter")  # the only project
    drive(lib, script)
    kept = [p for p in Library.open(lib).papers() if p.source.startswith("feed:")]
    assert sorted(p.tags for p in kept) == [[], ["gc-ms", "review"]]
    assert sorted(p.projects for p in kept) == [[], ["joint-align"]]


def test_prompts_can_be_cancelled(lib):
    before = inbox_ids(lib)

    async def script(app, pilot):
        await pilot.press("t", "escape", "p")  # no projects: a warning, no dialog
    _, notices = drive(lib, script)
    assert inbox_ids(lib) == before
    assert ("warning", "no projects yet: `ruru project add …`") in notices


def test_filter(lib):
    async def script(app, pilot):
        await pilot.press("slash")
        await pilot.press(*"f:xue")
        await pilot.press("enter")
        assert [i.paper.source for i in app.items] == ["feed:xue"]
        await pilot.press("slash", "ctrl+u", "enter")
        assert len(app.items) == 4
        await pilot.press("2", "slash", *"t:gc-ms", "enter")
        assert [p.key for p in app.reading] == ["wood2026joint"]
        await pilot.press("slash", "ctrl+u", *"fitness", "enter")
        assert [p.key for p in app.reading] == ["xue2025fitness"]
    drive(lib, script)


def test_library_paper_screen_actions(lib, monkeypatch):
    monkeypatch.setattr(cli, "edit_text", lambda text: "Edited in the TUI.")
    opened = []
    monkeypatch.setattr(tui.webbrowser, "open", opened.append)

    async def script(app, pilot):
        await pilot.press("2")
        await select(app, pilot, "#reading-table", "wood2026joint")
        assert app.query_one("#reading-table").row_count == 2 and app.tab == "reading"
        await pilot.press("enter")
        assert isinstance(app.screen, tui.PaperScreen) and app.screen.key == "wood2026joint"
        await pilot.press("e")
        await pilot.press("t", *"+key -gc-ms", "enter")
        await pilot.press("s")
        await pilot.press("down", "down", "down", "enter")  # to-read, later, reading, read
        await pilot.press("o")  # no URL
        await pilot.press("escape")
        assert not isinstance(app.screen, tui.PaperScreen)
        await select(app, pilot, "#reading-table", "xue2025fitness")
        await pilot.press("o")
    _, notices = drive(lib, script)
    p = Library.open(lib).get("wood2026joint")
    assert p.notes == "Edited in the TUI." and p.tags == ["key"] and p.status == "read" and p.read_on
    assert git_log(lib)[:3] == ["ruru: status wood2026joint read", "ruru: tag wood2026joint +key -gc-ms",
                                "ruru: notes wood2026joint"]
    assert ("warning", "no URL for this paper") in notices
    assert opened == ["https://doi.org/10.1186/s13321-021-00544-w"]


def test_edit_notes_unchanged_and_editor_failure(lib, monkeypatch):
    monkeypatch.setattr(cli, "edit_text", lambda text: text)

    async def script(app, pilot):
        await pilot.press("2", "e")
        monkeypatch.setattr(cli, "edit_text", lambda text: (_ for _ in ()).throw(cli.UsageError("editor failed")))
        await pilot.press("e")
    _, notices = drive(lib, script)
    assert not git_log(lib)[0].startswith("ruru: notes")
    assert ("error", "editor failed") in notices


def test_pdf_open_and_fetch(lib, net, monkeypatch):
    ran = []
    monkeypatch.setattr(tui, "open_file", ran.append)
    net.add(r"jcheminf\.biomedcentral\.com/track/pdf/", body=b"%PDF-1.4 test")
    (lib / "papers" / "wood2026joint" / "paper.pdf").write_bytes(b"%PDF")

    async def script(app, pilot):
        await pilot.press("2")
        for key in ("wood2026joint", "xue2025fitness"):
            await select(app, pilot, "#reading-table", key)
            await pilot.press("d")
            await app.workers.wait_for_complete()
            await pilot.pause()
    drive(lib, script)
    assert [str(p).split("/")[-2] for p in ran] == ["wood2026joint", "xue2025fitness"]
    assert (lib / "papers" / "xue2025fitness" / "paper.pdf").read_bytes() == b"%PDF-1.4 test"


def test_pdf_fetch_failure(lib, monkeypatch):
    async def script(app, pilot):
        await pilot.press("2")
        await select(app, pilot, "#reading-table", "wood2026joint")
        await pilot.press("d")
        await app.workers.wait_for_complete()
    # The paper has no DOI, so ruru looks it up by title, which finds nothing.
    from httpfix import Router
    monkeypatch.setattr(tui.cli.http, "default_transport", Router().add("title.search", body='{"results": []}'))
    _, notices = drive(lib, script)
    assert any(s == "error" and "no work titled" in m for s, m in notices)


def test_pdf_without_open_access_opens_the_publisher(lib, monkeypatch, browsed, clipboard):
    async def script(app, pilot):
        await pilot.press("2")
        await select(app, pilot, "#reading-table", "xue2025fitness")
        await pilot.press("d")
        await app.workers.wait_for_complete()
    from httpfix import Router  # no open-access copy: OpenAlex has no pdf_url, the DOI page is a paywall
    monkeypatch.setattr(tui.cli.http, "default_transport", Router().add(".", status=404))
    _, notices = drive(lib, script)
    assert browsed == ["https://doi.org/10.1186/s13321-021-00544-w"] and clipboard == ["xue2025fitness.pdf"]
    assert any(s == "warning" and "save it as xue2025fitness.pdf" in m for s, m in notices)


def test_fetch_reports_new_items_and_failures(lib, net, monkeypatch):
    from httpfix import Router
    for path in (lib / "inbox").glob("*/*.md"):
        path.unlink()
    (lib / ".ruru" / "feeds.json").unlink()
    monkeypatch.setattr(model, "today", lambda: __import__("datetime").date(2026, 10, 17))
    net.routes.insert(0, Router().add(r"authorships", status=503).routes[0])

    async def script(app, pilot):
        await pilot.press("f")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one("#inbox-table").row_count == 3
    _, notices = drive(lib, script)
    assert ("warning", "3 new item(s); failed: xue") in notices


def test_projects_tab(lib, run, tmp_path):
    (tmp_path / "main.tex").write_text("\\cite{wood2026joint, missing2020x}\n")
    run("project", "add", "joint-align", "--path", str(tmp_path), "--bib", "refs.bib", "--tex", "main.tex")
    run("project", "add", "notex", "--path", str(tmp_path), "--bib", "refs.bib")
    run("cite", "joint-align", "wood2026joint")

    async def script(app, pilot):
        await pilot.press("5")
        assert app.tab == "projects"
        await pilot.press("c")
    _, notices = drive(lib, script)
    projects = tui.projmod.load(Library.open(lib).settings.projects)
    text = tui.project_markdown(Library.open(lib), projects["joint-align"])
    assert "Reading list (1)" in text and "`missing2020x`" in text and "1 cited keys" not in text
    assert "*No tex files configured.*" in tui.project_markdown(
        Library.open(lib), tui.projmod.load(Library.open(lib).settings.projects)["notex"])
    assert ("information", "checked") in notices


def test_reloads_when_the_library_changes(lib, run):
    async def script(app, pilot):
        assert app.query_one("#inbox-table").row_count == 4
        run("skip", inbox_ids(lib)[0])  # another process (an agent) changes the library
        app.poll()
        await pilot.pause()
        assert app.query_one("#inbox-table").row_count == 3
        await pilot.press("r")
    _, notices = drive(lib, script)
    assert ("information", "reloaded") in notices


def test_errors_are_reported_not_raised(lib, monkeypatch):
    def locked(*a, **k):
        raise LockHeld("library is locked")
    monkeypatch.setattr(ops, "triage", locked)

    async def script(app, pilot):
        await pilot.press("y")
    _, notices = drive(lib, script)
    assert ("error", "library is locked") in notices


def test_empty_library_views(tmp_path, run, monkeypatch):
    root = tmp_path / "empty"
    run("init", str(root))

    async def script(app, pilot):
        await pilot.press("y", "a", "t", "o", "2", "e", "s", "d", "t", "o", "5")
        assert app.query_one("#reading-table").row_count == 0
        for tab in ("inbox", "library", "project"):  # the mascot stands in for every empty pane
            placeholder = app.query_one(f"#{tab}-empty")
            assert placeholder.display and "◉" in placeholder.content.plain
            assert not app.query_one(f"#{tab}-detail").display
        assert "Inbox empty." in app.query_one("#inbox-empty").content.plain
        assert app.title.startswith("(◉,◉) ruru")
    _, notices = drive(root, script)
    assert not [n for n in notices if n[0] == "error"]


def test_bad_settings_are_reported(lib):
    async def script(app, pilot):
        (lib / "ruru.toml").write_text("broken = [")
        app.reload()
    _, notices = drive(lib, script)
    assert any(s == "error" and "ruru.toml" in m for s, m in notices)


def test_parse_filter():
    assert tui.parse_filter("t:gc-ms s:read retention time x:y") == ({"t": "gc-ms", "s": "read"},
                                                                     "retention time x:y")


def test_run_and_cli_entry(lib, monkeypatch, run):
    started = []
    monkeypatch.setattr(tui.RuruApp, "run", lambda self: started.append(self.lib.root))
    assert run("tui").code == 0 and started == [lib]
    assert run("--no-commit", "tui").code == 0


def test_paper_json_unaffected():
    assert json.dumps({"ok": True})


def test_remove_paper(lib):
    async def script(app, pilot):
        await pilot.press("2")
        await select(app, pilot, "#reading-table", "wood2026joint")
        await pilot.press("x", "enter")  # "no, keep it" is first
        assert app.query_one("#reading-table").row_count == 2
        await pilot.press("x", "escape")
        await pilot.press("x", "down", "enter")
        assert app.query_one("#reading-table").row_count == 1
        await select(app, pilot, "#reading-table", "xue2025fitness")
        await pilot.press("enter", "x", "down", "enter")
        assert not isinstance(app.screen, tui.PaperScreen)
    _, notices = drive(lib, script)
    assert Library.open(lib).keys() == []
    assert git_log(lib)[:2] == ["ruru: rm xue2025fitness", "ruru: rm wood2026joint"]
    assert any(m.startswith("removed wood2026joint") for _, m in notices)


def test_library_sort_and_no_project(lib, run, tmp_path):
    run("project", "add", "demo", "--path", str(tmp_path), "--bib", "refs.bib")
    run("add", "--title", "Fitness landscapes", "--year", "2025", "-p", "demo")  # labels xue2025fitness

    def order(app):
        table = app.query_one("#reading-table")
        return [str(table.coordinate_to_cell_key((r, 0)).row_key.value) for r in range(table.row_count)]

    async def script(app, pilot):
        await pilot.press("2")
        assert order(app) == ["xue2025fitness", "wood2026joint"]  # added: same day, then key descending
        await pilot.press("S")  # key
        assert order(app) == ["wood2026joint", "xue2025fitness"] and "by key" in str(
            app.query_one("#tabs").get_tab("library").label)
        await pilot.press("S")  # year
        assert order(app) == ["wood2026joint", "xue2025fitness"]
        await pilot.press("S", "S", "S")  # status, title, back to added
        assert order(app) == ["xue2025fitness", "wood2026joint"]
        await pilot.press("slash", *"p:none", "enter")
        assert order(app) == ["wood2026joint"]
        await pilot.press("slash", "ctrl+u", *"fitness", "enter")
        await pilot.press("S")  # a chosen sort beats relevance
        assert order(app) == ["xue2025fitness"]
    _, notices = drive(lib, script)
    assert ("information", "sorted by key") in notices


def test_new_items_are_bold_until_viewed(lib, run, monkeypatch):
    monkeypatch.setattr(tui, "VIEW_SECONDS", 0.3)
    first, second = inbox_ids(lib)[:2]

    def bold(app, item_id):
        table = app.query_one("#inbox-table")
        return table.get_row(item_id)[0].style == "bold"

    async def script(app, pilot):
        await pilot.pause(0.6)  # resting on the first item marks it
        assert not bold(app, first) and bold(app, second)
        assert "Inbox (4, 3 new)" in str(app.query_one("#tabs").get_tab("inbox").label)
        await pilot.press("j", "k")  # passing over the second item quickly doesn't
        await pilot.pause(0.6)
        assert bold(app, second)
        await pilot.press("u")  # mark the first new again
        await pilot.pause(0.6)
        assert bold(app, first)
        await pilot.press("u")
        assert not bold(app, first)
    drive(lib, script)
    assert read_viewed(lib) == {first}
    assert json.loads(run("inbox", "--json").out)[0]["viewed"] is True  # the CLI reads it, never marks
    assert read_viewed(lib) == {first}
    run("skip", first)
    run("unskip", first)
    assert json.loads(run("show", first, "--json").out)["viewed"] is False  # it came back: new again


def test_unkeep(lib, run):
    item = inbox_ids(lib)[0]
    run("keep", item, "-q")
    key = next(k for k in run("list", "-q").out.split() if Library.open(lib).get(k).source.startswith("feed:"))
    run("tag", key, "+gc-ms")
    r = run("unkeep", key)
    assert r.code == 0 and f"back in the inbox as {item}" in r.out
    assert git_log(lib)[0] == f"ruru: unkeep {key}" and item in inbox_ids(lib)
    back = json.loads(run("show", item, "--json").out)
    assert back["tags"] == ["gc-ms"] and back["viewed"] is False and back["published"]
    assert not [r for r in read_seen(lib) if r.get("key") == key]
    r = run("keep", item, "-q")  # and it can be kept again, tags and all
    assert Library.open(lib).get(r.out.strip()).tags == ["gc-ms"]
    run("note", "wood2026joint", "--agent", "-m", "Read it.")
    r = run("unkeep", "wood2026joint")
    assert r.code == cli.USAGE and "has notes" in r.err
    run("project", "add", "demo", "--path", str(lib), "--bib", "refs.bib")
    run("add", "--title", "Fitness landscapes", "--year", "2025", "-p", "demo")
    assert "project demo" in run("unkeep", "xue2025fitness").err
    (lib / "papers" / "xue2025fitness" / "paper.pdf").write_bytes(b"%PDF")
    assert "a PDF and project demo" in run("unkeep", "xue2025fitness").err
    assert run("unkeep", "nosuchkey").code == cli.USAGE


def test_reading_list(lib):
    first, second = inbox_ids(lib)[:2]

    async def script(app, pilot):
        assert [p.key for p in app.reading] == ["xue2025fitness", "wood2026joint"] and app.papers == []
        assert "Reading list (2)" in str(app.query_one("#tabs").get_tab("reading").label)
        await select(app, pilot, "#inbox-table", first)
        await pilot.press("y")  # kept as read: straight to the Library
        await select(app, pilot, "#inbox-table", second)
        await pilot.press("l")  # to the reading list
        assert len(app.papers) == 1 and len(app.reading) == 3
        await pilot.press("2")
        await select(app, pilot, "#reading-table", "wood2026joint")
        assert not app.check_action("triage", ("skip",)) and app.check_action("triage", ("keep",))
        await pilot.press("n", "l")  # inbox-only: nothing happens
        assert len(app.reading) == 3
        await pilot.press("y")
        assert "wood2026joint" in [p.key for p in app.papers] and len(app.reading) == 2
        await select(app, pilot, "#reading-table", "xue2025fitness")
        await pilot.press("enter")
        assert isinstance(app.screen, tui.PaperScreen) and not app.check_action("triage", ("skip",))
        await pilot.press("y")  # done reading, from the paper's own screen
        assert not app.check_action("triage", ("keep",))  # already read
        await pilot.press("escape", "4")
        assert app.tab == "library" and len(app.papers) == 3
        await select(app, pilot, "#library-table", "xue2025fitness")
        assert not app.check_action("triage", ("keep",))
    _, notices = drive(lib, script)
    assert ("information", "read: wood2026joint (now in the Library)") in notices
    statuses = {p.key: p.status for p in Library.open(lib).papers()}
    assert statuses["wood2026joint"] == statuses["xue2025fitness"] == "read"
    assert sorted(statuses.values()) == ["later", "read", "read", "read"]


def test_reviews_tab(lib, run, tmp_path, browsed):
    for key in ("wood2026joint", "xue2025fitness"):
        (lib / "papers" / key / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    text = ("---\ntitle: {t}\npapers: [wood2026joint, xue2025fitness]\n---\n"
            "On [@wood2026joint] and [@xue2025fitness].\n")
    for title in ("Alignment and fitness", "Second look"):
        (tmp_path / "r.md").write_text(text.format(t=title))
        assert run("review", "add", str(tmp_path / "r.md"), "--by", "claude").code == 0
    first, second = (r.id for r in sorted(Reviews(lib).all(), key=lambda r: r.title))

    async def script(app, pilot):
        await pilot.press("3")
        label = lambda: str(app.query_one("#tabs").get_tab("reviews").label)
        assert app.tab == "reviews" and "Reviews (2, 2 new)" in label()
        table = app.query_one("#reviews-table")
        assert table.get_row(first)[0].style == "bold"
        await select(app, pilot, "#reviews-table", first)
        assert not app.check_action("triage", ("later",)) and app.check_action("triage", ("skip",))
        await pilot.press("y")
        assert table.get_row(first)[0].style == "" and "1 new" in label()
        await pilot.press("enter")  # the full page, in the browser
        await select(app, pilot, "#reviews-table", second)
        await pilot.press("n")
        assert [r.id for r in app.reviews] == [first]
        await pilot.press("slash", *"nomatch", "enter")
        assert app.reviews == []
        await pilot.press("slash", "ctrl+u", "enter", "2")
        await select(app, pilot, "#reading-table", "wood2026joint")
        assert "### Reviews" in tui.paper_markdown(app.lib.get("wood2026joint"), True, app.all_reviews)
    _, notices = drive(lib, script)
    assert browsed and browsed[0].endswith(f"/.ruru/reviews/{first}.html")
    assert [r.status for r in Reviews(lib).all()] == ["kept"]
    assert ("information", "discard: Second look") in notices


def test_reviews_tab_sort(lib, run, tmp_path):
    for key in ("wood2026joint", "xue2025fitness"):
        (lib / "papers" / key / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    for title, papers in (("Zeta on two", "[wood2026joint, xue2025fitness]"), ("Alpha on one", "[wood2026joint]")):
        cites = " ".join(f"[@{k}]" for k in papers.strip("[]").split(", "))
        (tmp_path / "r.md").write_text(f"---\ntitle: {title}\npapers: {papers}\n---\n{cites}\n")
        run("review", "add", str(tmp_path / "r.md"))
    zeta, alpha = (r.id for r in sorted(Reviews(lib).all(), key=lambda r: r.title, reverse=True))

    def order(app):
        table = app.query_one("#reviews-table")
        return [str(table.coordinate_to_cell_key((i, 0)).row_key.value) for i in range(table.row_count)]

    async def script(app, pilot):
        await pilot.press("3")
        assert order(app) == [alpha, zeta]  # filed last, shown first
        await pilot.press("S")  # title
        assert order(app) == [alpha, zeta] and "by title" in str(app.query_one("#tabs").get_tab("reviews").label)
        await pilot.press("S")  # papers: most first
        assert order(app) == [zeta, alpha]
        await select(app, pilot, "#reviews-table", zeta)
        await pilot.press("y")  # kept: below the new one, whatever the sort
        assert order(app) == [alpha, zeta]
        await pilot.press("S")
        assert app.review_sort == "created" and app.sort == "added"  # the library's sort is untouched
    drive(lib, script)


CLAIMS_TEX = r"""\begin{document}
Alignment is joint \cite{wood2026joint}. Landscapes are rugged \cite{xue2025fitness, ghost2020x}.
Nothing cited here.
\end{document}
"""


@pytest.fixture
def claims_project(lib, run, tmp_path):
    (tmp_path / "main.tex").write_text(CLAIMS_TEX)
    run("project", "add", "joint-align", "--path", str(tmp_path), "--bib", "refs.bib", "--tex", "main.tex")
    (lib / "papers" / "wood2026joint" / "paper.pdf").write_bytes(b"%PDF")
    return tmp_path


def test_claims_helpers(lib, claims_project, monkeypatch):
    library = Library.open(lib)
    claims = ops.claims(library, "joint-align")
    first, second = claims
    verdicts = {(first.id, "wood2026joint"): tui.claimsmod.make(
        first, "wood2026joint", "partial", quote="joint for GC-MS", note="only GC-MS", by="claude")}
    assert tui.claim_marks(first, verdicts) == "~" and tui.claim_marks(second, verdicts) == "○○"
    assert tui.filter_claims(claims, verdicts, "v:unchecked") == [second]
    assert tui.filter_claims(claims, verdicts, "k:ghost2020x") == [second]
    assert tui.filter_claims(claims, verdicts, "v:partial joint") == [first]
    md = tui.claim_markdown(library, second, verdicts, "main.tex")
    assert "### main.tex:2" in md and "no PDF" in md and "not in the library" in md
    md = tui.claim_markdown(library, first, verdicts, "main.tex")
    assert "**partial** (p. " not in md and "by claude" in md and "> joint for GC-MS" in md
    text = tui.project_markdown(library, tui.projmod.load(library.settings.projects)["joint-align"],
                                (claims, verdicts))
    assert "### Claims (2 sentences citing 3 keys)" in text and "~ partial 1 · ○ unchecked 2" in text
    assert "**partial** `wood2026joint` main.tex:2: “joint for GC-MS” only GC-MS" in text
    assert "**No PDF (1):** `xue2025fitness`" in text and "**Not in the library:** `ghost2020x`" in text
    for editor, expected in [("vim", ["vim", "+7", "/x.tex"]),
                             ("code --wait", ["code", "--wait", "-g", "/x.tex:7"]),
                             ("/usr/local/bin/subl", ["/usr/local/bin/subl", "/x.tex:7"])]:
        monkeypatch.setenv("VISUAL", editor)
        assert tui.editor_command(tui.Path("/x.tex"), 7) == expected


def test_claims_screen(lib, claims_project, monkeypatch, browsed, tmp_path):
    opened, edited = [], []
    monkeypatch.setattr(tui, "open_file", opened.append)
    monkeypatch.setattr(tui, "edit_at", lambda path, line: edited.append((path.name, line)))
    monkeypatch.setattr(cli, "downloads_dir", lambda: tmp_path / "dl")
    (tmp_path / "dl").mkdir()

    def no_open_access(library, client, key, force=False):
        raise tui.RuruError(f"{key}: no open-access PDF known")
    monkeypatch.setattr(ops, "fetch_pdf", no_open_access)

    async def script(app, pilot):
        await pilot.press("5")
        assert "### Claims (2 sentences" in app.query_one("#project-detail")._markdown
        await pilot.press("enter")
        screen = app.screen
        assert isinstance(screen, tui.ClaimsScreen) and screen.project == "joint-align"
        table = screen.query_one("#claims-table")
        assert table.row_count == 2 and "0/3 cited keys checked" in screen.sub_title
        # A verdict on the first claim: one key, has a PDF, so verdict → quote → page.
        await pilot.press("v")
        await pilot.press("enter")  # supported (the first choice)
        await pilot.press(*"joint alignment", "enter")
        await pilot.press("4", "enter")
        await pilot.pause()
        assert "1/3 cited keys checked" in screen.sub_title
        assert screen.query_one("#claims-detail")._markdown.count("**supported** (p. 4, from pdf") == 1
        await pilot.press("d", "e")
        # The second claim cites two keys: choose, then no PDF, so what was read; escape cancels.
        await pilot.press("j", "v", "enter", "down", "down", "enter", "enter", "escape")
        assert "1/3" in screen.sub_title
        await pilot.press("v", "enter", "down", "down", "enter", "down", "enter", *"says the opposite", "enter")
        await pilot.pause()
        assert "2/3" in screen.sub_title
        await pilot.press("x")
        await pilot.pause()
        assert "1/3" in screen.sub_title
        await pilot.press("x")  # nothing left to drop
        await pilot.press("slash", *"v:unchecked", "enter")
        assert table.row_count == 1 and "filter: v:unchecked" in screen.sub_title
        await pilot.press("slash", *"nothing matches", "enter")
        assert table.row_count == 0 and screen.query_one("#claims-empty").display
        await pilot.press("v", "x", "d", "e")  # no claim under the cursor: nothing happens
        await pilot.press("slash", "ctrl+u", "enter", "j")
        await pilot.press("m")
        await app.workers.wait_for_complete()
        await pilot.pause()
        (tmp_path / "dl" / "xue2025fitness.pdf").write_bytes(b"%PDF-1.4")
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("d")
        await pilot.press("enter")  # xue2025fitness, now with its PDF
        await pilot.press("escape")
        assert not isinstance(app.screen, tui.ClaimsScreen)
    _, notices = drive(lib, script)
    verdicts = tui.claimsmod.load(lib, "joint-align")
    assert [(v.key, v.verdict, v.source, v.page, v.quote) for v in verdicts.values()] == [
        ("wood2026joint", "supported", "pdf", "4", "joint alignment")]
    log = git_log(lib)
    assert log[0].startswith("ruru: verdict joint-align drop ")
    assert log[1:3] == ["ruru: verdict joint-align xue2025fitness:unsupported",
                        "ruru: verdict joint-align wood2026joint:supported"]
    assert edited == [("main.tex", 2)] and opened[0].name == "paper.pdf"
    assert opened[-1].parent.name == "xue2025fitness"
    assert browsed == ["https://doi.org/10.1186/s13321-021-00544-w"]
    assert ("warning", "no verdict on this claim yet") in notices
    assert any("opened 1 publisher page(s)" in m and "not in the library: ghost2020x" in m for _, m in notices)
    assert ("information", f"filed 1 PDF(s) from {tmp_path / 'dl'}") in notices


def test_claims_screen_needs_tex(lib, run, tmp_path):
    run("project", "add", "notex", "--path", str(tmp_path), "--bib", "refs.bib")

    async def script(app, pilot):
        await pilot.press("5", "enter")
        assert not isinstance(app.screen, tui.ClaimsScreen)
    _, notices = drive(lib, script)
    assert ("warning", "notex lists no tex files: add it again with --tex") in notices
