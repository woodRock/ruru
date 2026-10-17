import subprocess
import sys

import pytest

from ruru import __version__, cli


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"ruru {__version__}"


def test_no_command_prints_help_to_stderr(capsys):
    assert cli.main([]) == cli.USAGE
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage: ruru" in err


def test_usage_error_exits_1_not_2(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["no-such-command"])
    assert exc.value.code == cli.USAGE


def test_every_help_ends_with_examples():
    parser = cli.build_parser()
    helps = [parser.format_help()]
    for action in parser._subparsers._group_actions:  # type: ignore[union-attr]
        helps += [p.format_help() for p in action.choices.values()]
    for text in helps:
        tail = text.rstrip().split("\n\n")[-1]
        assert tail.startswith("examples:"), text


def test_tui_without_textual(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ruru.tui", None)  # import fails as if the extra were missing
    assert cli.main(["tui"]) == cli.USAGE
    assert "ruru[tui]" in capsys.readouterr().err


def test_core_never_imports_textual():
    code = "import sys, ruru.cli; ruru.cli.build_parser(); assert 'textual' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)
