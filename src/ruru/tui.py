"""`ruru tui`: an interactive view of the library (optional extra: `pip install 'ruru[tui]'`).

Only `ruru tui` imports this module, and so Textual. Every action calls the same function the
CLI does (`ruru.ops`), so the two cannot diverge, and every change is committed as usual. The app
polls the library's files and reloads when something else (an agent, the CLI, a scheduled fetch)
changes them.

Keys: j/k move, / filter, r reload, q quit; tabs 1 Inbox, 2 Library, 3 Projects.
Inbox: y keep, n skip, l later, a accept the suggestion, t keep with tags, p keep for a project,
o open URL, f fetch. Library: enter open, t tag, s status, e edit notes, d PDF (fetch/open), o URL.
Projects: c re-check, m get the missing PDFs its claims cite, enter the claims screen.
Claims: / filter (k:KEY v:VERDICT v:unchecked), v verdict, x drop it, d PDF, e edit the .tex there,
m missing PDFs.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import subprocess
import webbrowser
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Markdown,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
)

from ruru import claims as claimsmod
from ruru import cli, logo, ops
from ruru import projects as projmod
from ruru.config import resolve_library
from ruru.errors import OK, RuruError
from ruru.inbox import Inbox, Item, read_viewed, write_viewed
from ruru.library import Library
from ruru.model import STATUSES, UNREAD, Paper
from ruru.reviews import SORTS as REVIEW_SORTS
from ruru.reviews import Review, Reviews, created_label
from ruru.search import SORTS, Filed, filed_times, in_project, needs_filed, search, sort_papers

POLL_SECONDS = 2.0
VIEW_SECONDS = 1.0  # resting on an inbox item this long marks it viewed (no longer bold)
DETAIL_SECONDS = 0.05  # the detail pane renders once the cursor settles, so holding j stays smooth
PANES = ("inbox", "reading", "reviews", "library", "project")
SUGGESTION_MARK = {"keep": "+", "skip": "-", "later": "~"}  # an agent's suggestion, at a glance


def run(args: argparse.Namespace) -> int:
    lib = Library.open(resolve_library(args.library), auto_commit=False if args.no_commit else None)
    RuruApp(lib).run()
    return OK


def open_file(path: Path) -> None:
    """Open a file in the system viewer (Preview, …)."""
    subprocess.run([cli.opener, str(path)], check=False)


def editor_command(path: Path, line: int) -> list[str]:
    """$VISUAL/$EDITOR (vi) opened at `line`: `+LINE FILE` for terminal editors, FILE:LINE for GUI ones."""
    editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi")
    name = Path(editor[0]).name
    if name in ("code", "cursor", "codium"):
        return [*editor, "-g", f"{path}:{line}"]
    if name in ("subl", "zed"):
        return [*editor, f"{path}:{line}"]
    return [*editor, f"+{line}", str(path)]


def edit_at(path: Path, line: int) -> None:
    subprocess.run(editor_command(path, line), check=False)


def fingerprint(root: Path) -> tuple[tuple[str, int], ...]:
    """Cheap change detection: mtimes of the git index, settings, seen.jsonl and the directories
    whose contents change when papers or inbox items are added, removed or committed."""
    inbox = root / "inbox"
    paths = [root / ".git" / "index", root / "seen.jsonl", root / "ruru.toml", root / "papers", inbox,
             root / "reviews", root / claimsmod.DIR]
    paths += sorted(inbox.iterdir()) if inbox.is_dir() else []
    return tuple((str(p), p.stat().st_mtime_ns) for p in paths if p.exists())


# --- filtering -----------------------------------------------------------------------------------

def parse_filter(text: str) -> tuple[dict[str, str], str]:
    """'t:gc-ms s:to-read retention time' -> ({'t': 'gc-ms', 's': 'to-read'}, 'retention time')."""
    fields, words = {}, []
    for token in text.split():
        prefix, _, value = token.partition(":")
        if value and prefix in ("t", "p", "s", "f", "k", "v"):
            fields[prefix] = value
        else:
            words.append(token)
    return fields, " ".join(words)


def filter_papers(papers: list[Paper], text: str, sort: str = SORTS[0],
                  filed: Filed = lambda p: 0.0) -> list[Paper]:
    """Words rank by relevance unless a sort other than the default was chosen."""
    fields, words = parse_filter(text)
    papers = [p for p in papers
              if (not fields.get("t") or fields["t"] in p.tags)
              and (not fields.get("p") or in_project(p, fields["p"]))
              and (not fields.get("s") or p.status == fields["s"])]
    if words:
        papers = [p for _, p in search(papers, words)]
        if sort == SORTS[0]:
            return papers
    return sort_papers(papers, sort, filed=filed)


def filter_items(items: list[Item], text: str) -> list[Item]:
    fields, words = parse_filter(text)
    if fields.get("f"):
        items = [i for i in items if i.paper.source == f"feed:{fields['f']}"]
    if words:
        keep = {id(p) for _, p in search([i.paper for i in items], words)}
        items = [i for i in items if id(i.paper) in keep]
    return items


def filter_reviews(reviews: list[Review], text: str) -> list[Review]:
    fields, words = parse_filter(text)
    if fields.get("t"):
        reviews = [r for r in reviews if fields["t"] in r.tags]
    folded = [w.lower() for w in words.split()]
    return [r for r in reviews if all(w in f"{r.title} {r.body} {' '.join(r.papers)}".lower() for w in folded)]


Verdicts = dict[tuple[str, str], claimsmod.Verdict]


def filter_claims(claims: list[claimsmod.Claim], verdicts: Verdicts, text: str) -> list[claimsmod.Claim]:
    """Words in the sentence; k:KEY cites it; v:VERDICT has it for some key (v:unchecked: a key without)."""
    fields, words = parse_filter(text)
    folded = [w.lower() for w in words.split()]

    def given(c: claimsmod.Claim) -> set[str]:
        return {verdicts[(c.id, k)].verdict if (c.id, k) in verdicts else "unchecked" for k in c.keys}
    return [c for c in claims
            if (not fields.get("k") or fields["k"] in c.keys)
            and (not fields.get("v") or fields["v"] in given(c))
            and all(w in c.text.lower() for w in folded)]


def claim_marks(c: claimsmod.Claim, verdicts: Verdicts) -> str:
    return "".join(claimsmod.MARKS[verdicts[(c.id, k)].verdict][0] if (c.id, k) in verdicts
                   else claimsmod.UNCHECKED for k in c.keys)


# --- markdown views ------------------------------------------------------------------------------

def _ids(p: Paper) -> str:
    return " · ".join(f"{k}: `{v}`" for k, v in p.ids.items() if v)


def item_markdown(item: Item) -> str:
    p = item.paper
    lines = [f"## {p.title}", ", ".join(p.authors) or "*no authors listed*",
             " · ".join(str(x) for x in (p.year, p.venue) if x),
             f"`{item.id}` · {p.source} · fetched {item.fetched}"]
    if s := item.suggestion:
        lines.append(f"> **Suggested: {s.get('action')}** ({s.get('by')}): {s.get('reason')}")
    lines.append(p.abstract or "*No abstract.*")
    if ids := _ids(p):
        lines.append(ids)
    if p.url:
        lines.append(p.url)
    return "\n\n".join(line for line in lines if line)


def review_markdown(lib: Library, r: Review) -> str:
    lines = [f"## {r.title}",
             f"{created_label(r)} · {'**new**' if r.status == 'new' else 'kept'}"
             + (f" · by {r.by}" if r.by else "") + "  ·  *o opens the full page*",
             " ".join(f"`#{t}`" for t in r.tags), "### Papers"]
    for k in r.papers:
        try:
            p = lib.get(k)
            lines.append(f"- `{k}` **{p.status}**{' · PDF' if lib.has_pdf(k) else ''} {p.title}")
        except RuruError:
            lines.append(f"- `{k}` *no longer in the library*")
    lines += ["---", r.body]
    return "\n\n".join(line for line in lines if line)


def paper_markdown(p: Paper, has_pdf: bool = False, reviews: list[Review] | None = None) -> str:
    lines = [f"## {p.title}", ", ".join(p.authors) or "*no authors listed*",
             " · ".join(str(x) for x in (p.year, p.venue) if x),
             f"`{p.key}` · **{p.status}** · added {p.added or '-'}" + (" · PDF" if has_pdf else ""),
             " ".join(f"`#{t}`" for t in p.tags) + (f"  projects: {', '.join(p.projects)}" if p.projects else ""),
             _ids(p), p.url or "", p.abstract or ""]
    if reviews:
        lines += ["### Reviews", *[f"- `{r.id}` {r.title}" for r in reviews]]
    lines += ["### Notes", p.notes or "*None yet: press e.*"]
    if p.agent_notes:
        lines += ["### Agent notes", p.agent_notes.replace("### ", "#### ")]
    return "\n\n".join(line for line in lines if line)


def claim_markdown(lib: Library, c: claimsmod.Claim, verdicts: Verdicts, place: str) -> str:
    lines = [f"### {place}:{c.line}", "> " + c.text, f"`{c.id}`"]
    for k in c.keys:
        title = f" — {lib.get(k).title}" if lib.exists(k) else ""
        v = verdicts.get((c.id, k))
        if v is None:
            why = ("" if lib.has_pdf(k) else " · no PDF (m gets the missing ones)") if lib.exists(k) \
                else " · not in the library"
            lines += [f"#### {claimsmod.UNCHECKED} `{k}`{title}", f"*unchecked*{why} · v records a verdict"]
            continue
        page = f"p. {v.page}, " if v.page else ""
        lines.append(f"#### {claimsmod.MARKS[v.verdict][0]} `{k}`{title}")
        lines.append(f"**{v.verdict}** ({page}from {v.source}{', by ' + v.by if v.by else ''}, {v.date})")
        lines += [f"> {v.quote}"] if v.quote else []
        lines += [v.note] if v.note else []
    return "\n\n".join(lines)


def claims_summary(lib: Library, project: projmod.Project, claims: list[claimsmod.Claim],
                   verdicts: Verdicts) -> list[str]:
    """The project pane's Claims section: counts, the problems in full, what's left and why."""
    pairs = [(c, k) for c in claims for k in c.keys]
    counts = {name: sum(1 for c, k in pairs if (v := verdicts.get((c.id, k))) and v.verdict == name)
              for name in claimsmod.VERDICTS}
    unchecked = sum(1 for c, k in pairs if (c.id, k) not in verdicts)
    keys = list(dict.fromkeys(k for _, k in pairs))
    no_pdf = [k for k in keys if lib.exists(k) and not lib.has_pdf(k)]
    unknown = [k for k in keys if not lib.exists(k)]
    lines = [f"### Claims ({len(claims)} sentences citing {len(keys)} keys)",
             " · ".join([f"{claimsmod.MARKS[n][0]} {n} {counts[n]}" for n in claimsmod.VERDICTS if counts[n]]
                        + [f"{claimsmod.UNCHECKED} unchecked {unchecked}"])]
    for c, k in pairs:
        v = verdicts.get((c.id, k))
        if v and v.verdict in ("unsupported", "partial", "not-found"):
            detail = " ".join(x for x in (f"“{v.quote}”" if v.quote else "", v.note or "") if x)
            lines.append(f"- {claimsmod.MARKS[v.verdict][0]} **{v.verdict}** `{k}` "
                         f"{claimsmod.where(c, project.tex_paths)}:{c.line}: {detail}".rstrip(": "))
    if no_pdf:
        lines.append(f"**No PDF ({len(no_pdf)}):** " + ", ".join(f"`{k}`" for k in no_pdf) + " · m gets them")
    if unknown:
        lines.append("**Not in the library:** " + ", ".join(f"`{k}`" for k in unknown))
    lines.append("*enter: every claim, with its verdicts*")
    return lines


def project_markdown(lib: Library, project: projmod.Project,
                     claims: tuple[list[claimsmod.Claim], Verdicts] | None = None) -> str:
    papers = [p for p in lib.papers() if project.name in p.projects]
    order = {s: i for i, s in enumerate(STATUSES)}
    lines = [f"## {project.name}",
             f"`{project.path}` · bib `{project.bib}` · tex {', '.join(project.tex) or '-'}",
             f"### Reading list ({len(papers)})"]
    papers.sort(key=lambda p: (order.get(p.status, 9), p.key))
    lines += [f"- **{p.status}** `{p.key}` {p.title}" for p in papers] or ["*empty*"]
    lines.append("### Citation check")
    if not project.tex:
        lines.append("*No tex files configured.*")
        return "\n\n".join(lines)
    try:
        report, _ = ops.check(lib, project.name)
    except RuruError as e:
        lines.append(f"*{e}*")
        return "\n\n".join(lines)
    lines.append(f"{report.cited} cited keys · {report.issues} issue(s)")
    for label, keys in [("Missing from the bib", [m["key"] for m in report.missing]),
                        ("Never cited (managed block)", report.unused),
                        ("Kept for this project, not cited", report.kept_unused),
                        ("Out of date with the library", report.stale)]:
        if keys:
            lines.append(f"**{label}:** " + ", ".join(f"`{k}`" for k in keys))
    lines += [f"**Duplicate:** `{a}` = `{b}`" for a, b in report.duplicates]
    lines += [f"*{w}*" for w in report.warnings]
    if claims is not None:
        lines += claims_summary(lib, project, *claims)
    return "\n\n".join(lines)


# --- widgets and screens -------------------------------------------------------------------------

class Table(DataTable[Any]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
        Binding("g", "scroll_top", "top", show=False),
        Binding("G", "scroll_bottom", "bottom", show=False),
    ]

    def current_key(self) -> str | None:
        if not self.row_count:
            return None
        return str(self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value)

    def refill(self, rows: list[tuple[str, list[str]]], bold: set[str] | None = None) -> None:
        """Replace the rows (those in `bold` in bold), keeping the cursor on the same key, or the same
        position if it is gone."""
        key, index = self.current_key(), self.cursor_row
        self.clear()
        for row_key, cells in rows:
            style = "bold" if bold and row_key in bold else ""
            self.add_row(*(Text(c, style=style) for c in cells), key=row_key)
        keys = [k for k, _ in rows]
        if keys:
            self.move_cursor(row=keys.index(key) if key in keys else min(index, len(keys) - 1))


class Prompt(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, label: str, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.label, self.placeholder, self.value = label, placeholder, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.label)
            yield Input(value=self.value, placeholder=self.placeholder)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Choose(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, label: str, choices: list[str]) -> None:
        super().__init__()
        self.label, self.choices = label, choices

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.label)
            yield OptionList(*self.choices)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.choices[event.option_index])

    def action_cancel(self) -> None:
        self.dismiss(None)


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
        assert isinstance(app, RuruApp)
        with contextlib.suppress(RuruError):
            self.query_one("#paper-full", Markdown).update(
                paper_markdown(app.lib.get(self.key), app.lib.has_pdf(self.key),
                               Reviews(app.lib.root).citing(self.key)))


Step = tuple[str, Callable[[dict[str, str]], ModalScreen[str | None]], Callable[[dict[str, str]], bool]]


class ClaimsScreen(Screen[None]):
    """A project's citing sentences and the verdict on each cited key (`ruru claims`, `ruru verdict`)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q", "back", "back"),
        Binding("slash", "filter", "filter"),
        Binding("v", "verdict", "verdict"),
        Binding("x", "drop", "drop verdict"),
        Binding("d", "pdf", "PDF"),
        Binding("e", "edit", "edit .tex"),
        Binding("m", "app.missing_pdfs", "missing PDFs"),
        Binding("a", "add_pdfs", "file downloads"),
    ]

    def __init__(self, project: str) -> None:
        super().__init__()
        self.project = project
        self.filter = ""
        self.rows: dict[str, claimsmod.Claim] = {}
        self.verdicts: Verdicts = {}
        self.mains: list[Path] = []
        self._timer: Timer | None = None

    @property
    def ruru(self) -> RuruApp:
        app = self.app
        assert isinstance(app, RuruApp)
        return app

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(id="claims-filter", placeholder="filter: words, k:KEY v:VERDICT v:unchecked (enter to apply)")
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
        self.verdicts = claimsmod.load(app.lib.root, self.project)
        shown = filter_claims(claims, self.verdicts, self.filter)
        self.rows = {}
        for c in shown:
            self.rows.setdefault(f"{c.id}@{c.file}:{c.line}", c)
        self.query_one(Table).refill([
            (key, [claim_marks(c, self.verdicts), f"{claimsmod.where(c, self.mains)}:{c.line}", " ".join(c.keys),
                   c.text]) for key, c in self.rows.items()])
        checked = sum((c.id, k) in self.verdicts for c in claims for k in c.keys)
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
            md.update(claim_markdown(self.ruru.lib, c, self.verdicts, claimsmod.where(c, self.mains)))
        empty.display = c is None
        md.display = c is not None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        if self._timer:
            self._timer.stop()
        self._timer = self.set_timer(DETAIL_SECONDS, self.show_claim)

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
            old = self.verdicts.get((c.id, k))

            def was(field: str) -> str:
                return str(getattr(old, field, None) or "")
            steps: list[Step] = [
                ("verdict", lambda a: Choose(f"Does {k} support this sentence?", list(claimsmod.VERDICTS)),
                 lambda a: True),
                ("from", lambda a: Choose(f"{k} has no PDF here. What did you read?", ["abstract", "other"]),
                 lambda a: not has_pdf),
                ("quote", lambda a: Prompt(f"{k}'s own words, verbatim:", value=was("quote")),
                 lambda a: a["verdict"] in claimsmod.NEEDS_QUOTE),
                ("page", lambda a: Prompt("Page (optional):", value=was("page")),
                 lambda a: has_pdf and a["verdict"] in claimsmod.NEEDS_QUOTE),
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
        judged = [k for k in c.keys if (c.id, k) in self.verdicts]
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
        known = [k for k in c.keys if app.lib.exists(k)]
        if not known:
            app.notify(f"not in the library: {', '.join(c.keys)}", severity="warning")
            return

        def show(k: str) -> None:
            if app.lib.has_pdf(k):
                open_file(app.lib.paper_dir(k) / "paper.pdf")
            else:
                app.notify(f"looking for an open-access PDF of {k}…")
                app.fetch_pdf(k)
        self.pick_key(known, "Which paper's PDF?", show)

    def action_edit(self) -> None:
        c = self.current()
        if c is None:
            return
        with self.ruru.suspended():
            edit_at(c.file, c.line)
        self.ruru.reload()

    def action_add_pdfs(self) -> None:
        def add() -> None:
            filed, refused = ops.add_pdfs(self.ruru.lib, [cli.downloads_dir()])
            for src, problem in refused:
                self.app.notify(f"{src.name}: {problem}", severity="warning", timeout=8)
            self.app.notify(f"filed {len(filed)} PDF(s) from {cli.downloads_dir()}" if filed
                            else f"no KEY.pdf files in {cli.downloads_dir()}")
        self.ruru.attempt(add)


# --- the app -------------------------------------------------------------------------------------

class RuruApp(App[None]):
    TITLE = logo.plain([logo.MINI]) + " ruru"
    CSS = """
    TabPane { padding: 0; }
    .pane { height: 1fr; }
    .pane Table { width: 3fr; }
    .pane .detail { width: 2fr; border-left: solid $primary-darken-2; padding: 0 1; }
    #filter, #claims-filter { dock: top; display: none; }
    #filter.shown, #claims-filter.shown { display: block; }
    Prompt, Choose { align: center middle; }
    .dialog { width: 70; height: auto; max-height: 20; border: thick $primary; background: $surface; padding: 1; }
    #project-list { width: 1fr; }
    .empty { padding: 1 2; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "quit"),
        Binding("r", "reload", "reload"),
        Binding("slash", "filter", "filter"),
        Binding("1", "tab('inbox')", "inbox", show=False),
        Binding("2", "tab('reading')", "reading list", show=False),
        Binding("3", "tab('reviews')", "reviews", show=False),
        Binding("4", "tab('library')", "library", show=False),
        Binding("5", "tab('projects')", "projects", show=False),
        # inbox
        Binding("y", "triage('keep')", "keep (read)"),  # on the reading list: mark read
        Binding("n", "triage('skip')", "skip"),
        Binding("l", "triage('later')", "reading list"),
        Binding("a", "accept", "accept suggestion"),
        Binding("p", "keep_for_project", "keep for project"),
        Binding("f", "fetch", "fetch"),
        Binding("u", "toggle_viewed", "new/seen"),
        # library
        Binding("e", "edit_notes", "edit notes"),
        Binding("s", "status", "status"),
        Binding("d", "pdf", "PDF"),
        Binding("x", "remove", "remove"),
        Binding("S", "sort", "sort"),
        # shared
        Binding("t", "tag", "tag"),
        Binding("o", "open_url", "open URL"),
        # projects
        Binding("c", "check", "check"),
        Binding("m", "missing_pdfs", "missing PDFs"),
    ]
    TAB_ACTIONS: ClassVar[dict[str, set[str]]] = {
        "inbox": {"triage", "accept", "keep_for_project", "fetch", "toggle_viewed", "tag", "open_url"},
        "reading": {"triage", "open_paper", "edit_notes", "status", "pdf", "remove", "sort", "tag", "open_url"},
        "reviews": {"triage", "open_url", "sort"},
        "library": {"open_paper", "edit_notes", "status", "pdf", "remove", "sort", "tag", "open_url"},
        "projects": {"check", "missing_pdfs"},
    }

    def __init__(self, lib: Library) -> None:
        super().__init__()
        self.lib = lib
        self.items: list[Item] = []
        self.reading: list[Paper] = []  # kept, not read yet (UNREAD statuses)
        self.papers: list[Paper] = []  # read: the Library tab
        self.filters = {"inbox": "", "reading": "", "reviews": "", "library": ""}
        self.reviews: list[Review] = []
        self.all_reviews: list[Review] = []
        self.sort = SORTS[0]
        self.review_sort = REVIEW_SORTS[0]
        self.viewed: set[str] = set()
        self._view_timer: Timer | None = None
        self._detail_timer: Timer | None = None
        self._shown: dict[str, tuple[str | None, tuple[str, ...]]] = {}  # each pane's content: skip re-renders
        self._kept_new: str | None = None  # marked new with `u`: not re-marked until the cursor leaves it
        self._seen = fingerprint(lib.root)
        self._claims: dict[tuple[str, ...], tuple[tuple[int, ...], list[Path], list[claimsmod.Claim]]] = {}
        self.sub_title = str(lib.root)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(id="filter", placeholder="filter: words, t:TAG p:PROJECT s:STATUS f:FEED (enter to apply)")
        with TabbedContent(initial="inbox", id="tabs"):
            with TabPane("Inbox", id="inbox"), Horizontal(classes="pane"):
                yield Table(id="inbox-table", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(classes="detail"):
                    yield Static(id="inbox-empty", classes="empty")
                    yield Markdown(id="inbox-detail")
            with TabPane("Reading list", id="reading"), Horizontal(classes="pane"):
                yield Table(id="reading-table", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(classes="detail"):
                    yield Static(id="reading-empty", classes="empty")
                    yield Markdown(id="reading-detail")
            with TabPane("Reviews", id="reviews"), Horizontal(classes="pane"):
                yield Table(id="reviews-table", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(classes="detail"):
                    yield Static(id="reviews-empty", classes="empty")
                    yield Markdown(id="reviews-detail")
            with TabPane("Library", id="library"), Horizontal(classes="pane"):
                yield Table(id="library-table", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(classes="detail"):
                    yield Static(id="library-empty", classes="empty")
                    yield Markdown(id="library-detail")
            with TabPane("Projects", id="projects"), Horizontal(classes="pane"):
                yield OptionList(id="project-list")
                with VerticalScroll(classes="detail"):
                    yield Static(id="project-empty", classes="empty")
                    yield Markdown(id="project-detail")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#inbox-table", Table).add_columns("", "year", "feed", "title")
        self.query_one("#reviews-table", Table).add_columns("created", "papers", "title")
        for table in ("#reading-table", "#library-table"):
            self.query_one(table, Table).add_columns("key", "year", "status", "tags", "title")
        self.reload()
        self.show_details(*PANES)
        self.query_one("#inbox-table", Table).focus()
        self.set_interval(POLL_SECONDS, self.poll)
        self.start_view_timer()

    # --- state -----------------------------------------------------------------------------------

    @property
    def tab(self) -> str:
        return self.query_one("#tabs", TabbedContent).active or "inbox"

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Tab-specific actions are only offered (and shown in the footer) on their tab. On the reading
        list and a paper's screen, `y` (keep) marks the paper read; skip and later are inbox-only."""
        if isinstance(self.screen, ClaimsScreen):
            return action in ("missing_pdfs", "quit", "reload")
        if isinstance(self.screen, PaperScreen):
            if action == "triage":
                return parameters == ("keep",) and self.lib.get(self.screen.key).status != "read"
            return action not in self.TAB_ACTIONS["inbox"] or action in self.TAB_ACTIONS["library"]
        if action == "triage" and self.tab == "reading":
            return parameters == ("keep",)
        if action == "triage" and self.tab == "reviews":  # y keeps a review, n discards it
            return parameters in (("keep",), ("skip",))
        owners = [tab for tab, actions in self.TAB_ACTIONS.items() if action in actions]
        return not owners or self.tab in owners

    def poll(self) -> None:
        now = fingerprint(self.lib.root)
        if now != self._seen:
            self.reload()

    def reload(self) -> None:
        self._seen = fingerprint(self.lib.root)
        try:
            self.lib = Library.open(self.lib.root, auto_commit=self.lib.auto_commit)
            self.items = filter_items(Inbox(self.lib.root).items(), self.filters["inbox"])
            self.viewed = read_viewed(self.lib.root)
            papers = list(self.lib.papers())
            self.all_reviews = Reviews(self.lib.root).all(self.review_sort)
            self.reviews = filter_reviews(self.all_reviews, self.filters["reviews"])
            unread = [p for p in papers if p.status in UNREAD]
            filed = filed_times(self.lib.root) if needs_filed(self.sort) else lambda p: 0.0
            self.reading = filter_papers(unread, self.filters["reading"], self.sort, filed)
            self.papers = filter_papers([p for p in papers if p not in unread], self.filters["library"],
                                        self.sort, filed)
        except RuruError as e:
            self.notify(str(e), severity="error")
            return
        self.query_one("#inbox-table", Table).refill([
            (i.id, [SUGGESTION_MARK.get((i.suggestion or {}).get("action", ""), ""), str(i.paper.year or ""),
                    i.paper.source.removeprefix("feed:"), i.paper.title])
            for i in self.items], bold={i.id for i in self.items} - self.viewed)
        for table, papers in (("#reading-table", self.reading), ("#library-table", self.papers)):
            self.query_one(table, Table).refill([
                (p.key, [p.key, str(p.year or ""), p.status, " ".join(p.tags), p.title]) for p in papers])
        self.query_one("#reviews-table", Table).refill(
            [(r.id, [created_label(r), str(len(r.papers)), r.title]) for r in self.reviews],
            bold={r.id for r in self.reviews if r.status == "new"})
        names = list(projmod.load(self.lib.settings.projects))
        plist = self.query_one("#project-list", OptionList)
        highlighted = plist.highlighted
        plist.clear_options()
        plist.add_options(names)
        if names:
            plist.highlighted = min(highlighted or 0, len(names) - 1)
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.get_tab("inbox").label = f"Inbox ({len(self.items)}{self._new_count()})"
        by = f" by {self.sort}" if self.sort != SORTS[0] else ""
        tabs.get_tab("reading").label = f"Reading list ({len(self.reading)}){by}"
        new = sum(1 for r in self.reviews if r.status == "new")
        rby = f" by {self.review_sort}" if self.review_sort != REVIEW_SORTS[0] else ""
        tabs.get_tab("reviews").label = f"Reviews ({len(self.reviews)}{f', {new} new' if new else ''}){rby}"
        tabs.get_tab("library").label = f"Library ({len(self.papers)}){by}"
        self.show_details()
        if isinstance(self.screen, PaperScreen):
            self.screen.refresh_paper()
        if isinstance(self.screen, ClaimsScreen):
            self.screen.refresh_claims()

    def current_item(self) -> Item | None:
        key = self.query_one("#inbox-table", Table).current_key()
        return next((i for i in self.items if i.id == key), None)

    def current_review(self) -> Review | None:
        rid = self.query_one("#reviews-table", Table).current_key()
        return next((r for r in self.reviews if r.id == rid), None)

    def current_key(self) -> str | None:
        if isinstance(self.screen, PaperScreen):
            return self.screen.key
        return self.query_one(f"#{'reading' if self.tab == 'reading' else 'library'}-table", Table).current_key()

    def _detail(self, tab: str, markdown: str | None, *empty: str) -> None:
        """Show `markdown` in the tab's detail pane, or the mascot with the `empty` message."""
        if self._shown.get(tab) == (markdown, empty):
            return
        self._shown[tab] = (markdown, empty)
        md = self.query_one(f"#{tab}-detail", Markdown)
        placeholder = self.query_one(f"#{tab}-empty", Static)
        if markdown is None:
            placeholder.update(logo.rich_text(logo.SMALL, *empty))
        else:
            md.update(markdown)
        placeholder.display = markdown is None
        md.display = markdown is not None

    @property
    def pane(self) -> str:
        return "project" if self.tab == "projects" else self.tab

    def show_details(self, *panes: str) -> None:
        """Render the detail panes named (default: the visible one); a hidden pane catches up when its
        tab is shown, so moving the cursor costs one render (the project pane also scans the .tex)."""
        panes = panes or (self.pane,)
        if "inbox" in panes:
            item = self.current_item()
            self._detail("inbox", item_markdown(item) if item else None,
                         "Inbox empty." if not self.filters["inbox"] else "Nothing matches the filter.",
                         "f fetches the feeds.")
        for tab, empty, hint in (
                ("reading", "Nothing to read.", "l in the inbox puts a paper here; y marks it read."),
                ("library", "The library is empty.", "y keeps a paper you've read, or `ruru add DOI`.")):
            if tab not in panes:
                continue
            key = self.query_one(f"#{tab}-table", Table).current_key()
            self._detail(tab, paper_markdown(self.lib.get(key), self.lib.has_pdf(key),
                                             [r for r in self.all_reviews if key in r.papers]) if key else None,
                         "No papers match." if self.filters[tab] else empty, hint)
        if "reviews" in panes:
            review = self.current_review()
            self._detail("reviews", review_markdown(self.lib, review) if review else None,
                         "No reviews." if not self.filters["reviews"] else "Nothing matches the filter.",
                         "Ask Claude Code for a review of related reading-list papers (it needs their PDFs).")
        if "project" in panes:
            plist = self.query_one("#project-list", OptionList)
            projects = projmod.load(self.lib.settings.projects)
            names = list(projects)
            if plist.highlighted is not None and names:
                project = projects[names[plist.highlighted]]
                claims = None
                if project.tex:
                    with contextlib.suppress(RuruError, OSError):
                        claims = (self.project_claims(project.name), claimsmod.load(self.lib.root, project.name))
                self._detail("project", project_markdown(self.lib, project, claims))
            else:
                self._detail("project", None, "No projects yet.",
                             "ruru project add NAME --path … --bib … --tex …")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Holding j fires this per row: render the pane once the cursor settles, not for every row."""
        if self._detail_timer:
            self._detail_timer.stop()
        pane = str(event.data_table.id).removesuffix("-table")
        self._detail_timer = self.set_timer(DETAIL_SECONDS, lambda: self.show_details(pane))
        if event.data_table.id == "inbox-table":
            self.start_view_timer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "project-list":
            name = self.current_project()
            if name and projmod.load(self.lib.settings.projects)[name].tex:
                self.push_screen(ClaimsScreen(name))
            elif name:
                self.notify(f"{name} lists no tex files: add it again with --tex", severity="warning")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "project-list":
            self.show_details("project")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        target = {"inbox": "#inbox-table", "reading": "#reading-table", "reviews": "#reviews-table",
                  "library": "#library-table", "projects": "#project-list"}
        self.query_one(target[self.tab]).focus()
        self.show_details()
        self.refresh_bindings()
        self.start_view_timer()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id in ("reading-table", "library-table"):
            self.action_open_paper()
        elif event.data_table.id == "reviews-table":
            self.action_open_url()

    # --- helpers ---------------------------------------------------------------------------------

    def _new_count(self) -> str:
        new = sum(1 for i in self.items if i.id not in self.viewed)
        return f", {new} new" if new else ""

    def start_view_timer(self) -> None:
        """Mark the item under the cursor viewed if it is still there after VIEW_SECONDS."""
        if self._view_timer:
            self._view_timer.stop()
        item = self.current_item()
        if item and item.id != self._kept_new:
            self._kept_new = None
        if item and item.id not in self.viewed and item.id != self._kept_new:
            self._view_timer = self.set_timer(VIEW_SECONDS, lambda: self._looked_at(item.id))

    def _looked_at(self, item_id: str) -> None:
        """Still on it after VIEW_SECONDS, on the inbox tab: Jesse has seen it."""
        item = self.current_item()
        if self.tab == "inbox" and item and item.id == item_id and not isinstance(self.screen, ModalScreen):
            self.set_viewed(item_id, True)

    def set_viewed(self, item_id: str, viewed: bool) -> None:
        current = read_viewed(self.lib.root)
        write_viewed(self.lib.root, current | {item_id} if viewed else current - {item_id})
        self.reload()

    def current_project(self) -> str | None:
        if isinstance(self.screen, ClaimsScreen):
            return self.screen.project
        names = list(projmod.load(self.lib.settings.projects))
        highlighted = self.query_one("#project-list", OptionList).highlighted
        return names[highlighted] if highlighted is not None and names else None

    def project_claims(self, name: str) -> list[claimsmod.Claim]:
        """`ops.claims`, re-read only when one of the .tex files it came from has changed."""
        project = ops.get_project(self.lib, name)
        key = (name, *map(str, project.tex_paths))

        def stamp(files: list[Path]) -> tuple[int, ...]:
            return tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in files)
        cached = self._claims.get(key)
        if cached and cached[0] == stamp(cached[1]):
            return cached[2]
        claims, files = ops.claims_and_files(self.lib, name)
        self._claims[key] = (stamp(files), files, claims)
        return claims

    def attempt(self, fn: Callable[[], Any], done: str | None = None) -> bool:
        """Run a library operation; report errors instead of crashing; reload either way."""
        try:
            fn()
        except RuruError as e:
            self.notify(str(e), severity="error", timeout=8)
            return False
        finally:
            self.reload()
        if done:
            self.notify(done)
        return True

    def attempt_value(self, fn: Callable[[], Any]) -> Any:
        """`fn()`, or None after reporting its error."""
        try:
            return fn()
        except RuruError as e:
            self.notify(str(e), severity="error", timeout=8)
            return None

    @contextlib.contextmanager
    def suspended(self) -> Iterator[None]:
        try:
            with self.suspend():
                yield
        except SuspendNotSupported:  # headless (tests)
            yield

    # --- actions ---------------------------------------------------------------------------------

    def action_tab(self, tab: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab

    def action_reload(self) -> None:
        self.reload()
        self.notify("reloaded")

    def action_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.value = self.filters.get(self.tab, "")
        box.add_class("shown")
        box.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "filter":
            return
        if self.tab in self.filters:
            self.filters[self.tab] = event.value.strip()
        event.input.remove_class("shown")
        self.reload()
        self.on_tabbed_content_tab_activated(None)  # type: ignore[arg-type]  # refocus the table

    def action_triage(self, decision: str, tags: list[str] | None = None, project: str | None = None) -> None:
        if self.tab == "reviews" and not isinstance(self.screen, PaperScreen):
            review = self.current_review()
            if review and decision in ("keep", "skip"):
                verdict = "keep" if decision == "keep" else "discard"
                self.attempt(lambda: ops.triage_reviews(self.lib, [review.id], verdict),
                             f"{verdict}: {review.title[:60]}")
            return
        if isinstance(self.screen, PaperScreen) or self.tab == "reading":  # `y`: done reading
            key = self.current_key()
            if key and decision == "keep":
                self.attempt(lambda: ops.set_status(self.lib, [key], "read"), f"read: {key} (now in the Library)")
            return
        item = self.current_item()
        if item is None:
            return
        projects = [project] if project else []
        self.attempt(lambda: ops.triage(self.lib, [item.id], decision, tags=tags or [], projects=projects),
                     f"{decision}: {item.paper.title[:60]}")

    def action_toggle_viewed(self) -> None:
        item = self.current_item()
        if item is None:
            return
        viewed = item.id not in self.viewed
        self._kept_new = None if viewed else item.id  # don't re-mark it seen a moment later
        self.set_viewed(item.id, viewed)

    def action_accept(self) -> None:
        item = self.current_item()
        if item is None:
            return
        if not item.suggestion:
            self.notify("no suggestion on this item", severity="warning")
            return
        self.action_triage(str(item.suggestion["action"]))

    def action_tag(self) -> None:
        if self.tab == "inbox" and not isinstance(self.screen, PaperScreen):
            item = self.current_item()
            if item is None:
                return

            def keep_tagged(value: str | None) -> None:
                tags = [cli._tag(t) for t in (value or "").replace(",", " ").split()]
                if value is not None and tags:
                    self.action_triage("keep", tags=tags)
            self.push_screen(Prompt("Keep with tags (space-separated):", "gc-ms alignment"), keep_tagged)
            return
        key = self.current_key()
        if key is None:
            return

        def apply(value: str | None) -> None:
            words = (value or "").split()
            add = [cli._tag(w.lstrip("+")) for w in words if not w.startswith("-")]
            remove = [cli._tag(w[1:]) for w in words if w.startswith("-") and len(w) > 1]
            if add or remove:
                self.attempt(lambda: ops.tag(self.lib, [key], add, remove), f"tagged {key}")
        self.push_screen(Prompt(f"Tags for {key}: +tag to add, -tag to remove", "+gc-ms -todo"), apply)

    def action_keep_for_project(self) -> None:
        item = self.current_item()
        names = list(projmod.load(self.lib.settings.projects))
        if item is None:
            return
        if not names:
            self.notify("no projects yet: `ruru project add …`", severity="warning")
            return

        def chosen(name: str | None) -> None:
            if name:
                self.action_triage("keep", project=name)
        self.push_screen(Choose("Keep for which project?", names), chosen)

    def action_open_url(self) -> None:
        if self.tab == "reviews" and not isinstance(self.screen, PaperScreen):
            review = self.current_review()
            if review:
                path = self.attempt_value(lambda: cli.render_review_file(self.lib, review.id))
                if path:
                    webbrowser.open(path.as_uri())
            return
        if self.tab == "inbox" and not isinstance(self.screen, PaperScreen):
            item = self.current_item()
            paper = item.paper if item else None
        else:
            key = self.current_key()
            paper = self.lib.get(key) if key else None
        if paper is None:
            return
        url = paper.url or (f"https://doi.org/{paper.ids['doi']}" if paper.ids.get("doi") else None)
        if not url:
            self.notify("no URL for this paper", severity="warning")
            return
        webbrowser.open(url)

    def action_open_paper(self) -> None:
        key = self.current_key()
        if key:
            self.push_screen(PaperScreen(key))

    def action_edit_notes(self) -> None:
        key = self.current_key()
        if key is None:
            return
        paper = self.lib.get(key)
        with self.suspended():
            try:
                text = cli.edit_text(paper.notes)
            except RuruError as e:
                self.notify(str(e), severity="error")
                return
        self.attempt(lambda: ops.set_notes(self.lib, key, text), f"notes saved: {key}")

    def action_status(self) -> None:
        key = self.current_key()
        if key is None:
            return

        def chosen(status: str | None) -> None:
            if status:
                self.attempt(lambda: ops.set_status(self.lib, [key], status), f"{key}: {status}")
        self.push_screen(Choose(f"Status for {key}:", list(STATUSES)), chosen)

    def action_sort(self) -> None:
        """Cycle the order: on the reading list and library, added (newest first), key, year, status,
        title; on reviews (new ones always first), created, title, papers."""
        if self.tab == "reviews":
            self.review_sort = REVIEW_SORTS[(REVIEW_SORTS.index(self.review_sort) + 1) % len(REVIEW_SORTS)]
            chosen = self.review_sort
        else:
            self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
            chosen = self.sort
        self.reload()
        self.notify(f"sorted by {chosen}")

    def action_remove(self) -> None:
        key = self.current_key()
        if key is None:
            return
        title = self.lib.get(key).title
        yes = f"yes, remove {key}"

        def chosen(answer: str | None) -> None:
            if answer != yes:
                return
            if isinstance(self.screen, PaperScreen):
                self.pop_screen()
            self.attempt(lambda: ops.remove(self.lib, [key]), f"removed {key} (`git revert` brings it back)")
        self.push_screen(Choose(f"Remove {key} from the library?\n{title[:60]}\nIts PDF is deleted too.",
                                ["no, keep it", yes]), chosen)

    def action_pdf(self) -> None:
        key = self.current_key()
        if key is None:
            return
        if self.lib.has_pdf(key):
            open_file(self.lib.paper_dir(key) / "paper.pdf")
        else:
            self.notify(f"looking for an open-access PDF of {key}…")
            self.fetch_pdf(key)

    @work(thread=True, exclusive=True, group="network")
    def fetch_pdf(self, key: str) -> None:
        try:
            with cli.client_for(self.lib) as client:
                path = ops.fetch_pdf(self.lib, client, key)
        except RuruError as e:
            link = cli.browse_for_pdf(self.lib.get(key))
            message = (f"{e}. Opened {link}: save it as {key}.pdf (on the clipboard) in {cli.downloads_dir()}, "
                       "then `ruru pdf add`" if link else str(e))
            self.call_from_thread(self.notify, message, severity="warning" if link else "error", timeout=12)
            return
        self.call_from_thread(self.reload)
        self.call_from_thread(self.notify, f"saved {path.name} for {key}")
        open_file(path)

    def action_fetch(self) -> None:
        self.notify("fetching feeds…")
        self.fetch_feeds()

    @work(thread=True, exclusive=True, group="network")
    def fetch_feeds(self) -> None:
        try:
            feeds_ = cli.feeds.load(self.lib.settings.feeds)
            with cli.client_for(self.lib) as client:
                results, items = ops.fetch(self.lib, client, feeds_)
        except RuruError as e:
            self.call_from_thread(self.notify, str(e), severity="error", timeout=8)
            return
        failed = [r.name for r in results if r.error]
        self.call_from_thread(self.reload)
        message = f"{len(items)} new item(s)" + (f"; failed: {', '.join(failed)}" if failed else "")
        self.call_from_thread(self.notify, message, severity="warning" if failed else "information")

    def action_missing_pdfs(self) -> None:
        name = self.current_project()
        if name:
            self.notify(f"{name}: looking for the cited papers' PDFs…")
            self.get_missing_pdfs(name)

    @work(thread=True, exclusive=True, group="network")
    def get_missing_pdfs(self, name: str) -> None:
        """Download the open-access PDFs the project's claims need, then open the rest's DOI pages."""
        try:
            papers, unknown = cli.cited_papers(self.lib, name)
            before = [p for p in papers if not self.lib.has_pdf(p.key)]
            missing = cli.fetch_open_access(self.lib, before)
        except RuruError as e:
            self.call_from_thread(self.notify, str(e), severity="error", timeout=8)
            return
        opened = cli.open_links(missing)
        parts = [f"saved {len(before) - len(missing)} open-access PDF(s)"] if len(before) > len(missing) else []
        if opened:
            parts.append(f"opened {len(opened)} publisher page(s): save each as KEY.pdf in {cli.downloads_dir()}, "
                         "then a files them")
        if len(missing) > len(opened):
            parts.append(f"{len(missing) - len(opened)} more without a link or past the {cli.MAX_TABS}-tab limit")
        if unknown:
            parts.append(f"not in the library: {', '.join(unknown)}")
        self.call_from_thread(self.reload)
        self.call_from_thread(self.notify, "; ".join(parts) or "every cited paper has its PDF",
                              severity="warning" if missing or unknown else "information", timeout=12)

    def action_check(self) -> None:
        self.show_details()
        self.notify("checked")
