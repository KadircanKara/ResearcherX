"""app/local_rag/search.py — dense + BM25 -> RRF -> Cohere rerank.

The whole retrieval system. Every stage is pinned here, plus the two
properties that make the pipeline honest: admission is DISJUNCTIVE (a chunk
the dense gate rejected can still arrive through BM25), and the reranker is
OPTIONAL (its absence changes the order, never whether an answer happens).
"""

from pathlib import Path

import numpy as np
import pytest

from app.local_rag.index import build_bm25
from app.local_rag.rerank import RerankResult
from app.local_rag.search import SearchParams, dense_ranking, search
from app.local_rag.store import Chunk, LocalStore, Paper

# query is [1,0,0,0]; cosine distance = 1 - cos_sim
_QUERY_VEC = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
_TEXTS = [
    "swarm coverage path planning",  # distance 0.0
    "battery chemistry charging",  # distance 0.4
    "revisit time minimisation surveys",  # distance 1.0 — outside the gate
]
_VECTORS = np.array(
    [[1.0, 0.0, 0.0, 0.0], [0.6, 0.8, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], dtype=np.float32
)


def _store(texts=None, vectors=None) -> LocalStore:
    texts = _TEXTS if texts is None else texts
    vectors = _VECTORS if vectors is None else vectors
    paper = Paper(paper_id="p1", title="Test Paper")
    chunks = [
        Chunk(paper_id="p1", paper_title="Test Paper", chunk_index=i, text=t)
        for i, t in enumerate(texts)
    ]
    return LocalStore(
        root=Path("/nonexistent"),
        papers=[paper],
        chunks=chunks,
        vectors=vectors,
        embedding_model="test",
    )


class _FakeReranker:
    """Returns the order it was constructed with, and records what it saw."""

    def __init__(self, order: list[int] | None):
        self._order = order
        self.seen_documents: list[str] | None = None
        self.seen_top_n: int | None = None

    async def rerank(self, query: str, documents: list[str], top_n: int):
        self.seen_documents = documents
        self.seen_top_n = top_n
        if self._order is None:
            return None
        return [
            RerankResult(index=i, relevance_score=1.0 - rank / 100)
            for rank, i in enumerate(self._order)
            if i < len(documents)
        ]


# ── dense arm ────────────────────────────────────────────────────────────


def test_dense_ranking_orders_by_cosine_distance_ascending():
    ranked = dense_ranking(_VECTORS, _QUERY_VEC, threshold=2.0, limit=10)
    assert [i for i, _ in ranked] == [0, 1, 2]
    assert ranked[0][1] == pytest.approx(0.0, abs=1e-6)
    assert ranked[1][1] == pytest.approx(0.4, abs=1e-6)


def test_dense_ranking_drops_chunks_past_the_threshold():
    ranked = dense_ranking(_VECTORS, _QUERY_VEC, threshold=0.75, limit=10)
    assert [i for i, _ in ranked] == [0, 1]


def test_dense_ranking_respects_the_limit():
    assert len(dense_ranking(_VECTORS, _QUERY_VEC, threshold=2.0, limit=1)) == 1


def test_dense_ranking_on_an_empty_matrix_returns_nothing():
    empty = np.zeros((0, 4), dtype=np.float32)
    assert dense_ranking(empty, _QUERY_VEC, threshold=0.75, limit=10) == []


def test_a_zero_vector_chunk_does_not_crash_the_cosine():
    vectors = np.array([[0.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]], dtype=np.float32)
    ranked = dense_ranking(vectors, _QUERY_VEC, threshold=2.0, limit=10)
    assert ranked[0][0] == 1


# ── the pipeline ─────────────────────────────────────────────────────────


async def test_returns_chunks_ordered_by_the_fused_ranking_when_no_reranker():
    store = _store()
    hits = await search(
        store=store,
        bm25=build_bm25(_TEXTS),
        query="swarm coverage",
        query_vector=_QUERY_VEC,
        reranker=None,
        params=SearchParams(),
    )
    assert hits[0].chunk.chunk_index == 0


async def test_a_chunk_outside_the_dense_gate_still_arrives_through_bm25():
    """Disjunctive admission: this is the whole point of a hybrid arm, and
    chunk 2 is at cosine distance 1.0, well past the 0.75 gate."""
    store = _store()
    hits = await search(
        store=store,
        bm25=build_bm25(_TEXTS),
        query="revisit time minimisation",
        query_vector=_QUERY_VEC,
        reranker=None,
        params=SearchParams(),
    )
    assert 2 in [h.chunk.chunk_index for h in hits]


async def test_a_sparse_only_rank_one_outranks_a_dense_only_rank_three():
    """Pins the operator-set fusion constants: at equal weights and k=60,
    0.5/(60+1) > 0.5/(60+3), so an arm's best hit beats the other arm's
    third. At 0.7/0.3 it would not."""
    texts = ["alpha", "beta", "gamma", "delta lexical-marker"]
    vectors = np.array(
        [
            [1.0, 0.0, 0.0, 0.0],  # dense rank 1
            [0.9, 0.44, 0.0, 0.0],  # dense rank 2
            [0.8, 0.6, 0.0, 0.0],  # dense rank 3
            [0.75, 0.66, 0.0, 0.0],  # dense rank 4, and sparse rank 1
        ],
        dtype=np.float32,
    )
    hits = await search(
        store=_store(texts, vectors),
        bm25=build_bm25(texts),
        query="lexical-marker",
        query_vector=_QUERY_VEC,
        reranker=None,
        params=SearchParams(),
    )
    order = [h.chunk.chunk_index for h in hits]
    assert order.index(3) < order.index(2)


class _ReversingReranker:
    """Inverts whatever order the fusion produced — the strongest available
    evidence that the final order is the reranker's and not RRF's."""

    async def rerank(self, query: str, documents: list[str], top_n: int):
        return [
            RerankResult(index=i, relevance_score=rank / 100)
            for rank, i in enumerate(reversed(range(len(documents))))
        ]


async def test_the_reranker_decides_the_final_order():
    store = _store()
    kwargs = dict(
        store=store,
        bm25=build_bm25(_TEXTS),
        query="revisit time minimisation",
        query_vector=_QUERY_VEC,
        params=SearchParams(),
    )
    fused = await search(reranker=None, **kwargs)
    reranked = await search(reranker=_ReversingReranker(), **kwargs)

    assert len(fused) > 1, "the fixture must admit more than one chunk to be meaningful"
    assert [h.chunk.chunk_index for h in reranked] == [h.chunk.chunk_index for h in reversed(fused)]
    assert reranked[0].rerank_score is not None


async def test_a_reranker_returning_none_keeps_the_fused_order():
    """Fail-open: an outage changes the ORDER, never whether the turn
    answers."""
    store = _store()
    fused = await search(
        store=store,
        bm25=build_bm25(_TEXTS),
        query="swarm coverage",
        query_vector=_QUERY_VEC,
        reranker=None,
        params=SearchParams(),
    )
    degraded = await search(
        store=store,
        bm25=build_bm25(_TEXTS),
        query="swarm coverage",
        query_vector=_QUERY_VEC,
        reranker=_FakeReranker(order=None),
        params=SearchParams(),
    )
    assert [h.chunk.chunk_index for h in degraded] == [h.chunk.chunk_index for h in fused]
    assert all(h.rerank_score is None for h in degraded)


async def test_a_reranker_returning_no_results_at_all_keeps_the_fused_order():
    """An empty verdict over a non-empty candidate set is a provider
    anomaly, not a judgement that nothing is relevant — Cohere returns
    top_n results, it does not filter. Dropping every hit here would turn
    an anomaly into a refusal."""
    store = _store()
    kwargs = dict(
        store=store,
        bm25=build_bm25(_TEXTS),
        query="swarm coverage",
        query_vector=_QUERY_VEC,
        params=SearchParams(),
    )
    fused = await search(reranker=None, **kwargs)
    empty_verdict = await search(reranker=_FakeReranker(order=[]), **kwargs)

    assert [h.chunk.chunk_index for h in empty_verdict] == [h.chunk.chunk_index for h in fused]


async def test_the_reranker_is_given_at_most_rerank_candidates_documents():
    texts = [f"document number {i}" for i in range(40)]
    vectors = np.tile(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32), (40, 1))
    reranker = _FakeReranker(order=list(range(5)))
    await search(
        store=_store(texts, vectors),
        bm25=build_bm25(texts),
        query="document",
        query_vector=_QUERY_VEC,
        reranker=reranker,
        params=SearchParams(rerank_candidates=7, rerank_top_n=3),
    )
    assert len(reranker.seen_documents) == 7
    assert reranker.seen_top_n == 3


async def test_results_are_capped_at_rerank_top_n():
    texts = [f"document number {i}" for i in range(40)]
    vectors = np.tile(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32), (40, 1))
    hits = await search(
        store=_store(texts, vectors),
        bm25=build_bm25(texts),
        query="document",
        query_vector=_QUERY_VEC,
        reranker=None,
        params=SearchParams(rerank_top_n=4),
    )
    assert len(hits) == 4


async def test_citation_numbers_are_one_to_n_in_final_order():
    """All three chunks are admitted here — 0 and 1 by the dense gate, 2 by
    BM25 alone — so the numbering covers a full catalog."""
    hits = await search(
        store=_store(),
        bm25=build_bm25(_TEXTS),
        query="revisit time minimisation",
        query_vector=_QUERY_VEC,
        reranker=_ReversingReranker(),
        params=SearchParams(),
    )
    assert len(hits) == 3
    assert [h.n for h in hits] == [1, 2, 3]


async def test_an_empty_store_returns_no_hits():
    store = _store(texts=[], vectors=np.zeros((0, 4), dtype=np.float32))
    hits = await search(
        store=store,
        bm25=build_bm25([]),
        query="anything",
        query_vector=_QUERY_VEC,
        reranker=None,
        params=SearchParams(),
    )
    assert hits == []


async def test_a_query_matching_nothing_in_either_arm_returns_no_hits():
    """The refusal path depends on this: an empty catalog is what makes the
    model decline instead of answering from memory."""
    far_query = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)
    hits = await search(
        store=_store(),
        bm25=build_bm25(_TEXTS),
        query="zzzz qqqq",
        query_vector=far_query,
        reranker=None,
        params=SearchParams(),
    )
    assert hits == []


def test_the_shipped_defaults_are_the_operator_set_ones():
    params = SearchParams()
    assert params.dense_weight == 0.5
    assert params.sparse_weight == 0.5
    assert params.rrf_k == 60
    assert params.rerank_candidates == 50
    assert params.rerank_top_n == 10
