"""Per-kind recall/MRR across the retrieval arms. Pure: no DB, no ORM.

The golden set types each positive case by the retriever it is meant to
exercise (`POSITIVE_KINDS`), so one pooled number hides which arm serves which
kind. This module scores every (kind, arm) cell from already-fetched ranked
lists; the runner supplies the lists.

`lists[arm][i]` is case i's ranked list for that arm. The dense arm is in
distance order, so `simulate_retrieval` sorts it; every other arm arrives in
its own rank order (sparse ranking or fused order) and may carry
`distance=None`, so it is scored with `presorted=True` and never re-sorted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from evals.retrieval.golden_set import POSITIVE_KINDS, Case
from evals.retrieval.metrics import Scored, mean_reciprocal_rank, recall_at_k

ARMS: tuple[str, ...] = ("dense", "sparse_sql", "sparse_bm25", "hybrid_sql", "hybrid_bm25")
RECALL_KS: tuple[int, ...] = (10, 20, 60)
_MRR_K = 60


@dataclass(frozen=True)
class KindRow:
    kind: str
    arm: str
    n: int
    recall: dict[int, float]
    mrr: float


def per_kind_table(
    cases: Sequence[Case], lists: Mapping[str, Sequence[Sequence[Scored]]]
) -> list[KindRow]:
    """One row per (kind, arm) that has at least one positive case.

    Off-topic cases are skipped: they have no expected evidence, so recall on
    them is meaningless.
    """
    rows: list[KindRow] = []
    for kind in POSITIVE_KINDS:
        idx = [i for i, c in enumerate(cases) if c.kind == kind]
        if not idx:
            continue
        for arm in ARMS:
            presorted = arm != "dense"
            pairs = [(cases[i], list(lists[arm][i])) for i in idx]
            rows.append(
                KindRow(
                    kind=kind,
                    arm=arm,
                    n=len(pairs),
                    recall={k: recall_at_k(pairs, k, presorted=presorted) for k in RECALL_KS},
                    mrr=mean_reciprocal_rank(pairs, _MRR_K, presorted=presorted),
                )
            )
    return rows


def macro(rows: list[KindRow], arm: str, metric: str) -> float:
    """Unweighted mean over the positive kinds present, so a 2-case kind
    weighs as much as a 30-case one. `metric` is "mrr" or "r@60"."""
    if metric not in ("mrr", "r@60"):
        raise ValueError(f"unknown metric {metric!r}")
    values = [r.mrr if metric == "mrr" else r.recall[60] for r in rows if r.arm == arm and r.n > 0]
    if not values:
        raise ValueError(f"no rows for arm {arm!r}")
    return sum(values) / len(values)


# --- HNSW against exact search (`run_eval --exact`) ---------------------------
#
# Each list below is case-aligned: index i is case i in every argument. The
# id lists are the dense arm's chunk ids in rank order, already gated, so
# they hold exactly what production's dense arm hands to fusion.


def _aligned(*lists: Sequence) -> int:
    lengths = {len(x) for x in lists}
    if len(lengths) != 1:
        raise ValueError(f"case-aligned lists differ in length: {sorted(lengths)}")
    return lengths.pop()


def hnsw_misses(
    exact: Sequence[Sequence[str]],
    hnsw: Sequence[Sequence[str]],
    answers: Sequence[set[str]],
) -> list[int]:
    """Indices of the cases whose answer exact search found and HNSW did not.

    `answers[i]` is the set of chunk ids that answer case i. A case neither
    arm answers is not a miss (that is a retrieval failure, not an ANN one),
    and neither is a case only HNSW answers. Identity of the other rows does
    not matter here: a pool that differs only in chunks nobody needs is
    ANN recall loss with no cost to the answer, which `differing_id_sets`
    reports separately.
    """
    _aligned(exact, hnsw, answers)
    return [
        i
        for i, (e, h, a) in enumerate(zip(exact, hnsw, answers, strict=True))
        if a.intersection(e) and not a.intersection(h)
    ]


def differing_id_sets(exact: Sequence[Sequence[str]], hnsw: Sequence[Sequence[str]]) -> list[int]:
    """Indices of the cases whose dense id SETS differ (order ignored)."""
    _aligned(exact, hnsw)
    return [i for i, (e, h) in enumerate(zip(exact, hnsw, strict=True)) if set(e) != set(h)]


@dataclass(frozen=True)
class ExactRow:
    kind: str
    n: int
    hnsw: float
    exact: float


def exact_vs_hnsw_by_kind(
    cases: Sequence[Case],
    hnsw: Sequence[Sequence[Scored]],
    exact: Sequence[Sequence[Scored]],
    *,
    k: int = 60,
) -> list[ExactRow]:
    """recall@k per positive kind for one arm run twice, HNSW and exact.

    Both lists arrive in the arm's own rank order (fused, or dense by
    d_rank), so neither is re-sorted."""
    _aligned(cases, hnsw, exact)
    rows: list[ExactRow] = []
    for kind in POSITIVE_KINDS:
        idx = [i for i, c in enumerate(cases) if c.kind == kind]
        if not idx:
            continue
        rows.append(
            ExactRow(
                kind=kind,
                n=len(idx),
                hnsw=recall_at_k([(cases[i], list(hnsw[i])) for i in idx], k, presorted=True),
                exact=recall_at_k([(cases[i], list(exact[i])) for i in idx], k, presorted=True),
            )
        )
    return rows


def _percentile(ordered: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy's default)."""
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def latency_summary(ms: Sequence[float]) -> tuple[float, float, float]:
    """(mean, p50, p95) of per-query latencies in milliseconds."""
    if not ms:
        raise ValueError("no latencies to summarise")
    ordered = sorted(ms)
    return sum(ordered) / len(ordered), _percentile(ordered, 0.5), _percentile(ordered, 0.95)
