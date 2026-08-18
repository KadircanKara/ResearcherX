"""Hybrid rank fusion.

Dense retrieval ranks by cosine distance; sparse retrieval ranks by lexical
overlap. Their SCORES are not comparable -- cosine distance is bounded [0, 2]
and comparable across queries, while `ts_rank_cd` is unbounded and depends on
query length and corpus statistics -- so a weighted sum of the raw values is
arithmetic on incompatible units. Per-query min-max normalization does not fix
it either: a chunk's normalized score then depends on which OTHER chunks came
back, so no tuned constant is reproducible.

Reciprocal Rank Fusion consumes RANKS instead, which is scale-free and
reproducible, and it keeps the weights meaningful:

    score(c) = w_dense / (k + rank_dense(c)) + w_sparse / (k + rank_sparse(c))

A chunk missing from one arm simply contributes nothing from that arm -- which
is what lets the sparse arm surface a chunk the dense distance gate rejected,
the failure this module exists to fix (see the `max_context_chunks` block in
app/core/config.py: the answering chunk at global rank 53, and at rank 771
under a different phrasing of the same question).

Pure on purpose: `evals/retrieval/run_eval.py --hybrid` imports these exact
functions, so the harness can never measure a policy production does not run.
"""

from collections.abc import Hashable, Sequence
from typing import TypeVar

K = TypeVar("K", bound=Hashable)


def fuse_rrf(
    dense_ranked: Sequence[K],
    sparse_ranked: Sequence[K],
    *,
    w_dense: float,
    w_sparse: float,
    k: int,
) -> list[tuple[K, float]]:
    """Fuse two ranked key sequences into one, best first.

    Rank is POSITION, 1-based: `dense_ranked[0]` is dense rank 1. Passing
    ranked sequences rather than scores is what keeps this function free of
    both DB types and score-scale assumptions.

    Ties are broken by dense rank, then sparse rank, then insertion order --
    never by set or dict iteration order. A nondeterministic retrieval order
    would make the eval harness unrepeatable, which is worse than any
    particular tie-break rule being arbitrary.
    """
    dense_rank: dict[K, int] = {}
    for position, key in enumerate(dense_ranked, start=1):
        dense_rank.setdefault(key, position)
    sparse_rank: dict[K, int] = {}
    for position, key in enumerate(sparse_ranked, start=1):
        sparse_rank.setdefault(key, position)

    # Insertion order: dense arm first, then sparse-only keys. Used as the
    # last tie-break so the result is a pure function of the inputs.
    order: dict[K, int] = {}
    for key in list(dense_ranked) + list(sparse_ranked):
        order.setdefault(key, len(order))

    scored: list[tuple[K, float]] = []
    for key in order:
        score = 0.0
        if key in dense_rank:
            score += w_dense / (k + dense_rank[key])
        if key in sparse_rank:
            score += w_sparse / (k + sparse_rank[key])
        scored.append((key, score))

    missing = len(order) + 1
    scored.sort(
        key=lambda pair: (
            -pair[1],
            dense_rank.get(pair[0], missing),
            sparse_rank.get(pair[0], missing),
            order[pair[0]],
        )
    )
    return scored


def keep_within_rank_window(fused_scores: Sequence[float], *, window: int) -> int:
    """How many fused chunks to keep when retrieval is scoped to ONE paper.

    Returns a PREFIX LENGTH, not a filtered list, matching
    `intra_paper_ranker.keep_within_paper`: the caller holds the rows and
    slices them itself.

    This REPLACES the distance-space `intra_paper_delta` cut under hybrid
    retrieval, because RRF emits a rank, not a distance, and a constant tuned
    as `best_distance + delta` has no meaning against a fused score. It takes
    `fused_scores` rather than a bare count so a future score-relative policy
    can replace the body without changing a single call site.
    """
    if window <= 0:
        return 0
    return min(len(fused_scores), window)
