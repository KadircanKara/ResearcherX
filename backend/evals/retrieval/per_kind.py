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
