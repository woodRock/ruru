"""The derived cache: word counts and PDF text, keyed on the files they came from."""

import os
import sqlite3

import pytest

from ruru import index as indexmod
from ruru import rank
from ruru.index import Index, signature


def _doc(path, text, calls):
    def count():
        calls.append(path.name)
        return rank.counts(text)
    return path.stem, path, count


def test_counts_are_cached_until_the_file_changes(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("one")
    calls: list[str] = []
    idx = Index(tmp_path)
    assert idx.counts("paper", [_doc(f, "alignment peaks", calls)]) == {"a": rank.counts("alignment peaks")}
    assert idx.counts("paper", [_doc(f, "something else", calls)]) == {"a": rank.counts("alignment peaks")}
    assert calls == ["a.md"]  # the second call was answered from the cache
    f.write_text("two!")  # new size and time
    assert idx.counts("paper", [_doc(f, "something else", calls)]) == {"a": rank.counts("something else")}
    assert calls == ["a.md", "a.md"]
    persisted = Index(tmp_path).counts("paper", [_doc(f, "x", calls)])
    assert persisted == {"a": rank.counts("something else")}


def test_kinds_are_separate_and_removed_files_are_forgotten(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("a")
    b.write_text("b")
    calls: list[str] = []
    idx = Index(tmp_path)
    idx.counts("paper", [_doc(a, "alpha", calls), _doc(b, "beta", calls)])
    idx.counts("inbox", [_doc(a, "gamma", calls)])
    assert idx.status().papers == 2 and idx.status().inbox == 1
    assert idx.counts("paper", [_doc(a, "alpha", calls)]) == {"a": rank.counts("alpha")}
    assert idx.status().papers == 1  # b is gone
    assert idx.counts("inbox", [_doc(a, "other", calls)])["a"] == rank.counts("gamma")


def test_a_missing_file_is_counted_but_not_cached(tmp_path):
    ghost = tmp_path / "ghost.md"
    calls: list[str] = []
    idx = Index(tmp_path)
    assert idx.counts("paper", [_doc(ghost, "alignment", calls)]) == {"ghost": rank.counts("alignment")}
    assert idx.status().papers == 0 and signature(ghost) is None


def test_an_unusable_database_still_answers(tmp_path):
    (tmp_path / ".ruru").mkdir()
    (tmp_path / ".ruru" / "index.sqlite").write_bytes(b"this is not a sqlite database" * 50)
    f = tmp_path / "a.md"
    f.write_text("x")
    idx = Index(tmp_path)
    assert idx.counts("paper", [_doc(f, "alignment", [])]) == {"a": rank.counts("alignment")}
    assert idx.status().exists is False
    assert idx.pdf_text("k") is None and idx.search_pdfs("alignment") == []
    assert not (tmp_path / ".ruru" / "index.sqlite").exists()  # the corrupt file was removed
    assert Index(tmp_path).counts("paper", [_doc(f, "alignment", [])])["a"] and Index(tmp_path).status().exists


def test_a_read_only_directory_degrades_to_no_cache(tmp_path):
    (tmp_path / ".ruru").write_text("a file where the directory should be")
    f = tmp_path / "a.md"
    f.write_text("x")
    idx = Index(tmp_path)
    assert idx.counts("paper", [_doc(f, "alignment", [])]) == {"a": rank.counts("alignment")}
    assert idx.sync_pdfs({"k": f}, lambda p: "text").failed  # reports it instead of raising


def test_a_new_tokeniser_version_rebuilds(tmp_path, monkeypatch):
    f = tmp_path / "a.md"
    f.write_text("x")
    calls: list[str] = []
    Index(tmp_path).counts("paper", [_doc(f, "alignment", calls)])
    monkeypatch.setattr(indexmod, "VERSION", indexmod.VERSION + 1)
    Index(tmp_path).counts("paper", [_doc(f, "alignment", calls)])
    assert calls == ["a.md", "a.md"]


def test_clear_removes_the_files(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("x")
    idx = Index(tmp_path)
    idx.counts("paper", [_doc(f, "alignment", [])])
    assert idx.path.exists()
    idx.clear()
    assert not idx.path.exists() and idx.status().papers == 0


# --- PDF text ------------------------------------------------------------------------------------

@pytest.fixture(params=[True, False], ids=["fts5", "scan"])
def idx(request, tmp_path):
    return Index(tmp_path, fts=request.param)


def _pdfs(tmp_path, **texts):
    out = {}
    for key, _ in texts.items():
        path = tmp_path / f"{key}.pdf"
        path.write_bytes(f"%PDF {key}".encode())
        out[key] = path
    return out


def test_pdf_text_is_extracted_once_and_searchable(idx, tmp_path):
    texts = {"huber": "Huber weights downweight outliers in robust regression.",
             "cow": "Correlation optimised warping aligns chromatograms. Naïve baselines drift."}
    pdfs = _pdfs(tmp_path, **texts)
    seen: list[str] = []
    result = idx.sync_pdfs(pdfs, lambda p: texts[p.stem], progress=seen.append)
    assert result.extracted == ["cow", "huber"] and seen == ["cow", "huber"] and not result.failed
    assert idx.fts is (idx._want_fts and idx.status().fts)
    again = idx.sync_pdfs(pdfs, lambda p: pytest.fail("read again"))
    assert again.current == 2 and not again.extracted
    assert [k for k, _ in idx.search_pdfs("huber weights")] == ["huber"]
    assert [k for k, _ in idx.search_pdfs("outliers regression")] == ["huber"]
    assert idx.search_pdfs("huber chromatograms") == []  # every word must be there
    assert [k for k, _ in idx.search_pdfs("naive")] == ["cow"]  # accents are folded
    snippet = idx.search_pdfs("warping")[0][1]
    assert "warping" in snippet.lower() and "\n" not in snippet
    assert idx.pdf_text("huber") == texts["huber"] and idx.pdf_text("nope") is None
    assert idx.search_pdfs("   ") == []


def test_a_changed_or_removed_pdf_is_updated(idx, tmp_path):
    pdfs = _pdfs(tmp_path, a="one", b="two")
    texts = {"a": "alpha text", "b": "beta text"}
    idx.sync_pdfs(pdfs, lambda p: texts[p.stem])
    pdfs["a"].write_bytes(b"%PDF changed, and longer")
    texts["a"] = "gamma text"
    result = idx.sync_pdfs(pdfs, lambda p: texts[p.stem])
    assert result.extracted == ["a"] and result.current == 1
    assert idx.search_pdfs("alpha") == [] and [k for k, _ in idx.search_pdfs("gamma")] == ["a"]
    del pdfs["b"]
    idx.sync_pdfs({"a": pdfs["a"]}, lambda p: "x", forget=False)  # a partial sync keeps b's text
    assert idx.pdf_text("b") == "beta text"
    idx.sync_pdfs({"a": pdfs["a"]}, lambda p: "x")  # a full one drops it
    assert idx.pdf_text("b") is None and idx.search_pdfs("beta") == []


def test_failed_pdfs_are_remembered_until_they_change(idx, tmp_path):
    pdfs = _pdfs(tmp_path, bad="broken")

    def boom(path):
        raise ValueError("encrypted")
    result = idx.sync_pdfs(pdfs, boom)
    assert result.failed == {"bad": "encrypted"} and idx.status().pdfs_failed == 1
    assert idx.pdf_text("bad") is None
    assert idx.sync_pdfs(pdfs, lambda p: pytest.fail("tried again")).current == 1
    retried = idx.sync_pdfs(pdfs, lambda p: "recovered text", retry=True)
    assert retried.extracted == ["bad"] and idx.pdf_text("bad") == "recovered text"
    assert idx.status().pdfs_failed == 0 and idx.status().pdfs == 1


def test_pending_lists_what_is_not_read_yet(idx, tmp_path):
    pdfs = _pdfs(tmp_path, a="1", b="2")
    assert idx.pending_pdfs(pdfs) == ["a", "b"]
    idx.sync_pdfs({"a": pdfs["a"]}, lambda p: "x", forget=False)
    assert idx.pending_pdfs(pdfs) == ["b"]


def test_text_stored_without_fts_is_searchable_once_fts_arrives(tmp_path):
    pdfs = _pdfs(tmp_path, a="1")
    Index(tmp_path, fts=False).sync_pdfs(pdfs, lambda p: "alignment of chromatograms")
    later = Index(tmp_path)
    assert [k for k, _ in later.search_pdfs("chromatograms")] == ["a"]
    assert later.fts


def test_status_json(tmp_path):
    st = Index(tmp_path).status()
    assert st.to_json()["full_text_search"] in (True, False) and st.to_json()["papers"] == 0
    assert os.path.basename(st.to_json()["path"]) == "index.sqlite"
    assert isinstance(sqlite3.sqlite_version, str)


def test_warn_failures_prints_each(capsys):
    indexmod.warn_failures({"b": "bad", "a": "worse"})
    assert capsys.readouterr().err.splitlines() == [
        "ruru: warning: a: no text from the PDF: worse", "ruru: warning: b: no text from the PDF: bad"]
