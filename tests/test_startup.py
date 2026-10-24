"""Starting `ruru` stays cheap: heavy libraries load only for the commands that need them."""

import json
import subprocess
import sys

import pytest

HEAVY = ("textual", "rich", "bibtexparser", "pyparsing", "feedparser", "pypdf", "markdown_it", "mdit_py_plugins",
         "latex2mathml")


def modules_after(code: str) -> set[str]:
    script = f"import sys\n{code}\nprint(__import__('json').dumps(sorted(sys.modules)))"
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout
    return {m.split(".")[0] for m in json.loads(out)}


def test_building_the_parser_imports_none_of_the_heavy_libraries():
    loaded = modules_after("from ruru import cli\ncli.build_parser()")
    assert not loaded & set(HEAVY), sorted(loaded & set(HEAVY))


@pytest.mark.parametrize("module", ["bibtexparser", "feedparser"])
def test_they_load_when_their_command_runs(module, tmp_path):
    code = {
        "bibtexparser": "from ruru.bibtex import parse_bibtex\nparse_bibtex('@misc{a, title={T}, year={2020}}')",
        "feedparser": "from ruru.resolve import arxiv\n"
                      "arxiv.parse_dated('<feed xmlns=\"http://www.w3.org/2005/Atom\"></feed>')",
    }[module]
    assert module in modules_after(code)
