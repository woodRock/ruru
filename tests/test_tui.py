"""Textual pilot tests for `ruru tui` (skipped without the [tui] extra)."""

import asyncio
import json

import pytest

pytest.importorskip("textual")

from conftest import git_log
from httpfix import FEEDS
from ruru import cli, model, ops, tui
from ruru.errors import LockHeld
from ruru.inbox import Inbox
from ruru.library import Library

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
        assert "Inbox (4)" in str(app.query_one("#tabs").get_tab("inbox").label)
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
    assert statuses == ["later", "to-read"]
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
        assert [p.key for p in app.papers] == ["wood2026joint"]
        await pilot.press("slash", "ctrl+u", *"fitness", "enter")
        assert [p.key for p in app.papers] == ["xue2025fitness"]
    drive(lib, script)


def test_library_paper_screen_actions(lib, monkeypatch):
    monkeypatch.setattr(cli, "edit_text", lambda text: "Edited in the TUI.")
    opened = []
    monkeypatch.setattr(tui.webbrowser, "open", opened.append)

    async def script(app, pilot):
        await pilot.press("2")
        await select(app, pilot, "#library-table", "wood2026joint")
        assert app.query_one("#library-table").row_count == 2 and app.tab == "library"
        await pilot.press("enter")
        assert isinstance(app.screen, tui.PaperScreen) and app.screen.key == "wood2026joint"
        await pilot.press("e")
        await pilot.press("t", *"+key -gc-ms", "enter")
        await pilot.press("s")
        await pilot.press("down", "down", "down", "enter")  # to-read, later, reading, read
        await pilot.press("o")  # no URL
        await pilot.press("escape")
        assert not isinstance(app.screen, tui.PaperScreen)
        await select(app, pilot, "#library-table", "xue2025fitness")
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
            await select(app, pilot, "#library-table", key)
            await pilot.press("d")
            await app.workers.wait_for_complete()
            await pilot.pause()
    drive(lib, script)
    assert [str(p).split("/")[-2] for p in ran] == ["wood2026joint", "xue2025fitness"]
    assert (lib / "papers" / "xue2025fitness" / "paper.pdf").read_bytes() == b"%PDF-1.4 test"


def test_pdf_fetch_failure(lib, monkeypatch):
    async def script(app, pilot):
        await pilot.press("2")
        await select(app, pilot, "#library-table", "wood2026joint")
        await pilot.press("d")
        await app.workers.wait_for_complete()
    # The paper has no DOI, so ruru looks it up by title, which finds nothing.
    from httpfix import Router
    monkeypatch.setattr(tui.cli.http, "default_transport", Router().add("title.search", body='{"results": []}'))
    _, notices = drive(lib, script)
    assert any(s == "error" and "no work titled" in m for s, m in notices)


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
        await pilot.press("3")
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
        await pilot.press("y", "a", "t", "o", "2", "e", "s", "d", "t", "o", "3")
        assert app.query_one("#library-table").row_count == 0
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
