import argparse
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
    def walk(parser):
        yield parser
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for sub in action.choices.values():
                    yield from walk(sub)

    helps = [p.format_help() for p in walk(cli.build_parser())]
    assert len(helps) > 20
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


def test_a_closed_pipe_is_not_an_error(monkeypatch):
    """`ruru list | head -1`: the reader leaves early; ruru exits quietly with success, with stdout
    pointed at /dev/null so that the exit-time flush doesn't fail too."""
    import os

    def boom(args):
        raise BrokenPipeError()
    redirected = []
    monkeypatch.setattr(os, "dup2", lambda src, dst: redirected.append(dst))  # (not onto pytest's capture file)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 99, raising=False)
    assert cli.dispatch(argparse.Namespace(func=boom)) == cli.OK
    assert redirected == [99]


def test_dispatch_turns_errors_into_exit_codes(capsys):
    import argparse

    from ruru.errors import NothingMatched, UsageError

    def raises(exc):
        def func(args):
            raise exc
        return argparse.Namespace(func=func)
    assert cli.dispatch(raises(UsageError("bad"))) == cli.USAGE
    assert cli.dispatch(raises(NothingMatched("none"))) == cli.NO_MATCH
    assert cli.dispatch(raises(FileNotFoundError(2, "No such file", "x.md"))) == cli.USAGE
    err = capsys.readouterr().err
    assert "ruru: error: bad" in err and "ruru: none" in err and "No such file: x.md" in err
