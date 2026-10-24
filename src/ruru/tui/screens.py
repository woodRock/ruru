"""The two screens that open over the app: a paper, and a project's claims."""

from __future__ import annotations

import contextlib
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Markdown,
    Static,
)

from ruru import claims as claimsmod
from ruru import desktop, logo, ops
from ruru.errors import RuruError
from ruru.reviews import Reviews
from ruru.tui.render import ClaimsView, claim_markdown, claim_marks, claims_view, filter_claims, paper_markdown
from ruru.tui.widgets import DETAIL_SECONDS, Choose, Prompt, Table

Step = tuple[str, Callable[[dict[str, str]], ModalScreen[str | None]], Callable[[dict[str, str]], bool]]

if TYPE_CHECKING:
    from ruru.tui.app import RuruApp


class PaperScreen(Screen[None]):
    """One paper in full: metadata, notes, agent notes."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q", "app.pop_screen", "back"),
        Binding("e", "app.edit_notes", "edit notes"),
        Binding("d", "app.pdf", "PDF"),
        Binding("o", "app.open_url", "open URL"),
        Binding("t", "app.tag", "tag"),
        Binding("s", "app.status", "status"),
        Binding("x", "app.remove", "remove"),
    ]

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Markdown(id="paper-full")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_paper()

    def refresh_paper(self) -> None:
        app = self.app
        from ruru.tui.app import RuruApp

        assert isinstance(app, RuruApp)
        with contextlib.suppress(RuruError):
            self.query_one("#paper-full", Markdown).update(
                paper_markdown(app.lib.get(self.key), app.lib.has_pdf(self.key),
                               Reviews(app.lib.root).citing(self.key)))


class ClaimsScreen(Screen[None]):
    """A project's citing sentences and the verdict on each cited key (`ruru claims`, `ruru verdict`)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q", "back", "back"),
        Binding("slash", "filter", "filter"),
        Binding("v", "verdict", "verdict"),
        Binding("x", "drop", "drop verdict"),
        Binding("d", "pdf", "PDF / source"),
        Binding("e", "edit", "edit .tex"),
        Binding("m", "app.missing_pdfs", "missing PDFs"),
        Binding("a", "add_pdfs", "file downloads"),
    ]

    def __init__(self, project: str) -> None:
        super().__init__()
        self.project = project
        self.filter = ""
        self.rows: dict[str, claimsmod.Claim] = {}
        self.view = ClaimsView([], {}, set(), {}, {})
        self.mains: list[Path] = []
        self._timer: Timer | None = None

    @property
    def ruru(self) -> RuruApp:
        app = self.app
        from ruru.tui.app import RuruApp

        assert isinstance(app, RuruApp)
        return app

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(id="claims-filter",
                    placeholder="filter: words, k:KEY v:VERDICT v:unchecked v:outdated v:own (enter)")
        with Horizontal(classes="pane"):
            yield Table(id="claims-table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(classes="detail"):
                yield Static(id="claims-empty", classes="empty")
                yield Markdown(id="claims-detail")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Table).add_columns("", "where", "keys", "sentence")
        self.refresh_claims()
        self.query_one(Table).focus()

    def refresh_claims(self) -> None:
        app = self.ruru
        try:
            self.mains = ops.get_project(app.lib, self.project).tex_paths
            claims = app.project_claims(self.project)
        except RuruError as e:
            app.notify(str(e), severity="error", timeout=8)
            return
        self.view = claims_view(app.lib, self.project, claims)
        shown = filter_claims(self.view, self.filter)
        self.rows = {}
        for c in shown:
            self.rows.setdefault(f"{c.id}@{c.file}:{c.line}", c)
        self.query_one(Table).refill([
            (key, [claim_marks(c, self.view), f"{claimsmod.where(c, self.mains)}:{c.line}",
                   " ".join(c.keys), c.text]) for key, c in self.rows.items()])
        checked = sum(self.view.state(c, k) not in ("unchecked", "outdated") for c in claims for k in c.keys)
        total = sum(len(c.keys) for c in claims)
        self.sub_title = (f"{self.project}: {len(shown)} of {len(claims)} claims · {checked}/{total} cited keys "
                          "checked" + (f" · filter: {self.filter}" if self.filter else ""))
        self.show_claim()

    def current(self) -> claimsmod.Claim | None:
        key = self.query_one(Table).current_key()
        return self.rows.get(key) if key else None

    def show_claim(self) -> None:
        c = self.current()
        md = self.query_one("#claims-detail", Markdown)
        empty = self.query_one("#claims-empty", Static)
        if c is None:
            empty.update(logo.rich_text(logo.SMALL, "No claims match." if self.filter else "No citing sentences.",
                                        "/ changes the filter." if self.filter else "Cite something in the .tex."))
        else:
            md.update(claim_markdown(self.ruru.lib, c, self.view, claimsmod.where(c, self.mains)))
        empty.display = c is None
        md.display = c is not None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        if self._timer:
            self._timer.stop()
        self._timer = self.set_timer(DETAIL_SECONDS, self._settled)

    def _settled(self) -> None:
        with contextlib.suppress(NoMatches):  # the screen may be closing
            self.show_claim()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.filter = event.value.strip()
        event.input.remove_class("shown")
        self.refresh_claims()
        self.query_one(Table).focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_filter(self) -> None:
        box = self.query_one("#claims-filter", Input)
        box.value = self.filter
        box.add_class("shown")
        box.focus()

    def pick_key(self, keys: list[str], label: str, then: Callable[[str], None]) -> None:
        if not keys:
            return
        if len(keys) == 1:
            then(keys[0])
            return
        self.app.push_screen(Choose(label, keys), lambda k: then(k) if k else None)

    def ask(self, steps: list[Step], done: Callable[[dict[str, str]], None],
            answers: dict[str, str] | None = None) -> None:
        """Push each step's dialog in turn (those whose condition holds); escape anywhere cancels."""
        answers = answers or {}
        step = next((step for step in steps if step[0] not in answers and step[2](answers)), None)
        if step is None:
            done(answers)
            return
        name, make, _ = step

        def got(value: str | None) -> None:
            if value is not None:
                self.ask(steps, done, {**answers, name: value})
        self.app.push_screen(make(answers), got)

    def action_verdict(self) -> None:
        c = self.current()
        if c is None:
            return
        lib = self.ruru.lib

        def judge(k: str) -> None:
            has_pdf = lib.exists(k) and lib.has_pdf(k)
            entry = self.view.cited.get(k)  # a draft or software: the source is known, no PDF to ask about
            old = self.view.verdicts.get((c.id, k))

            def was(field: str) -> str:
                return str(getattr(old, field, None) or "")
            steps: list[Step] = [
                ("verdict", lambda a: Choose(f"Does {k} support this sentence?", list(claimsmod.VERDICTS)),
                 lambda a: True),
                ("from", lambda a: Choose(f"{k} has no PDF here. What did you read?", ["abstract", "other"]),
                 lambda a: not has_pdf and entry is None),
                ("quote", lambda a: Prompt(f"{k}'s own words, verbatim:", value=was("quote")),
                 lambda a: a["verdict"] in claimsmod.NEEDS_QUOTE),
                ("page", lambda a: Prompt("Page (optional):" if entry is None else
                                          "Where: file and line, or section (optional):", value=was("page")),
                 lambda a: (has_pdf or entry is not None) and a["verdict"] in claimsmod.NEEDS_QUOTE),
                ("note", lambda a: Prompt("What does the paper say instead?", value=was("note")),
                 lambda a: a["verdict"] in claimsmod.NEEDS_NOTE),
            ]

            def record(a: dict[str, str]) -> None:
                self.ruru.attempt(lambda: ops.record_verdicts(lib, self.project, [{"claim": c.id, "key": k, **a}]),
                                  f"{k}: {a['verdict']}")
            self.ask(steps, record)
        self.pick_key(c.keys, "Which cited paper?", judge)

    def action_drop(self) -> None:
        c = self.current()
        if c is None:
            return
        judged = [k for k in c.keys if (c.id, k) in self.view.verdicts]
        if not judged:
            self.app.notify("no verdict on this claim yet", severity="warning")
            return
        def drop(k: str) -> None:
            self.ruru.attempt(lambda: ops.drop_verdicts(self.ruru.lib, self.project, c.id, [k]),
                              f"dropped the verdict for {k}")
        self.pick_key(judged, "Drop the verdict for which paper?", drop)

    def action_pdf(self) -> None:
        c = self.current()
        if c is None:
            return
        app = self.ruru
        known = [k for k in c.keys if app.lib.exists(k) or k in self.view.cited]
        if not known:
            app.notify(f"not in the library: {', '.join(c.keys)}", severity="warning")
            return

        def show(k: str) -> None:
            """A paper's PDF (else open access, else its publisher); a draft's .tex or software's README."""
            if not app.lib.exists(k):
                sources = ops.cited_sources(app.lib, k)
                link = self.view.cited[k].link
                if sources:
                    desktop.open_file(sources[0])
                elif link:
                    webbrowser.open(link)
                else:
                    app.notify(f"{k}: nothing to open (no .tex, README or URL)", severity="warning")
            elif app.lib.has_pdf(k):
                desktop.open_file(app.lib.paper_dir(k) / "paper.pdf")
            else:
                app.notify(f"looking for an open-access PDF of {k}…")
                app.fetch_pdf(k)
        self.pick_key(known, "Which cited work?", show)

    def action_edit(self) -> None:
        c = self.current()
        if c is None:
            return
        with self.ruru.suspended():
            desktop.edit_at(c.file, c.line)
        self.ruru.reload()

    def action_add_pdfs(self) -> None:
        def add() -> None:
            filed, refused = ops.add_pdfs(self.ruru.lib, [desktop.downloads_dir()])
            for src, problem in refused:
                self.app.notify(f"{src.name}: {problem}", severity="warning", timeout=8)
            self.app.notify(f"filed {len(filed)} PDF(s) from {desktop.downloads_dir()}" if filed
                            else f"no KEY.pdf files in {desktop.downloads_dir()}")
        self.ruru.attempt(add)
