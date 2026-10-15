"""Rerank ORDERING, and only the ordering.

Pure, no DB, no ORM types and no network, matching the hybrid_ranker.py /
mention_ranker.py / intra_paper_ranker.py convention: production
(`chat_service`) and the eval harnesses (`evals/retrieval/run_eval.py`,
`evals/retrieval/mention_eval.py`) import this same function, so the policy
that ships cannot drift from the policy that is measured. The harnesses
MIRROR production's SQL rather than importing it, and that is exactly how
the per-paper guarantee's absence hid from every mirrored arm on
2026-08-18 — the rerank does not get a second copy.

The network call lives in `app/local_rag/rerank.py::CohereReranker`, whose
contract is that `None` means "no opinion".
`JevReranker` (`app/services/jev_reranker.py`) honours the same contract.
"""

from collections.abc import Callable, Sequence
from typing import Protocol, TypeVar

from app.local_rag.rerank import RerankResult

T = TypeVar("T")


class Reranker(Protocol):
    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankResult] | None: ...


def apply_rerank(
    items: Sequence[T],
    results: Sequence[RerankResult] | None,
    *,
    min_score: float | None = None,
) -> list[T]:
    """`items` reordered by relevance score, best first.

    `results` addresses `items` by index and may cover only a prefix of them
    (the client asks for a `top_n`). Scored items lead, in descending score;
    every UNSCORED item follows in its original relative order.

    Nothing is dropped. The rerank decides ORDER, never admission: the
    caller slices the result to the context budget, so discarding unscored
    candidates here would shrink the pool below that budget and silently
    turn an ordering stage into a filter. It also never rescues a chunk the
    distance gate cut — a gated chunk is not in `items` to begin with.

    `results is None` is the client's fail-open signal (an outage, a missing
    key, a malformed response) and returns the input order unchanged. An
    empty `results` is the client's answer for an empty document list and
    behaves the same way, deliberately: neither means "drop everything".

    Scores are sorted here rather than trusted to arrive sorted. The output
    of this function IS the order the model sees its excerpts in, and a
    provider returning results in request order would otherwise reorder the
    catalog with no error anywhere.

    A duplicated index is honoured once and an unmappable index is ignored:
    the client already filters out-of-range indices, and this is the second
    guard, because an index that addresses the wrong candidate reads as a
    plausible answer citing the wrong paper.

    `min_score` is the ONE exception to "nothing is dropped", and it is
    opt-in (`rerank_min_score`, off by default): when set and the scorer
    answered, items scored below it AND every unscored item are dropped, so
    the result can be empty. An empty catalog is the point -- an off_topic
    question then reaches the model with nothing and it refuses. A scorer
    that failed open (`results` empty or None) still returns everything:
    an outage must never read as "nothing is relevant".
    """
    if not results:
        return list(items)

    ordered = sorted(results, key=lambda r: r.relevance_score, reverse=True)
    if min_score is not None:
        ordered = [r for r in ordered if r.relevance_score >= min_score]

    scored: list[T] = []
    taken: set[int] = set()
    for result in ordered:
        index = result.index
        if index < 0 or index >= len(items) or index in taken:
            continue
        taken.add(index)
        scored.append(items[index])

    if min_score is not None:
        return scored
    tail = [item for i, item in enumerate(items) if i not in taken]
    return scored + tail


async def rerank_items(
    items: Sequence[T],
    *,
    query: str,
    text_of: Callable[[T], str],
    reranker: Reranker | None,
    limit: int,
    min_score: float | None = None,
) -> list[T]:
    """The whole rerank stage: send a head, reorder it, keep the tail.

    This composition lives here, beside `apply_rerank`, rather than at each
    call site. `chat_service` and both eval harnesses call THIS -- the
    harnesses mirror production's SQL rather than importing it, and a
    hand-rolled head/tail split in each would be a second copy of the exact
    policy under measurement. That drift is not hypothetical: when the
    per-paper guarantee shipped on 2026-08-18 every mirrored arm went on
    measuring the older shape with no error anywhere.

    Impure only in that it awaits the INJECTED `reranker`; it opens no
    client and reads no settings, so a fake makes it fully testable.

    Only the first `limit` items are scored. The rest keep their incoming
    order behind them, so a candidate list longer than `limit` degrades to
    "reranked head, incoming tail" rather than to an arbitrary order.

    `reranker is None` -- the kill switch off, or no API key -- skips the
    call entirely and returns the incoming order. That is the same outcome
    as a reranker that fails, by design: one degraded path, not two.

    With `min_score` set (see `apply_rerank`) and a scorer that answered,
    the unscored tail past `limit` is dropped too: it was never judged, so
    it cannot pass a cutoff.
    """
    if reranker is None or not items:
        return list(items)
    head = list(items[:limit])
    results = await reranker.rerank(
        query=query,
        documents=[text_of(item) for item in head],
        # Every item sent gets a score back: this stage orders a list, it
        # does not select from one. The caller's budget cut is what selects.
        top_n=len(head),
    )
    if min_score is not None and results:
        return apply_rerank(head, results, min_score=min_score)
    return apply_rerank(head, results) + list(items[limit:])


async def rank_candidates(
    items: Sequence[T],
    *,
    query: str,
    text_of: Callable[[T], str],
    fusion_scorer: Reranker | None,
    reranker: Reranker | None,
    limit: int,
    min_score: float | None = None,
) -> list[T]:
    """Both ranking stages after RRF: an optional fusion scorer, then the rerank head.

    `fusion_scorer` (FUSION_MODE=jev) scores EVERY candidate -- the whole
    dense ∪ sparse union, already in RRF order -- and its order replaces
    RRF's. RRF's order survives only where the scorer has no opinion (it
    failed open), which is the same degraded path `rerank_items` has.
    Then `reranker` orders the first `limit` of that, exactly as before.

    Lives here, not at the call sites, for the reason `rerank_items` does:
    `chat_service` and both eval harnesses call this one function, so the
    arms the harness measures are the arms production can run.

    `min_score` applies to the LAST stage that runs -- the reranker when
    there is one, else the fusion scorer -- because that stage's scores set
    the final order. Its scale is that provider's own (Jev noul is a
    probability; Cohere's relevance is calibrated differently), so a value
    measured for one stage means nothing for another.
    """
    ordered = list(items)
    if fusion_scorer is not None:
        ordered = await rerank_items(
            ordered,
            query=query,
            text_of=text_of,
            reranker=fusion_scorer,
            limit=len(ordered),
            min_score=min_score if reranker is None else None,
        )
    return await rerank_items(
        ordered,
        query=query,
        text_of=text_of,
        reranker=reranker,
        limit=limit,
        min_score=min_score if reranker is not None else None,
    )
