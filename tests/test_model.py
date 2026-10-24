from datetime import date

import pytest

from ruru import model
from ruru.model import Paper

EXAMPLE = """\
---
key: wood2026joint
title: Joint Retention-Time Alignment of Many GC–MS Batches
authors: [Jesse Wood, Bach Nguyen, Bing Xue, Mengjie Zhang]
year: 2026
venue: IEEE Transactions on Artificial Intelligence
type: article
volume: '7'
issue: null
pages: 1-12
publisher: null
ids: {doi: 10.1109/tai.2026.1, arxiv: null, openalex: null, s2: null}
url: null
pdf_url: null
abstract: 'Ngā mihi: alignment of "many" batches.'
status: to-read
tags: [gc-ms, alignment]
projects: []
added: 2026-10-17
read_on: null
source: feed:arxiv-chem
custom_field: kept
---

## Notes

My own notes.

## Agent notes

<!-- ruru: agent-written; each entry dated and attributed -->

### 2026-10-17 — summary (agent)

A summary.
"""


def test_round_trip_is_exact():
    p = model.loads(EXAMPLE)
    assert p.added == date(2026, 10, 17)
    assert p.volume == "7"
    assert p.extra == {"custom_field": "kept"}
    assert model.dumps(p) == EXAMPLE


def test_defaults_dump_every_field_in_order():
    text = model.dumps(Paper(key="k", title="T"))
    keys = [line.split(":")[0] for line in text.split("---\n")[1].splitlines()]
    assert keys == list(model.FIELDS)
    assert model.loads(text).body == model.DEFAULT_BODY


def test_coercion_of_loose_yaml():
    p = model.from_front_matter({"title": "T", "year": "2020", "volume": 3, "added": "2026-01-02",
                                 "ids": {"doi": "10.1/X", "extra": 5}, "tags": None})
    assert p.year == 2020 and p.volume == "3" and p.added == date(2026, 1, 2)
    assert p.ids == {"doi": "10.1/X", "arxiv": None, "openalex": None, "s2": None, "extra": "5"}
    assert p.tags == []
    assert model.loads(model.dumps(p)).ids["extra"] == "5"


@pytest.mark.parametrize("text", ["no front matter", "---\nkey: x\n", "---\n- a list\n---\n"])
def test_bad_front_matter(text):
    with pytest.raises(model.FrontMatterError):
        model.loads(text)


def test_notes_sections():
    p = model.loads(EXAMPLE)
    assert p.notes == "My own notes."
    assert p.agent_notes == "### 2026-10-17 — summary (agent)\n\nA summary."
    body = model.set_notes(p.body, "New notes.\n\nTwo paragraphs.")
    assert model.get_notes(body) == "New notes.\n\nTwo paragraphs."
    assert model.get_agent_notes(body) == p.agent_notes  # agent notes untouched
    assert model.get_notes(model.set_notes(body, "")) == ""


def test_set_notes_without_sections():
    assert model.get_notes("") == "" and model.get_agent_notes("") == ""
    body = model.set_notes("Some text.\n", "Mine.")
    assert body == "Some text.\n\n## Notes\n\nMine.\n"
    assert model.get_notes(body) == "Mine."


def test_append_agent_note():
    body = model.append_agent_note(model.DEFAULT_BODY, "2026-10-17 — summary (agent)", "First.\n")
    body = model.append_agent_note(body, "2026-10-18 — critique (claude)", "Second.")
    assert model.get_notes(body) == ""
    assert model.get_agent_notes(body) == (
        "### 2026-10-17 — summary (agent)\n\nFirst.\n\n### 2026-10-18 — critique (claude)\n\nSecond."
    )
    # Recreates the section when missing, never inside Notes.
    body = model.append_agent_note("\n## Notes\n\nMine.\n", "h", "x")
    assert model.get_notes(body) == "Mine." and model.get_agent_notes(body) == "### h\n\nx"


@pytest.mark.parametrize(("kind", "raw", "norm"), [
    ("doi", "https://doi.org/10.1021/ACS.Anal", "10.1021/acs.anal"),
    ("doi", "doi:10.1/x", "10.1/x"),
    ("arxiv", "arXiv:2610.01234v3", "2610.01234"),
    ("arxiv", "https://arxiv.org/pdf/2610.01234v1.pdf", "2610.01234"),
    ("arxiv", "hep-th/9901001", "hep-th/9901001"),
    ("openalex", "https://openalex.org/w123", "W123"),
    ("s2", " abc ", "abc"),
    ("doi", "", None),
])
def test_norm_id(kind, raw, norm):
    assert model.norm_id(kind, raw) == norm


def _p(title="Joint Alignment", year=2026, **ids):
    p = Paper(title=title, year=year)
    p.ids.update(ids)
    return p


def test_same_paper():
    assert model.same_paper(_p(doi="10.1/X"), _p("Other", 1990, doi="https://doi.org/10.1/x"))
    assert model.same_paper(_p(arxiv="2610.01234v1"), _p("Other", arxiv="2610.01234v2"))
    assert model.same_paper(_p(openalex="W1"), _p("Other", openalex="https://openalex.org/W1"))
    assert model.same_paper(_p("Joint  alignment!"), _p("joint alignment", 2027))
    assert model.same_paper(_p(year=None), _p())
    assert not model.same_paper(_p(), _p(year=2028))
    assert not model.same_paper(_p(doi="10.1/a"), _p("Different", doi="10.1/b"))
    assert not model.same_paper(_p(""), _p(""))


def test_the_fast_yaml_loader_agrees_with_the_safe_one():
    import yaml
    text = ("key: wood2026joint\ntitle: 'A: tricky, title'\nyear: 2026\nadded: 2026-10-17\n"
            "ids: {doi: 10.1/x, arxiv: null}\ntags: [a, b]\nextra_date: 2026-01-02\n"
            "extra_time: 2026-01-02T03:04:05\n")
    assert model.load_yaml(text) == yaml.safe_load(text)
    assert type(model.load_yaml(text)["extra_date"]).__name__ == "date"
