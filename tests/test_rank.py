"""TF-IDF ranking: tokens, vectors, nearest neighbours, and the relevance of inbox items."""

import math

import pytest

from ruru import rank


def test_stem_is_conservative():
    assert [rank.stem(w) for w in ("alignments", "studies", "boxes", "classes", "optimisation", "gas", "ms",
                                   "analysis", "virus", "chromatography")] == [
        "alignment", "study", "box", "class", "optimization", "gas", "ms", "analysis", "virus", "chromatography"]


def test_tokens_drop_stopwords_numbers_and_single_letters():
    assert rank.tokens("A Novel Approach to Retention-Time Alignment in GC–MS (2024) using x") == [
        "retention", "time", "alignment", "gc", "ms"]
    assert rank.tokens("Étude of alignments") == ["etude", "alignment"]  # accents are folded before stemming


def test_counts_weight_title_and_tags_over_abstract():
    c = rank.counts("Peak alignment", "Aligning peaks across runs; alignment matters", tags=["gc-ms"],
                    venue="Analytical Chemistry")
    assert c["peak"] == rank.TITLE_WEIGHT + 1
    assert c["alignment"] == rank.TITLE_WEIGHT + 1
    assert c["ms"] == rank.TAG_WEIGHT and c["analytical"] == 1
    assert rank.title_counts("Peak alignment") == {"peak": 1, "alignment": 1}


def test_vectors_are_unit_length_and_rare_words_weigh_more():
    docs = [{"common": 1, "rare": 1}, {"common": 1}, {"common": 1, "other": 1}]
    idf = rank.Idf(docs)
    assert idf.weight("rare") > idf.weight("common") and idf.weight("never seen") > idf.weight("rare")
    v = rank.vectorise({"common": 2, "rare": 1}, idf)
    assert math.isclose(sum(x * x for x in v.values()), 1.0)
    assert v["rare"] > v["common"] * 0.5
    assert rank.vectorise({}, idf) == {}
    assert math.isclose(rank.cosine(v, v), 1.0)
    assert rank.cosine(v, rank.vectorise({"other": 1}, idf)) == 0.0


def test_nearest_orders_by_cosine_and_excludes():
    docs = {"a": {"gc": 3, "alignment": 3}, "b": {"gc": 3, "proteomics": 3}, "c": {"proteomics": 3, "cell": 3},
            "d": {"gc": 1, "alignment": 1, "peak": 1}}
    idf = rank.Idf(docs.values())
    corpus = rank.Corpus(docs, idf)
    assert len(corpus) == 4
    query = rank.vectorise({"gc": 2, "alignment": 2}, idf)
    ids = [i for i, _ in corpus.nearest(query, 4)]
    assert ids[:2] == ["a", "d"] and "c" not in ids  # c shares nothing with the query
    assert corpus.nearest(query, 4, exclude={"a"})[0][0] == "d"
    sims = [s for _, s in corpus.nearest(query, 4)]
    assert sims == sorted(sims, reverse=True) and sims[0] <= 1.0
    assert corpus.nearest({}, 3) == []


def test_common_words_are_not_looked_up_in_a_big_corpus():
    docs = {f"d{i}": {"everywhere": 1, f"own{i}": 1} for i in range(40)}
    idf = rank.Idf(docs.values())
    corpus = rank.Corpus(docs, idf)
    assert corpus.nearest(rank.vectorise({"everywhere": 1}, idf), 3) == []  # in every document: no signal
    assert corpus.nearest(rank.vectorise({"own7": 1}, idf), 1) == [("d7", pytest.approx(0.6, abs=0.4))]


def _library():
    docs = {f"lib{i}": rank.counts("Alignment of gas chromatography peaks",
                                   f"retention time alignment of peaks {i}") for i in range(6)}
    return docs | {"other": rank.counts("Protein folding", "molecular dynamics of proteins")}


def test_relevance_ranks_close_items_first():
    items = {
        "near": rank.counts("Retention time alignment for chromatography",
                            "aligning peaks by retention time"),
        "far": rank.counts("Quantum dots in solar cells", "photovoltaic efficiency of dots"),
    }
    titles = {k: rank.title_counts(k) for k in items}
    scores = rank.relevance(_library(), items, titles, [])
    assert scores is not None
    assert scores["near"].score > scores["far"].score
    assert scores["near"].similar[0][0].startswith("lib")
    assert scores["far"].score == 0.0 and scores["far"].similar == ()
    assert scores["near"].to_json()["similar_to"][0]["key"].startswith("lib")


def test_skipped_titles_push_similar_items_down():
    items = {"x": rank.counts("Peak alignment in mass spectrometry proteomics", "proteomics peak alignment"),
             "y": rank.counts("Peak alignment in gas chromatography", "gas chromatography peak alignment")}
    titles = {"x": rank.title_counts("Peak alignment in mass spectrometry proteomics"),
              "y": rank.title_counts("Peak alignment in gas chromatography")}
    skipped = [rank.title_counts("Mass spectrometry proteomics workflow"),
               rank.title_counts("Proteomics peak picking")]
    plain = rank.relevance(_library(), items, titles, [])
    with_skips = rank.relevance(_library(), items, titles, skipped)
    assert plain and with_skips
    assert with_skips["x"].penalty > 5 * with_skips["y"].penalty > 0  # y only shares "peak" with a skipped title
    assert with_skips["x"].score < plain["x"].score
    assert plain["x"].score - with_skips["x"].score > 5 * (plain["y"].score - with_skips["y"].score)


def test_too_small_a_library_cannot_rank():
    small = {f"k{i}": rank.counts("Alignment", "peaks") for i in range(rank.MIN_LIBRARY - 1)}
    assert rank.relevance(small, {"i": rank.counts("Alignment")}, {"i": {}}, []) is None
    assert rank.relevance({**small, "empty": {}}, {"i": rank.counts("Alignment")}, {"i": {}}, []) is None


@pytest.mark.parametrize("text", ["", "the of and", "2024 12"])
def test_documents_without_words_have_no_vector(text):
    assert rank.vectorise(rank.counts(text), rank.Idf([])) == {}
