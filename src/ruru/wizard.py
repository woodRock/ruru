"""`ruru feeds wizard`: interactive feed setup. The one ruru command that prompts.

Every feed it creates goes through `add_feed` (the same path as `ruru feeds add`), so the wizard
only helps find the right ids: arXiv categories, a journal's ISSN, authors' OpenAlex ids.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ruru.errors import RuruError
from ruru.feeds import Feed
from ruru.http import Client
from ruru.resolve import openalex
from ruru.text import words

ARXIV_CATEGORIES = [
    ("physics.chem-ph", "Chemical Physics"),
    ("physics.data-an", "Data Analysis, Statistics and Probability"),
    ("q-bio.QM", "Quantitative Methods"),
    ("cs.LG", "Machine Learning"),
    ("stat.ML", "Machine Learning (statistics)"),
    ("cs.NE", "Neural and Evolutionary Computing"),
    ("eess.SP", "Signal Processing"),
    ("cs.AI", "Artificial Intelligence"),
]

MENU = """
What would you like to follow?
  1  arXiv categories, optionally narrowed by keywords
  2  a journal
  3  authors
  4  a topic: a phrase in titles and abstracts, across all journals (OpenAlex)
  5  an RSS or Atom feed URL
  l  list current feeds
  q  done
"""


class Done(Exception):
    """The user ended the wizard (q or end of input)."""


def slug(text: str, limit: int = 30) -> str:
    return "-".join(words(text))[:limit].strip("-") or "feed"


class Wizard:
    def __init__(self, client: Client, existing: list[Feed], add_feed: Callable[[Feed], None],
                 ask: Callable[[str], str] | None = None, say: Callable[[str], None] = print) -> None:
        self.client = client
        self.names = {f.name for f in existing}
        self.existing = existing
        self.add_feed = add_feed
        self._ask = ask or (lambda prompt: input(prompt))  # looked up per call, not at import
        self.say = say

    def ask(self, prompt: str, default: str = "") -> str:
        try:
            answer = self._ask(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
        except EOFError:
            raise Done from None
        return answer or default

    def choose(self, prompt: str, n: int, *, many: bool = False) -> list[int]:
        """Numbers 1..n (comma-separated if `many`); [] for a blank answer."""
        while True:
            answer = self.ask(prompt)
            if not answer:
                return []
            try:
                picks = [int(x) for x in re.split(r"[,\s]+", answer) if x]
            except ValueError:
                picks = []
            if picks and all(1 <= p <= n for p in picks) and (many or len(picks) == 1):
                return [p - 1 for p in picks]
            self.say(f"  enter {'numbers' if many else 'a number'} from 1 to {n}, or leave blank")

    def unique_name(self, default: str) -> str:
        suggestion, n = default, 2
        while suggestion in self.names:
            suggestion, n = f"{default}-{n}", n + 1
        while True:
            name = slug(self.ask("Feed name", suggestion), 40)
            if name not in self.names:
                return name
            self.say(f"  there is already a feed named {name!r}")

    def save(self, feed: Feed) -> None:
        if self.ask(f"Add feed {feed.name!r} ({feed.describe()})? y/n", "y").lower().startswith("y"):
            self.add_feed(feed)
            self.names.add(feed.name)
            self.existing.append(feed)
            self.say(f"  added {feed.name!r}")

    # --- flows -----------------------------------------------------------------------------------

    def arxiv(self) -> None:
        for n, (cat, label) in enumerate(ARXIV_CATEGORIES, 1):
            self.say(f"  {n}  {cat:<16} {label}")
        answer = self.ask("Categories: numbers or arXiv names, comma-separated")
        cats = []
        for part in (p.strip() for p in answer.split(",") if p.strip()):
            if part.isdigit() and 1 <= int(part) <= len(ARXIV_CATEGORIES):
                cats.append(ARXIV_CATEGORIES[int(part) - 1][0])
            elif re.match(r"^[a-z\-]+(\.[A-Za-z\-]+)?$", part):
                cats.append(part)
            else:
                self.say(f"  skipping {part!r}: not a category")
        if not cats:
            return
        terms = [t.strip() for t in self.ask("Only abstracts mentioning (comma-separated; blank for everything)")
                 .split(",") if t.strip()]
        query = " OR ".join(f"cat:{c}" for c in cats)
        if len(cats) > 1 and terms:
            query = f"({query})"
        if terms:
            ors = " OR ".join(f'abs:"{t}"' if " " in t else f"abs:{t}" for t in terms)
            query += f" AND ({ors})" if len(terms) > 1 else f" AND {ors}"
        default = "arxiv-" + slug(cats[0].split(".")[-1], 12) + (f"-{slug(terms[0], 20)}" if terms else "")
        self.save(Feed(name=self.unique_name(default), kind="arxiv", query=query))

    def journal(self) -> None:
        name = self.ask("Journal name")
        if not name:
            return
        sources = [s for s in openalex.search_sources(self.client, name) if s.get("type") in (None, "journal")]
        if not sources:
            self.say("  no journal found by that name")
            return
        for n, s in enumerate(sources, 1):
            publisher = s.get("host_organization_name") or ""
            self.say(f"  {n}  {s['display_name']}  (ISSN {s.get('issn_l') or '-'}, "
                     f"{s.get('works_count', 0):,} works{', ' + publisher if publisher else ''})")
        picks = self.choose("Which one? (blank to cancel)", len(sources))
        if not picks:
            return
        s = sources[picks[0]]
        feed_name = self.unique_name(slug(s["display_name"]))
        if s.get("issn_l"):
            feed = Feed(name=feed_name, kind="crossref", issn=s["issn_l"], title=s["display_name"])
        else:
            source_id = s["id"].rsplit("/", 1)[-1]
            feed = Feed(name=feed_name, kind="openalex", filter=f"primary_location.source.id:{source_id}",
                        title=s["display_name"])
        self.save(feed)

    @staticmethod
    def _author_line(a: dict[str, Any]) -> str:
        places = [i["display_name"] for i in (a.get("last_known_institutions") or [])][:2]
        topics = [t["display_name"] for t in (a.get("topics") or [])][:1]
        extra = "; ".join(x for x in (", ".join(places), *topics) if x)
        return f"{a['display_name']}  ({a.get('works_count', 0):,} works{'; ' + extra if extra else ''})"

    def authors(self) -> None:
        chosen: list[dict[str, Any]] = []
        prompt = "Author name"
        while name := self.ask(prompt):
            found = openalex.search_authors(self.client, name)
            if not found:
                self.say("  nobody found by that name")
            for n, a in enumerate(found, 1):
                self.say(f"  {n}  {self._author_line(a)}")
            chosen += [found[i] for i in self.choose("Which? numbers, comma-separated (blank for none)",
                                                     len(found), many=True)] if found else []
            prompt = "Another author to add to this feed (blank to finish)"
        if not chosen:
            return
        ids = "|".join(a["id"].rsplit("/", 1)[-1] for a in chosen)
        default = f"author-{slug(chosen[0]['display_name'].split()[-1])}" if len(chosen) == 1 else "authors"
        title = ", ".join(a["display_name"] for a in chosen)
        self.save(Feed(name=self.unique_name(default), kind="openalex", filter=f"authorships.author.id:{ids}",
                       title=title))

    def topic(self) -> None:
        phrase = self.ask("Phrase (matched in titles and abstracts)")
        if phrase:
            self.save(Feed(name=self.unique_name(slug(phrase)), kind="openalex", search=phrase))

    def rss(self) -> None:
        url = self.ask("Feed URL")
        if not url:
            return
        if not re.match(r"^https?://", url):
            self.say("  that is not an http(s) URL")
            return
        self.save(Feed(name=self.unique_name(slug(re.sub(r"^https?://(www\.)?", "", url))), kind="rss", url=url))

    def list(self) -> None:
        if not self.existing:
            self.say("  no feeds yet")
        for f in self.existing:
            self.say(f"  {f.name:<24} {f.describe()}")

    def run(self) -> None:
        flows = {"1": self.arxiv, "2": self.journal, "3": self.authors, "4": self.topic, "5": self.rss,
                 "l": self.list}
        self.say("ruru feeds wizard: answers are saved to ruru.toml as you go; q or Ctrl-D to finish.")
        self.list()
        try:
            while True:
                self.say(MENU)
                choice = self.ask(">").lower()
                if choice in ("q", "quit", "done"):
                    break
                if choice not in flows:
                    self.say("  pick 1-5, l or q")
                    continue
                try:
                    flows[choice]()
                except RuruError as e:
                    self.say(f"  error: {e}")
        except Done:
            pass
        self.say("\nDone. Run `ruru fetch` to fill the inbox, then `ruru inbox`.")
