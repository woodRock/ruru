import pytest

from conftest import SAMPLE_BIB
from ruru import bibtex, keys, text
from ruru.model import Paper


@pytest.mark.parametrize(("authors", "year", "title", "key"), [
    (["Jesse Wood"], 2026, "Joint Retention-Time Alignment of Many GC–MS Batches", "wood2026joint"),
    (["Laurens van der Maaten"], 2008, "Visualizing Data using t-SNE", "maaten2008visualizing"),
    (["Núñez, José"], 2025, "A Survey of Pyrolysis", "nunez2025survey"),
    (["Søren Ørsted"], 2024, "On the Theory of Everything", "orsted2024theory"),
    (["Mengjie Zhang"], None, "The", "zhang2026the".replace("2026", "nd")),
    ([], 2020, "", "anon2020untitled"),
    (["Ngāti Kahu"], 2021, "Ngā Taonga", "kahu2021nga"),
])
def test_base_key(authors, year, title, key):
    assert keys.base_key(authors, year, title) == key


def test_unique_key_suffixes():
    taken = {"wood2026joint"} | {f"wood2026joint{c}" for c in "abcdefghijklmnopqrstuvwxyz"}
    assert keys.unique_key("smith2020x", taken) == "smith2020x"
    assert keys.unique_key("wood2026joint", {"wood2026joint"}) == "wood2026jointa"
    assert keys.unique_key("wood2026joint", taken) == "wood2026jointaa"


@pytest.mark.parametrize(("name", "split"), [
    ("Jesse Wood", ("Jesse", "Wood")),
    ("Wood, Jesse R. H.", ("Jesse R. H.", "Wood")),
    ("Laurens van der Maaten", ("Laurens", "van der Maaten")),
    ("Plato", ("", "Plato")),
    ("  Bing   Xue ", ("Bing", "Xue")),
])
def test_split_name(name, split):
    assert text.split_name(name) == split


def _paper(**kw):
    p = Paper(key="wood2026joint", title="Joint Retention-Time Alignment of Many GC–MS Batches",
              authors=["Jesse Wood", "Bach Nguyen"], year=2026, type="article",
              venue="IEEE Transactions on Artificial Intelligence", volume="7", issue="2", pages="1-12")
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_to_bibtex_is_deterministic_and_stable():
    p = _paper()
    p.ids["doi"] = "https://doi.org/10.1109/TAI.2026.1"
    expected = """\
@article{wood2026joint,
  author = {Wood, Jesse and Nguyen, Bach},
  title = {Joint Retention-Time Alignment of Many {GC--MS} Batches},
  journal = {IEEE Transactions on Artificial Intelligence},
  year = {2026},
  volume = {7},
  number = {2},
  pages = {1--12},
  doi = {10.1109/tai.2026.1}
}
"""
    assert bibtex.to_bibtex(p) == expected
    assert bibtex.to_bibtex(p) == bibtex.to_bibtex(_paper(ids=dict(p.ids)))


def test_ascii_and_utf8_encodings():
    p = _paper(authors=["Ngāti Ōtākou", "José Núñez", "Søren Ørsted", "İbrahim Çelik"],
               title="Māori & Pākehā: 50% of #1 — “quoted”", venue="Zeitschrift für Chemie")
    out = bibtex.to_bibtex(p, "ascii")
    assert r"{\=O}t{\=a}kou, Ng{\=a}ti" in out
    assert r"N{\'u}{\~n}ez, Jos{\'e}" in out
    assert r"{\O}rsted, S{\o}ren" in out
    assert r"{\c C}elik, {\.I}brahim" in out
    assert r"M{\=a}ori \& P{\=a}keh{\=a}: 50\% of \#1 --- ``quoted''" in out
    assert r"Zeitschrift f{\"u}r Chemie" in out
    assert out.isascii()
    utf8 = bibtex.to_bibtex(p, "utf8")
    assert "Ngāti" in utf8 and r"Māori \& Pākehā" in utf8


def test_escape_edge_cases():
    assert bibtex.escape("already \\& escaped") == "already \\& escaped"
    assert bibtex.escape("naïve í") == r'na{\"\i}ve {\'\i}'
    assert bibtex.escape("日本") == "日本"  # no LaTeX form: left alone


def test_title_protection():
    assert bibtex._protect("Deep LiDAR and GC-MS for t-SNE $x^2$ {Kept}") == \
        "Deep {LiDAR} and {GC-MS} for {t-SNE} $x^2$ {Kept}"
    assert bibtex._protect("Retention-Time Alignment") == "Retention-Time Alignment"


@pytest.mark.parametrize(("ptype", "venue", "line"), [
    ("inproceedings", "GECCO", "booktitle = {{GECCO}}"),
    ("thesis", "Victoria University of Wellington", "school = {Victoria University of Wellington}"),
    ("book", "Springer", "publisher = {Springer}"),
    ("misc", "Zenodo", "howpublished = {Zenodo}"),
])
def test_entry_types(ptype, venue, line):
    p = _paper(type=ptype, venue=venue, title="X", pages=None, volume=None, issue=None)
    out = bibtex.to_bibtex(p)
    assert f"@{bibtex._ENTRY_TYPE[ptype]}{{wood2026joint," in out
    assert line.replace("{{GECCO}}", "{GECCO}") in out


def test_preprint_and_corporate_author():
    p = _paper(type="preprint", venue=None, authors=["The HUPO Consortium", "Plato"], url="https://arxiv.org/abs/2610.01234")
    p.ids["arxiv"] = "2610.01234v2"
    out = bibtex.to_bibtex(p)
    assert out.startswith("@misc{")
    assert "author = {Consortium, The HUPO and Plato}" in out
    assert "howpublished = {arXiv preprint arXiv:2610.01234}" in out
    assert "eprint = {2610.01234},\n  archiveprefix = {arXiv},\n  url = {https://arxiv.org/abs/2610.01234}" in out


def test_parse_bibtex():
    (k1, a), (k2, b) = bibtex.parse_bibtex(SAMPLE_BIB)
    assert (k1, k2) == ("vandermaaten2008", "nunez2025")
    assert a.authors == ["Laurens van der Maaten", "Geoffrey Hinton"]
    assert a.title == "Visualizing Data using t-SNE"
    assert (a.year, a.volume, a.pages, a.type) == (2008, "9", "2579-2605", "article")
    assert a.ids["doi"] == "10.5555/fake.1"
    assert b.authors == ["José Núñez", "Søren Sørensen"]
    assert b.title == "A Survey of Pyrolysis & GC-MS"
    assert (b.type, b.ids["arxiv"]) == ("preprint", "2501.01234")


def test_parse_bibtex_types():
    src = """
@inproceedings{a, title={A}, booktitle={GECCO}, year={2020}}
@phdthesis{b, title={B}, school={VUW}, year={2024}}
@book{c, title={C}, publisher={Springer}}
@techreport{d, title={D}, year={n.d.}}
"""
    types = [(p.type, p.venue, p.year, p.publisher) for _, p in bibtex.parse_bibtex(src)]
    assert types == [("inproceedings", "GECCO", 2020, None), ("thesis", "VUW", 2024, None),
                     ("book", None, None, "Springer"), ("misc", None, None, None)]
