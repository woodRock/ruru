"""A review as one self-contained, readable HTML page (`ruru review open`, the TUI's Reviews tab).

The Markdown body is rendered with raw HTML disabled and `[@key]` citations become links to the
paper cards at the end (with Jesse's notes and the abstract, folded). TeX between `$…$` (inline)
or `$$…$$` (display) becomes MathML, which browsers draw natively. No scripts, no external assets:
the page works offline and prints well.
"""

from __future__ import annotations

from html import escape

from latex2mathml.converter import convert as tex_to_mathml
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin

from ruru.library import Library
from ruru.model import Paper
from ruru.reviews import Review, citations
from ruru.text import split_name


def _math(tex: str, options: dict[str, object]) -> str:
    try:
        return tex_to_mathml(tex.strip(), display="block" if options.get("display_mode") else "inline")
    except Exception:  # latex2mathml raises assorted errors on TeX it can't parse: show the source instead
        return f'<code class="math-error" title="TeX not rendered">{escape(tex)}</code>'


# `$5 and $6` stays text (allow_digits=False); `$$…$$` inside a paragraph is display math.
_md = (MarkdownIt("commonmark", {"html": False, "typographer": True})
       .use(dollarmath_plugin, allow_labels=False, allow_digits=False, double_inline=True, renderer=_math)
       .enable(["table", "strikethrough"]))

def author_label(p: Paper) -> str:
    families = [split_name(a)[1] for a in p.authors] or [p.key]
    names = families[0] if len(families) == 1 else (
        f"{families[0]} & {families[1]}" if len(families) == 2 else f"{families[0]} et al.")
    return f"{names} {p.year or 'n.d.'}"


def _link(p: Paper) -> str:
    label = author_label(p).replace("[", "(").replace("]", ")")
    title = p.title.replace('"', "'").replace("\\", "")
    return f'[{label}](#p-{p.key} "{title}")'


def _with_links(body: str, papers: dict[str, Paper]) -> str:
    out, last = [], 0
    for match, keys in citations(body):
        links = [_link(papers[k]) if k in papers else f"`{k}`" for k in keys]
        out += [body[last:match.start()], "(" + "; ".join(links) + ")"]
        last = match.end()
    return "".join(out) + body[last:]


def _card(p: Paper, has_pdf: bool) -> str:
    authors = ", ".join(p.authors[:6]) + (" et al." if len(p.authors) > 6 else "")
    where = " · ".join(escape(str(x)) for x in (p.venue, p.year) if x)
    doi = p.ids.get("doi")
    link = f"https://doi.org/{doi}" if doi else p.url
    anchor = f' · <a href="{escape(link)}">{escape(doi or "link")}</a>' if link else ""
    chips = [f'<span class="chip status-{escape(p.status)}">{escape(p.status)}</span>']
    if has_pdf:
        chips.append('<span class="chip">PDF</span>')
    chips += [f'<span class="chip tag">#{escape(t)}</span>' for t in p.tags]
    parts = [
        f'<li class="card" id="p-{escape(p.key)}">',
        f'<p class="card-title">{escape(p.title)}</p>',
        f'<p class="card-meta">{escape(authors) or "<em>no authors listed</em>"}</p>',
        f'<p class="card-meta">{where}{anchor}</p>',
        f'<p class="chips"><code>{escape(p.key)}</code>{"".join(chips)}</p>',
    ]
    if p.notes.strip():
        parts.append(f'<details open><summary>Your notes</summary>{_md.render(p.notes)}</details>')
    if p.abstract:
        parts.append(f"<details><summary>Abstract</summary><p>{escape(p.abstract)}</p></details>")
    return "\n".join(parts) + "\n</li>"


def render(lib: Library, review: Review) -> str:
    papers = {k: lib.get(k) for k in review.papers if (lib.paper_dir(k) / "paper.md").exists()}
    meta = " · ".join(x for x in (
        review.created.isoformat() if review.created else None,
        f"by {review.by}" if review.by else None,
        f"{len(review.papers)} paper{'s' if len(review.papers) != 1 else ''}",
        "new" if review.status == "new" else "kept",
    ) if x)
    tags = "".join(f'<span class="chip tag">#{escape(t)}</span>' for t in review.tags)
    cards = "\n".join(_card(papers[k], lib.has_pdf(k)) for k in review.papers if k in papers)
    missing = [k for k in review.papers if k not in papers]
    if missing:
        cards += "\n" + "\n".join(f'<li class="card gone" id="p-{escape(k)}"><code>{escape(k)}</code> '
                                  "is no longer in the library</li>" for k in missing)
    return PAGE.format(
        title=escape(review.title), meta=escape(meta), tags=tags,
        body=_md.render(_with_links(review.body, papers))
        .replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>"),
        cards=cards, id=escape(review.id),
    )


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --bg: #fbfaf7; --fg: #22201c; --muted: #6b665d; --rule: #e4e0d8; --card: #ffffff;
  --accent: #2f5d8a; --chip: #efece5;
  --to-read: #9a6700; --later: #6b665d; --reading: #1a7f8e; --read: #2e7d32;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #181715; --fg: #e8e4dc; --muted: #a09a8f; --rule: #34312c; --card: #211f1c;
    --accent: #8fb8e0; --chip: #2c2a26;
    --to-read: #e3b341; --later: #a09a8f; --reading: #56c2d0; --read: #7cc47f;
  }}
}}
* {{ box-sizing: border-box; }}
html {{ -webkit-text-size-adjust: 100%; }}
body {{
  margin: 0; background: var(--bg); color: var(--fg);
  font: 18px/1.65 Charter, "Iowan Old Style", "Source Serif Pro", Georgia, serif;
}}
main {{ max-width: 42rem; margin: 0 auto; padding: 3.5rem 1.25rem 5rem; }}
header {{ border-bottom: 1px solid var(--rule); padding-bottom: 1.25rem; margin-bottom: 2rem; }}
.kicker, .meta, .chips, .card-meta, summary, footer, th {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", sans-serif;
}}
.kicker {{ font-size: .75rem; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); margin: 0; }}
h1 {{ font-size: 2rem; line-height: 1.2; margin: .4rem 0 .6rem; letter-spacing: -.01em; }}
.meta {{ color: var(--muted); font-size: .85rem; margin: 0 0 .6rem; }}
h2 {{ font-size: 1.3rem; margin: 2.4rem 0 .6rem; }}
h3 {{ font-size: 1.05rem; margin: 1.8rem 0 .4rem; }}
p, ul, ol {{ margin: 0 0 1rem; }}
a {{ color: var(--accent); text-underline-offset: .15em; }}
a[href^="#p-"] {{ text-decoration: none; border-bottom: 1px dotted var(--accent); white-space: nowrap; }}
blockquote {{ margin: 1.2rem 0; padding: .2rem 1rem; border-left: 3px solid var(--rule); color: var(--muted); }}
code {{ font: .82em ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--chip); padding: .1em .35em;
  border-radius: 4px; }}
table {{ width: 100%; border-collapse: collapse; margin: 1.2rem 0 1.6rem; font-size: .9rem; line-height: 1.45; }}
.table-wrap {{ overflow-x: auto; }}
th, td {{ text-align: left; vertical-align: top; padding: .5rem .6rem; border-bottom: 1px solid var(--rule); }}
th {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); }}
.chips {{ display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; margin: .5rem 0 0;
  font-size: .75rem; }}
.chip {{ background: var(--chip); color: var(--muted); border-radius: 999px; padding: .1rem .6rem; }}
.status-to-read {{ color: var(--to-read); }} .status-later {{ color: var(--later); }}
.status-reading {{ color: var(--reading); }} .status-read {{ color: var(--read); }}
.papers {{ list-style: none; padding: 0; margin: 0; display: grid; gap: .9rem; }}
.card {{ background: var(--card); border: 1px solid var(--rule); border-radius: 10px; padding: 1rem 1.1rem;
  scroll-margin-top: 1rem; }}
.card:target {{ border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent); }}
.card p {{ margin: 0; }}
.card-title {{ font-weight: 600; line-height: 1.35; }}
.card-meta {{ color: var(--muted); font-size: .82rem; margin-top: .2rem !important; }}
.card.gone {{ color: var(--muted); font-size: .9rem; }}
math {{ font-family: "STIX Two Math", "Cambria Math", "Latin Modern Math", math; font-size: 1.05em; }}
.math.block {{ overflow-x: auto; margin: 1.1rem 0; text-align: center; }}
.math-error {{ color: var(--muted); }}
details {{ margin-top: .6rem; font-size: .92rem; }}
summary {{ cursor: pointer; color: var(--muted); font-size: .8rem; }}
details p {{ margin: .4rem 0 0; }}
footer {{ margin-top: 3rem; color: var(--muted); font-size: .75rem; }}
@media print {{ body {{ font-size: 11pt; }} main {{ padding: 0; }} details {{ display: none; }} }}
</style>
</head>
<body>
<main>
<header>
<p class="kicker">ruru review</p>
<h1>{title}</h1>
<p class="meta">{meta}</p>
<p class="chips">{tags}</p>
</header>
<article>
{body}
</article>
<h2>Papers</h2>
<ul class="papers">
{cards}
</ul>
<footer><code>reviews/{id}.md</code></footer>
</main>
</body>
</html>
"""
