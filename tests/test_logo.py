import re
import sys

import pytest

from ruru import cli, logo

ANSI = re.compile(r"\033\[[0-9;]*m")


@pytest.mark.parametrize("rows", [logo.HERO, logo.SMALL, [logo.MINI]])
def test_ansi_strips_to_plain(rows):
    coloured = logo.ansi(rows)
    assert "\033[" in coloured
    assert [line.rstrip() for line in ANSI.sub("", coloured).split("\n")] == logo.plain(rows).split("\n")
    assert logo.ansi(rows, color=False) == logo.plain(rows)


def test_banner_pairs_owl_with_wordmark():
    plain = logo.banner(color=False)
    lines = plain.split("\n")
    assert len(lines) == len(logo.SMALL)
    assert "ruru" in lines[1] and "literature library" in lines[2] and "◉" in lines[1]
    assert "\033[" not in plain and "\033[1;38;5;220m" in logo.banner(color=True)


def test_rich_text_and_svg(tmp_path):
    pytest.importorskip("rich")
    text = logo.rich_text(logo.SMALL, "Inbox empty.", "f fetches the feeds.")
    assert text.plain.endswith("Inbox empty.\nf fetches the feeds.") and "◉" in text.plain
    out = tmp_path / "ruru.svg"
    logo.save_svg(str(out))
    assert out.read_text().startswith("<svg") and "ruru" in out.read_text()


def test_bare_ruru_shows_banner_without_colour_when_piped(run):
    r = run()
    assert r.code == cli.USAGE and r.out == ""
    assert "(◉)(◉)" in r.err and "usage: ruru" in r.err and "\033[" not in r.err


def test_bare_ruru_colours_banner_on_a_tty(run, monkeypatch):
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
    assert "\033[1;38;5;220m" in run().err
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\033[" not in run().err


def test_help_has_no_owl(run):
    """Bare `ruru` shows the banner once; `-h` stays plain argparse text (no second, uncoloured owl)."""
    r = run("-h")
    assert "◉" not in r.out and "\033[" not in r.out
