"""The retrieval pipeline, and all of it:

    dense (numpy cosine)  ┐
                          ├─ weighted RRF ─ top candidates ─ Cohere rerank ─ top-N
    BM25 (bm25s)          ┘

Admission is DISJUNCTIVE, as in the SQL path this replaces: a chunk reaches
the fusion by clearing the dense distance gate OR by scoring in the BM25
top-N. That is what lets a lexically exact chunk the embedding missed still
arrive.

Fusion is over RANKS, never raw scores. Cosine distance is bounded [0,2] and
comparable across queries; a BM25 score is unbounded and depends on the
corpus statistics, so a weighted sum of the two is arithmetic on
incompatible units. `fuse_rrf` is imported from `app.services.hybrid_ranker`
rather than reimplemented — it is pure, already tested, and a second copy
would drift.

The reranker is OPTIONAL at every level: `None`, or one that fails, leaves
the fused order in place. It decides ordering, never whether a turn can be
answered.

One distance gate, no scope-dependent switching. The database path carries
a second, looser gate for single-paper scope plus two cut policies on top;
each was measured into existence and none is reproduced here (see this
package's `__init__`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.local_rag.index import BM25Index
from app.local_rag.rerank import RerankResult
from app.local_rag.store import Chunk, LocalStore
from app.services.hybrid_ranker import fuse_rrf


class Reranker(Protocol):
    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankResult] | None: ...


@dataclass(frozen=True)
class SearchParams:
    """Operator-set, 2026-09-06. Equal arm weights, RRF k=60, Cohere with
    its own defaults over 50 candidates down to 10."""

    similarity_threshold: float = 0.75
    dense_pool: int = 100
    sparse_pool: int = 100
    dense_weight: float = 0.5
    sparse_weight: float = 0.5
    rrf_k: int = 60
    rerank_candidates: int = 50
    rerank_top_n: int = 10


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk. `n` is its citation number in THIS answer."""

    n: int
    chunk: Chunk
    dense_rank: int | None
    sparse_rank: int | None
    rerank_score: float | None


def dense_ranking(
    vectors: np.ndarray, query_vector: np.ndarray, threshold: float, limit: int
) -> list[tuple[int, float]]:
    """`(row index, cosine distance)` under `threshold`, nearest first.

    Norms are computed here rather than assumed: an embedding provider that
    returns unnormalised vectors would otherwise silently turn cosine into
    a dot product, changing every ranking with no error anywhere.
    """
    if vectors.shape[0] == 0 or limit <= 0:
        return []
    norms = np.linalg.norm(vectors, axis=1)
    query_norm = float(np.linalg.norm(query_vector))
    if query_norm == 0:
        return []
    # A zero-norm row (an empty chunk that still got embedded) would divide
    # by zero; give it similarity 0, i.e. distance 1, and let the gate rule.
    safe = np.where(norms == 0, 1.0, norms)
    similarity = (vectors @ query_vector) / (safe * query_norm)
    similarity = np.where(norms == 0, 0.0, similarity)
    distance = 1.0 - similarity

    order = np.argsort(distance, kind="stable")
    hits: list[tuple[int, float]] = []
    for row in order:
        d = float(distance[row])
        if d >= threshold:
            break  # sorted ascending: everything after is further
        hits.append((int(row), d))
        if len(hits) == limit:
            break
    return hits


async def search(
    *,
    store: LocalStore,
    bm25: BM25Index,
    query: str,
    query_vector: np.ndarray,
    reranker: Reranker | None,
    params: SearchParams,
) -> list[Hit]:
    dense = dense_ranking(
        store.vectors, query_vector, params.similarity_threshold, params.dense_pool
    )
    sparse = bm25.search(query, limit=params.sparse_pool)

    dense_rank = {row: rank for rank, (row, _) in enumerate(dense, start=1)}
    sparse_rank = {row: rank for rank, (row, _) in enumerate(sparse, start=1)}

    fused = fuse_rrf(
        [row for row, _ in dense],
        [row for row, _ in sparse],
        w_dense=params.dense_weight,
        w_sparse=params.sparse_weight,
        k=params.rrf_k,
    )
    candidates = [row for row, _ in fused][: params.rerank_candidates]
    if not candidates:
        return []

    scores: dict[int, float] = {}
    ordered = candidates
    if reranker is not None:
        results = await reranker.rerank(
            query, [store.chunks[row].text for row in candidates], params.rerank_top_n
        )
        # `None` is the declared fail-open signal, and an EMPTY verdict over
        # a non-empty candidate set is treated the same way: Cohere returns
        # `top_n` results ranked, it does not filter, so nothing back is a
        # provider anomaly rather than "none of these are relevant".
        # Honouring it literally would turn that anomaly into a refusal.
        if results:
            ordered = [candidates[r.index] for r in results]
            scores = {candidates[r.index]: r.relevance_score for r in results}

    ordered = ordered[: params.rerank_top_n]
    return [
        Hit(
            n=i,
            chunk=store.chunks[row],
            dense_rank=dense_rank.get(row),
            sparse_rank=sparse_rank.get(row),
            rerank_score=scores.get(row),
        )
        for i, row in enumerate(ordered, start=1)
    ]
