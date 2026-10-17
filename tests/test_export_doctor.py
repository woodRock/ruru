import json

import pytest

from conftest import git_log
from httpfix import FEEDS, Router
from ruru import cli, doctor, http, model
from ruru.export import SCHEMA_VERSION


@pytest.fixture
def stocked(lib, run, net):
    run("add", "--title", "Joint Retention-Time Alignment", "--author", "Jesse Wood", "--year", "2026",
        "-t", "gc-ms", "-q")
    run("add", "--title", "Ngā taonga", "--author", "Āperahama Ōtākou", "--year", "2020", "-q")
    run("note", "wood2026joint", "--agent", "-m", "Abstract only: aligns batches.")
    run("feeds", "add", "jat", *FEEDS["jat"])
    run("fetch", "--since", "2026-01-01")
    return lib


def test_export_json(stocked, run, tmp_path):
    doc = json.loads(run("export", "json").out)
    assert doc["schema_version"] == SCHEMA_VERSION
    assert [p["key"] for p in doc["library"]] == ["otakou2020nga", "wood2026joint"]
    assert doc["library"][1]["agent_notes"].endswith("Abstract only: aligns batches.")
    assert set(doc["library"][0]) == set(json.loads(run("show", "otakou2020nga", "--json").out))
    assert len(doc["inbox"]) == 3 and doc["inbox"][0] == json.loads(run("inbox", "--json").out)[0]
    assert doc["feeds"] == json.loads(run("feeds", "--json").out)
    assert doc["projects"] == []
    assert doc["activity"][0]["message"].startswith("ruru: fetch") and "T" in doc["activity"][0]["date"]
    assert len(doc["activity"]) == len(git_log(stocked))
    lean = json.loads(run("export", "json", "--no-notes").out)
    assert "notes" not in lean["library"][0]
    out = tmp_path / "exports" / "lib.json"
    r = run("export", "json", "-o", str(out))
    assert r.out == "" and f"wrote {out}" in r.err and json.loads(out.read_text())["schema_version"] == 1


def test_export_bib(stocked, run, tmp_path):
    text = run("export", "bib").out
    assert text.index("@article{otakou2020nga,") < text.index("@article{wood2026joint,")
    assert r"{\=O}t{\=a}kou" in text and text.isascii()
    assert "Ōtākou" in run("export", "bib", "--bib-encoding", "utf8").out
    only = run("export", "bib", "-t", "gc-ms").out
    assert "wood2026joint" in only and "otakou" not in only
    assert run("export", "bib", "-p", "nope").code == cli.NO_MATCH
    assert run("export", "bib", "-s", "read").code == cli.NO_MATCH
    run("export", "bib", "-o", str(tmp_path / "all.bib"))
    assert (tmp_path / "all.bib").read_text() == text


def test_export_empty_library(lib, run):
    doc = json.loads(run("export", "json", "-o", "-").out)
    assert doc["library"] == [] and doc["inbox"] == [] and doc["activity"][-1]["message"] == "ruru: init"


def test_export_outside_git(tmp_path):
    from ruru import gitops
    assert gitops.log(tmp_path) == []


def _by_name(checks):
    return {c.name: c for c in checks}


def test_doctor_healthy_offline(stocked, run):
    checks = _by_name(doctor.run(stocked, offline=True))
    assert checks["library"].status == "ok" and checks["git status"].status == "ok"
    assert checks["git identity"].detail == "ruru test <test@example.invalid>"
    assert checks["papers"].detail == "2 papers" and checks["inbox"].detail == "3 items"
    assert checks["feeds"].detail == "1 configured"
    assert checks["openalex key"].status == "warn" and checks["config"].status == "ok"
    assert not any(name.startswith("api") for name in checks)
    r = run("doctor", "--offline")
    assert r.code == cli.OK and "ok    papers" in r.out


def test_doctor_finds_problems(stocked, run, monkeypatch, tmp_path):
    monkeypatch.setenv("RURU_OPENALEX_API_KEY", "k")
    (stocked / "papers" / "wood2026joint" / "paper.bib").write_text("edited by hand\n")
    (stocked / "papers" / "otakou2020nga" / "paper.md").write_text("garbage")
    (stocked / "inbox" / "2026-10-17").mkdir(parents=True, exist_ok=True)
    (stocked / "inbox" / "2026-10-17" / "bad.md").write_text("nope")
    (stocked / "ruru.toml").write_text((stocked / "ruru.toml").read_text().replace(
        'contact_email = "test@example.invalid"', 'contact_email = ""')
        + '\n[projects.gone]\npath = "/nonexistent/x"\nbib = "r.bib"\n')
    checks = _by_name(doctor.run(stocked, offline=True))
    assert checks["paper.bib"].status == "warn" and "wood2026joint" in checks["paper.bib"].detail
    assert checks["papers"].status == "fail" and "otakou2020nga" in checks["papers"].detail
    assert checks["inbox"].status == "warn" and "1 unreadable" in checks["inbox"].detail
    assert checks["contact email"].status == "warn" and checks["openalex key"].status == "ok"
    assert checks["git status"].status == "warn"
    assert checks["project gone"].status == "warn"
    assert run("doctor", "--offline").code == cli.PARTIAL


def test_doctor_bad_settings(lib, run, tmp_path, monkeypatch):
    (lib / "ruru.toml").write_text('[[feeds]]\nname = "x"\nkind = "nope"\n\n[projects."bad name"]\npath = "p"\n'
                                   'bib = "b"\n')
    checks = _by_name(doctor.run(lib, offline=True))
    assert checks["feeds"].status == "fail" and checks["projects"].status == "fail"
    monkeypatch.delenv("RURU_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    checks = doctor.run(tmp_path / "not-a-library", offline=True)
    assert [(c.name, c.status) for c in checks] == [("config", "warn"), ("library", "fail")]
    rows = json.loads(run("doctor", "-L", str(tmp_path / "nowhere"), "--offline", "--json").out)
    assert rows[-1]["status"] == "fail"


def test_doctor_without_git(tmp_path, run):
    root = tmp_path / "plain"
    run("init", str(root))
    import shutil
    shutil.rmtree(root / ".git")
    checks = _by_name(doctor.run(root, offline=True))
    assert checks["git"].status == "warn" and "git identity" not in checks


def test_doctor_apis(stocked, run, monkeypatch):
    router = Router().add("crossref", body="{}").add("openalex", status=503).add("arxiv", body="<feed/>")
    monkeypatch.setattr(http, "default_transport", router)
    checks = _by_name(doctor.run(stocked))
    assert checks["api crossref"].status == "ok" and "HTTP 200" in checks["api crossref"].detail
    assert checks["api openalex"].status == "fail" and checks["api arxiv"].status == "ok"
    monkeypatch.setattr(http, "default_transport", Router().add("crossref", status=404).add(".", body=""))
    assert _by_name(doctor.run(stocked))["api crossref"].status == "fail"
    rows = json.loads(run("doctor", "--json").out)
    assert {r["name"] for r in rows} >= {"api crossref", "api openalex", "api arxiv"}


def test_model_today_is_a_date():
    assert model.today().year >= 2026
