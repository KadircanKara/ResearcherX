"""Tests for the harness's bm25s sparse arm. Real bm25s, no DB, no network."""

from evals.retrieval.bm25_arm import (
    Bm25Corpus,
    CorpusChunk,
    fuse_with_bm25,
    index_text,
    sparse_holds_answer,
    sparse_row_count,
)
from evals.retrieval.golden_set import Case
from evals.retrieval.metrics import Scored

CHUNKS = [
    CorpusChunk(
        "c1", "p1", "Swarm Search", "particle swarm optimisation for target search", "2 Method"
    ),
    CorpusChunk("c2", "p1", "Swarm Search", "battery constraints limit flight time", "4 Results"),
    CorpusChunk("c3", "p2", "Edge Offloading", "task offloading to edge servers", "3 System Model"),
]

CASE = Case(
    id="battery",
    kind="content",
    question="How does battery affect flight time?",
    paper_title_contains="Swarm Search",
    expect_substrings=("battery constraints",),
)


def _dense(cid: str, rank: int, corpus: Bm25Corpus) -> Scored:
    c = corpus.by_id[cid]
    return Scored(
        paper_id=c.paper_id,
        paper_title=c.paper_title,
        chunk_text=c.text,
        distance=0.1 * rank,
        chunk_id=cid,
        d_rank=rank,
    )


def test_index_text_is_the_tsv_expression():
    assert index_text(CHUNKS[1]) == "4 Results battery constraints limit flight time"


def test_a_section_heading_alone_admits_a_chunk():
    """The tsv column indexes section_text too; so must the bm25s arm."""
    assert Bm25Corpus(CHUNKS).sparse_ranked("system model", 10) == ["c3"]


def test_sparse_ranked_returns_ids_best_first_and_drops_unmatched():
    ranked = Bm25Corpus(CHUNKS).sparse_ranked("battery flight time", 10)
    assert ranked == ["c2"]


def test_a_stopword_only_query_admits_nothing():
    assert Bm25Corpus(CHUNKS).sparse_ranked("what is the", 10) == []


def test_sparse_pool_caps_the_arm():
    ranked = Bm25Corpus(CHUNKS).sparse_ranked("search offloading battery", 2)
    assert len(ranked) == 2


def test_fusion_keeps_dense_fields_and_marks_sparse_only_admissions():
    corpus = Bm25Corpus(CHUNKS)
    hybrid_rows = [
        _dense("c1", 1, corpus),
        # A SQL-sparse-only row: no d_rank. The bm25s arm must drop it,
        # or the SQL sparse arm leaks into the arm meant to replace it.
        Scored(
            paper_id="p2",
            paper_title="Edge Offloading",
            chunk_text=CHUNKS[2].text,
            distance=None,
            chunk_id="c3",
            s_rank=1,
        ),
    ]
    fused = fuse_with_bm25(
        hybrid_rows, corpus, "battery flight", sparse_pool=10, w_dense=0.7, w_sparse=0.3, k=30
    )
    assert [c.chunk_id for c in fused] == ["c1", "c2"]
    c1, c2 = fused
    assert (c1.d_rank, c1.s_rank, c1.distance) == (1, None, 0.1)
    assert (c2.d_rank, c2.s_rank, c2.distance) == (None, 1, None)
    assert c2.chunk_text == CHUNKS[1].text


def test_sparse_holds_answer_reads_only_sparse_rows():
    corpus = Bm25Corpus(CHUNKS)
    dense_hit = [_dense("c2", 1, corpus)]
    assert not sparse_holds_answer(CASE, dense_hit)
    fused = fuse_with_bm25(
        [], corpus, CASE.question, sparse_pool=10, w_dense=0.7, w_sparse=0.3, k=30
    )
    assert sparse_holds_answer(CASE, fused)
    assert sparse_row_count(fused) == 1
