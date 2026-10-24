"""`ruru schema` describes the `--json` output; real output must validate against it."""

import json
from datetime import datetime

import pytest
from test_similar import CHROMATOGRAPHY, add_paper, key_of, write_item

from httpfix import FEEDS
from ruru import cli, pdftext, schema

jsonschema = pytest.importorskip("jsonschema")


def check(output: str, command: str) -> object:
    """Validate a command's JSON output against its schema; return the parsed document."""
    data = json.loads(output)
    jsonschema.Draft202012Validator(schema.for_command(command)).validate(data)
    return data


def test_the_schema_is_itself_valid_and_complete():
    doc = schema.document()
    jsonschema.Draft202012Validator.check_schema(doc)
    for name, spec in schema.COMMANDS.items():
        assert spec["of"] in schema.DEFS, name
        jsonschema.Draft202012Validator.check_schema(schema.for_command(name))
    for name in schema.names():
        jsonschema.Draft202012Validator.check_schema(schema.definition(name))
    assert doc["schema_version"] == 1 and doc["exit_codes"]["4"].startswith("the library lock")
    assert set(doc["exit_codes"]) == {"0", "1", "2", "3", "4"}
    with pytest.raises(KeyError):
        schema.definition("nope")


def test_the_schema_command(lib, run):
    doc = json.loads(run("schema").out)
    assert doc["$defs"].keys() == schema.DEFS.keys() and "ruru_version" in doc
    assert json.loads(run("schema", "inbox").out)["type"] == "array"
    assert json.loads(run("schema", "pdf", "missing").out)["items"] == {"$ref": "#/$defs/pdf_missing"}
    assert json.loads(run("schema", "paper").out)["$ref"] == "#/$defs/paper"
    names = run("schema", "-q").out.splitlines()
    assert "inbox" in names and "paper" in names and "pdf missing" in names
    r = run("schema", "nope")
    assert r.code == cli.USAGE and "ruru schema -q" in r.err


@pytest.fixture
def full(lib, run, net, tmp_path, monkeypatch):
    """A library with papers, an inbox, a project, a PDF, feeds and some history."""
    for title, abstract, authors, year in CHROMATOGRAPHY:
        add_paper(run, title, abstract, authors, year)
    write_item(lib, "item-a", "Aligning gas chromatography peaks", "retention time alignment of peaks",
               fetched=datetime(2026, 10, 18, 8, 0, 0))
    write_item(lib, "item-b", "Quantum dots", "solar cells")
    run("suggest", "item-b", "skip", "--reason", "off topic", "--by", "claude")
    write_item(lib, "item-c", "Skip me", "x")
    run("skip", "item-c", "--reason", "no")
    draft = tmp_path / "draft"
    draft.mkdir()
    key = key_of(run, CHROMATOGRAPHY[0][0])
    (draft / "main.tex").write_text(f"\\begin{{document}}\nWe align peaks \\cite{{{key}}}.\n\\end{{document}}\n")
    assert run("project", "add", "demo", "--path", str(draft), "--bib", "refs.bib", "--tex", "main.tex").code == 0
    monkeypatch.setattr(pdftext, "extract", lambda path: "page one\fpage two")
    (lib / "papers" / key / "paper.pdf").write_bytes(b"%PDF-1.4 x")
    for name in ("anal-chem", "jat"):
        assert run("feeds", "add", name, *FEEDS[name]).code == 0
    return lib, key


def test_real_output_matches_the_schema(full, run):
    lib, key = full
    other = key_of(run, CHROMATOGRAPHY[1][0])
    assert check(run("list", "--json").out, "list")
    assert check(run("search", "chromatography", "--json").out, "search")
    paper = check(run("show", key, "--json").out, "show")
    assert paper["key"] == key
    jsonschema.Draft202012Validator(schema.definition("inbox_item")).validate(
        json.loads(run("show", "item-a", "--json").out))
    assert check(run("inbox", "--json").out, "inbox")
    assert check(run("related", key, "--in", "all", "--json").out, "related")
    assert check(run("seen", "--json").out, "seen")
    assert check(run("feeds", "--json").out, "feeds")
    assert check(run("project", "--json").out, "project")
    assert check(run("doctor", "--offline", "--json").out, "doctor")
    assert check(run("pdf", "missing", "--all", "--json").out, "pdf missing")
    assert check(run("pdf", "text", key, "--json").out, "pdf text")
    assert check(run("index", "--json").out, "index")
    assert check(run("index", "update", "--pdfs", "--json").out, "index update")
    assert check(run("undo", "--list", "--json").out, "undo")
    assert check(run("check", "demo", "--json").out, "check")
    claims = check(run("claims", "demo", "--json").out, "claims")
    claim = claims["claims"][0]["id"]
    assert run("own", "add", key, "-q").code == 0
    assert check(run("own", "--json").out, "own")
    made = run("verdict", "demo", claim, key, "background", "--from", "other", "-q")
    assert made.code == 0
    verdicts = json.loads(run("claims", "demo", "--json").out)["claims"][0]["checks"][0]["verdict"]
    jsonschema.Draft202012Validator(schema.definition("verdict")).validate(verdicts)
    assert check(run("dupes", "--json").out, "dupes") == []
    lines = lib / "lines.txt"
    lines.write_text(f"show {key} --json\nsearch chromatography --json\nlist -p nonesuch\n")
    results = check(run("batch", "-F", str(lines)).out, "batch")
    assert [r["code"] for r in results] == [0, 0, cli.NO_MATCH]
    assert other


def test_fetch_output_matches_the_schema(lib, run, net):
    for name in ("anal-chem", "xue", "jat"):
        assert run("feeds", "add", name, *FEEDS[name]).code == 0
    r = run("fetch", "--since", "2026-01-01", "--json")
    assert check(r.out, "fetch")
    assert check(run("enrich", "--json").out, "enrich") == []  # nothing in the library to enrich
