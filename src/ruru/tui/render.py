"""What the TUI shows, as plain functions: filters and Markdown for items, papers, reviews, projects and claims."""

from __future__ import annotations

from typing import NamedTuple

from ruru import claims as claimsmod
from ruru import ops
from ruru import projects as projmod
from ruru.cited import Cited
from ruru.errors import RuruError
from ruru.inbox import Item
from ruru.library import Library
from ruru.model import STATUSES, Paper
from ruru.rank import Relevance
from ruru.reviews import Review, created_label
from ruru.search import SORTS, Filed, in_project, search, sort_papers

PANES = ("inbox", "reading", "reviews", "library", "project")


SUGGESTION_MARK = {"keep": "+", "skip": "-", "later": "~"}  # an agent's suggestion, at a glance


Verdicts = dict[tuple[str, str], claimsmod.Verdict]


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


class ClaimsView(NamedTuple):
    """A project's claims with everything needed to show their state."""
    claims: list[claimsmod.Claim]
    verdicts: Verdicts
    own: set[str]
    versions: dict[str, str | None]  # cited drafts and software: their current version
    cited: dict[str, Cited]

    def state(self, c: claimsmod.Claim, k: str) -> str:
        return claimsmod.state(c, k, self.verdicts, self.versions)


def claims_view(lib: Library, name: str, claims: list[claimsmod.Claim]) -> ClaimsView:
    keys = list({k for c in claims for k in c.keys})
    return ClaimsView(claims, claimsmod.load(lib.root, name), ops.own_keys(lib), ops.cited_versions(lib, keys),
                      ops.cited_entries(lib))


def filter_claims(view: ClaimsView, text: str) -> list[claimsmod.Claim]:
    """Words in the sentence; k:KEY cites it; v:STATE has a key in that state (a verdict,
    unchecked, outdated; v:own: Jesse's own work)."""
    fields, words = parse_filter(text)
    folded = [w.lower() for w in words.split()]
    return [c for c in view.claims
            if (not fields.get("k") or fields["k"] in c.keys)
            and (not fields.get("v")
                 or claimsmod.matches(c, {fields["v"]}, view.verdicts, view.versions, view.own))
            and all(w in c.text.lower() for w in folded)]


def _mark(state: str) -> str:
    marks = {"unchecked": claimsmod.UNCHECKED, "outdated": claimsmod.OUTDATED}
    return marks.get(state) or claimsmod.MARKS[state][0]


def claim_marks(c: claimsmod.Claim, view: ClaimsView) -> str:
    return "".join(_mark(view.state(c, k)) for k in c.keys)


def _ids(p: Paper) -> str:
    return " · ".join(f"{k}: `{v}`" for k, v in p.ids.items() if v)


def item_markdown(item: Item, rel: Relevance | None = None, duplicate_of: str | None = None) -> str:
    p = item.paper
    lines = [f"## {p.title}", ", ".join(p.authors) or "*no authors listed*",
             " · ".join(str(x) for x in (p.year, p.venue) if x),
             f"`{item.id}` · {p.source} · fetched {item.fetched}"]
    if rel:
        like = ", ".join(f"`{k}` ({s:.2f})" for k, s in rel.similar)
        lines.append(f"**Match {rel.score:.2f}**" + (f" · like {like}" if like else ""))
    if duplicate_of:
        lines.append(f"> **Possible duplicate** of `{duplicate_of}`")
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


def _what(lib: Library, k: str, view: ClaimsView) -> str:
    """How a cited key is described: a paper's title, or the draft/software it is."""
    if lib.exists(k):
        return f" — {lib.get(k).title}"
    entry = view.cited.get(k)
    if entry is None:
        return " — *not in the library*"
    where = f"project `{entry.project}`" if entry.kind == "draft" else f"`{entry.path or entry.link}`"
    return f" — {entry.kind}, {where}"


def claim_markdown(lib: Library, c: claimsmod.Claim, view: ClaimsView, place: str) -> str:
    lines = [f"### {place}:{c.line}", "> " + c.text, f"`{c.id}`"]
    for k in c.keys:
        mine = f" {claimsmod.OWN} *your own work*" if k in view.own else ""
        state = view.state(c, k)
        lines.append(f"#### {_mark(state)} `{k}`{_what(lib, k, view)}{mine}")
        v = view.verdicts.get((c.id, k))
        if v is None:
            why = ("" if lib.has_pdf(k) else " · no PDF (m gets the missing ones)") if lib.exists(k) \
                else "" if k in view.cited else " · `ruru add` it, or `ruru cited add` a draft or software"
            lines.append(f"*unchecked*{why} · v records a verdict")
            continue
        if state == "outdated":
            lines.append(f"*outdated: the {view.cited[k].kind} has changed since this verdict "
                         f"({v.version} → {view.versions.get(k)}); v judges it again*")
        where = f"{'p. ' if v.source in ('pdf', 'abstract') else ''}{v.page}, " if v.page else ""
        lines.append(f"**{v.verdict}** ({where}from {v.source}{', by ' + v.by if v.by else ''}, {v.date})")
        lines += [f"> {v.quote}"] if v.quote else []
        lines += [v.note] if v.note else []
    return "\n\n".join(lines)


def claims_summary(lib: Library, project: projmod.Project, view: ClaimsView) -> list[str]:
    """The project pane's Claims section: counts, the problems in full, what's left and why."""
    summary = claimsmod.summarise(view.claims, view.verdicts, view.own, lib.exists, lib.has_pdf, view.versions,
                                  set(view.cited))
    n = summary.counts
    lines = [f"### Claims ({summary.claims} sentences citing {len(summary.keys)} keys)",
             " · ".join([f"{claimsmod.MARKS[v][0]} {v} {n[v]}" for v in claimsmod.VERDICTS if n[v]]
                        + [f"{claimsmod.UNCHECKED} unchecked {n['unchecked']}"]
                        + ([f"{claimsmod.OUTDATED} outdated {n['outdated']}"] if n["outdated"] else [])
                        + ([f"{claimsmod.OWN} citing your work {n['own']}"] if n["own"] else []))]
    for c, v in summary.problems:
        detail = " ".join(x for x in (f"“{v.quote}”" if v.quote else "", v.note or "") if x)
        lines.append(f"- {claimsmod.MARKS[v.verdict][0]} **{v.verdict}** `{v.key}` "
                     f"{claimsmod.where(c, project.tex_paths)}:{c.line}: {detail}".rstrip(": "))
    if summary.no_pdf:
        lines.append(f"**No PDF ({len(summary.no_pdf)}):** " + ", ".join(f"`{k}`" for k in summary.no_pdf)
                     + " · m gets them")
    if summary.not_in_library:
        lines.append("**Not in the library:** " + ", ".join(f"`{k}`" for k in summary.not_in_library))
    lines.append("*enter: every claim, with its verdicts*")
    return lines


def project_markdown(lib: Library, project: projmod.Project, claims: ClaimsView | None = None) -> str:
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
        lines += claims_summary(lib, project, claims)
    return "\n\n".join(lines)
