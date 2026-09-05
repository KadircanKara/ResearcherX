"""app/local_rag/index.py — the BM25 arm, on bm25s.

Two `bm25s` behaviours differ from the Postgres arm this replaces and are
pinned here because nothing else would catch them:

- `retrieve(k=...)` RAISES when k exceeds the corpus size, where SQL's
  LIMIT simply returns fewer rows;
- a query matching no term still returns documents, at score 0.0, where
  `tsv @@ tsquery` returns none. Feeding those into RRF would let the
  sparse arm vote for arbitrary documents on every unmatched query.
"""

from pathlib import Path

import pytest

from app.local_rag.index import build_bm25, load_bm25, save_bm25

_CORPUS = [
    "swarm coverage path planning for unmanned aerial vehicles",
    "battery chemistry and charging cycles for small drones",
    "minimising revisit time in multi-UAV surveillance surveys",
]


def test_ranks_the_lexically_matching_document_first():
    index = build_bm25(_CORPUS)
    hits = index.search("revisit time", limit=3)
    assert hits[0][0] == 2


def test_returns_document_index_and_score_pairs():
    index = build_bm25(_CORPUS)
    hits = index.search("battery chemistry", limit=3)
    doc_index, score = hits[0]
    assert doc_index == 1
    assert score > 0


def test_documents_scoring_zero_are_excluded():
    """bm25s returns top-k whatever the scores; a 0.0 hit is 'no lexical
    evidence' and must not reach the fusion as a rank."""
    index = build_bm25(_CORPUS)
    assert index.search("zzzz qqqq", limit=3) == []


def test_a_limit_larger_than_the_corpus_does_not_raise():
    """bm25s raises on k > n_docs where a SQL LIMIT just returns fewer."""
    index = build_bm25(_CORPUS)
    hits = index.search("drones", limit=500)
    assert 0 < len(hits) <= len(_CORPUS)


def test_stemming_matches_a_different_inflection():
    index = build_bm25(["we describe the optimization of coverage", "unrelated text about cats"])
    assert index.search("optimizing coverage", limit=2)[0][0] == 0


def test_stopwords_do_not_decide_the_ranking():
    index = build_bm25(["the and of for revisit", "the and of for battery"])
    hits = index.search("the and of for battery", limit=2)
    assert hits[0][0] == 1


def test_save_and_load_preserve_the_ranking(tmp_path: Path):
    index = build_bm25(_CORPUS)
    before = index.search("revisit time", limit=3)

    save_bm25(index, tmp_path / "bm25")
    after = load_bm25(tmp_path / "bm25").search("revisit time", limit=3)

    assert [d for d, _ in after] == [d for d, _ in before]
    assert [round(s, 6) for _, s in after] == [round(s, 6) for _, s in before]


def test_an_empty_corpus_builds_and_searches_as_empty(tmp_path: Path):
    """A paper whose extraction produced no text must not make the index
    un-buildable."""
    index = build_bm25([])
    assert index.search("anything", limit=5) == []

    save_bm25(index, tmp_path / "bm25")
    assert load_bm25(tmp_path / "bm25").search("anything", limit=5) == []


def test_loading_a_missing_index_says_so(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="no BM25 index"):
        load_bm25(tmp_path / "never-built")
