"""A bm25s sparse arm for the retrieval harness — a candidate, not production.

Production's sparse arm is Postgres full-text search: `tsv @@ tsquery`
admission ordered by `ts_rank_cd`, which carries no IDF and no length
normalisation. This module answers whether real BM25 over the SAME chunks
would be a better lexical arm, before anyone wires it into `chat_service`.

What is held equal, so the comparison measures the scorer and nothing else:
- the documents: every chunk row of the project at `settings.embedding_model`,
  read from Postgres, indexed as `section_text || ' ' || text` -- exactly the
  expression the generated `tsv` column is built over (migration
  `d0e1f2a3b4c5`);
- the dense arm: the same `d_rank` ordering the hybrid query returns;
- fusion: the IMPORTED `fuse_rrf` with production's weights, `k` and sparse
  pool size.

The BM25 index is built by production's own `bm25_build.build_index`, so this
arm and the production sparse leg score identically.

Note the RRF weights and `k` were tuned against `ts_rank_cd`'s rankings
(see `hybrid_rrf_k` in config.py). A bm25s arm that loses here at those
constants has not been shown to lose at its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.local_rag.index import BM25Index
from app.services import bm25_tokenizer
from app.services.bm25_build import EmptyCorpus, build_index
from app.services.hybrid_ranker import fuse_rrf
from evals.retrieval.golden_set import Case, chunk_satisfies
from evals.retrieval.metrics import Scored


@dataclass(frozen=True)
class CorpusChunk:
    chunk_id: str
    paper_id: str
    paper_title: str
    text: str
    section_text: str


def index_text(chunk: CorpusChunk) -> str:
    """What BM25 sees for a chunk: the `tsv` column's own expression."""
    return bm25_tokenizer.index_text(chunk.section_text, chunk.text)


class Bm25Corpus:
    """One project's chunks and their BM25 index, built once per run."""

    def __init__(self, chunks: Sequence[CorpusChunk]) -> None:
        self.chunks = list(chunks)
        self.by_id = {c.chunk_id: c for c in self.chunks}
        try:
            retriever = build_index(
                [c.chunk_id for c in self.chunks], [index_text(c) for c in self.chunks]
            )
            self._index = BM25Index(retriever=retriever, n_docs=len(self.chunks))
        except EmptyCorpus:
            self._index = BM25Index(retriever=None, n_docs=0)

    def sparse_ranked(self, query: str, limit: int) -> list[str]:
        """Chunk ids, best first, zero scores dropped -- the bm25s analogue of
        the SQL arm's `tsv @@ tsq ... LIMIT :sparse_pool`."""
        return [self.chunks[doc].chunk_id for doc, _ in self._index.search(query, limit)]


def fuse_with_bm25(
    hybrid_rows: Sequence[Scored],
    corpus: Bm25Corpus,
    query: str,
    *,
    sparse_pool: int,
    w_dense: float,
    w_sparse: float,
    k: int,
) -> list[Scored]:
    """The hybrid arm with the SQL sparse ranking swapped for bm25s.

    `hybrid_rows` is `_hybrid_chunks_for`'s output; only its dense half
    (`d_rank` set) is kept. The result has the same shape as that output --
    fused order, `distance`/`d_rank` None on a sparse-only admission -- so it
    feeds the same presorted metrics and nothing distance-sorted.
    """
    dense = {r.chunk_id: r for r in hybrid_rows if r.d_rank is not None}
    dense_ranked = [r.chunk_id for r in sorted(dense.values(), key=lambda r: r.d_rank)]
    sparse_ranked = corpus.sparse_ranked(query, sparse_pool)
    s_rank = {cid: i for i, cid in enumerate(sparse_ranked, start=1)}
    fused = fuse_rrf(dense_ranked, sparse_ranked, w_dense=w_dense, w_sparse=w_sparse, k=k)
    out: list[Scored] = []
    for cid, _ in fused:
        row = dense.get(cid)
        chunk = corpus.by_id[cid] if row is None else None
        out.append(
            Scored(
                paper_id=row.paper_id if row else chunk.paper_id,
                paper_title=row.paper_title if row else chunk.paper_title,
                chunk_text=row.chunk_text if row else chunk.text,
                distance=row.distance if row else None,
                chunk_id=cid,
                d_rank=row.d_rank if row else None,
                s_rank=s_rank.get(cid),
            )
        )
    return out


def sparse_only_ranking(corpus: Bm25Corpus, query: str, limit: int) -> list[Scored]:
    """The bm25s sparse arm ALONE as `Scored` rows, best first.

    There is no dense half here, so `distance`/`d_rank` are None -- the same
    convention a sparse-only admission uses in the fused lists. Score it with
    `presorted=True` only.
    """
    out: list[Scored] = []
    for rank, cid in enumerate(corpus.sparse_ranked(query, limit), start=1):
        chunk = corpus.by_id[cid]
        out.append(
            Scored(
                paper_id=chunk.paper_id,
                paper_title=chunk.paper_title,
                chunk_text=chunk.text,
                distance=None,
                chunk_id=cid,
                s_rank=rank,
            )
        )
    return out


def sparse_holds_answer(case: Case, chunks: Sequence[Scored]) -> bool:
    """Did the SPARSE arm alone return an answering chunk, at any rank?

    The diagnostic the AND-ed `websearch_to_tsquery` failed (6 of 30): an arm
    that never returns the answer cannot help however it is weighted.
    """
    return any(
        c.s_rank is not None and chunk_satisfies(case, c.paper_title, c.chunk_text) for c in chunks
    )


def sparse_row_count(chunks: Sequence[Scored]) -> int:
    return sum(1 for c in chunks if c.s_rank is not None)
