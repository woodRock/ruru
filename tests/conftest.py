from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from httpfix import Router, standard
from ruru import cli, http


@dataclass
class Result:
    code: int
    out: str
    err: str


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No test sees the real user config, library or git identity."""
    monkeypatch.setenv("RURU_CONFIG", str(tmp_path / "config" / "config.toml"))
    monkeypatch.delenv("RURU_LIBRARY", raising=False)
    monkeypatch.delenv("RURU_AGENT", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("RURU_LOCK_TIMEOUT", "0.2")
    for var in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{var}_NAME", "ruru test")
        monkeypatch.setenv(f"GIT_{var}_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("RURU_OPENALEX_API_KEY", raising=False)
    # No network, ever: an empty router fails any request. Tests that need HTTP use `net`.
    monkeypatch.setattr(http, "default_transport", Router())
    monkeypatch.setattr(http, "default_sleep", lambda s: None)
    # Nor background processes: `ruru add --bib` would start `ruru enrich`. Tests read the calls.
    spawned: list[list[str]] = []
    monkeypatch.setattr(cli, "spawn", lambda argv, log: spawned.append(argv))
    return spawned


@pytest.fixture
def spawned(isolated) -> list[list[str]]:
    return isolated


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setattr(http, "default_sleep", calls.append)
    return calls


@pytest.fixture
def net(monkeypatch) -> Router:
    """A router serving every recorded fixture; add synthetic routes to it (they match first if
    inserted at the front: `net.routes.insert(0, …)`, or build a fresh `Router()`)."""
    router = standard(Router())
    monkeypatch.setattr(http, "default_transport", router)
    return router


@pytest.fixture
def run(capsys):
    def _run(*args: str) -> Result:
        try:
            code = cli.main(list(args))
        except SystemExit as e:
            code = int(e.code or 0)
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)
    return _run


@pytest.fixture
def lib(tmp_path, run, monkeypatch) -> Path:
    root = tmp_path / "papers"
    assert run("init", str(root), "--email", "test@example.invalid").code == 0
    monkeypatch.setenv("RURU_LIBRARY", str(root))
    return root


def git_log(root: Path) -> list[str]:
    cmd = ["git", "-C", str(root), "log", "--format=%s"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


SAMPLE_BIB = r"""
@article{vandermaaten2008,
  author = {van der Maaten, Laurens and Hinton, Geoffrey},
  title = {Visualizing Data using {t-SNE}},
  journal = {Journal of Machine Learning Research},
  year = {2008}, volume = {9}, pages = {2579--2605},
  doi = {10.5555/FAKE.1}
}
@misc{nunez2025,
  author = {N{\'u}{\~n}ez, Jos{\'e} and S{\o}rensen, S{\o}ren},
  title = {A Survey of Pyrolysis \& GC-MS},
  year = {2025}, eprint = {2501.01234v2}, archivePrefix = {arXiv}
}
"""
