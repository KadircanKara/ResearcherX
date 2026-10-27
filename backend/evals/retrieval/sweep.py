"""RRF weight / k sweep for the dense + bm25s hybrid, tuned on the tune split.

Pure: no DB, no ORM. The runner fetches each case's dense pool and bm25s
ranking ONCE (`CaseRankings`); every grid point then re-fuses those two id
lists with the IMPORTED `fuse_rrf` and scores the result through
`per_kind.per_kind_table`, so the sweep and production share one fusion.

Selection (`best`) must be handed tune-split rankings only. The test split is
used afterwards, to check the chosen point against the current settings, never
to choose it. Positives only: off-topic cases have no evidence to rank.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.core.config import settings
from app.services.hybrid_ranker import fuse_rrf
from evals.retrieval.golden_set import Case
from evals.retrieval.metrics import Scored
from evals.retrieval.per_kind import ARMS, macro, per_kind_table

W_DENSE_GRID: tuple[float, ...] = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
K_GRID: tuple[int, ...] = (5, 10, 20, 30, 60, 100)
_ARM = "hybrid_bm25"


def current_point() -> tuple[float, int]:
    """The (w_dense, k) production runs with, read from settings when asked."""
    return (settings.hybrid_dense_weight, settings.hybrid_rrf_k)


@dataclass(frozen=True)
class CaseRankings:
    case: Case
    dense: list[str]
    sparse: list[str]
    by_id: Mapping[str, Scored]


@dataclass(frozen=True)
class GridPoint:
    w_dense: float
    k: int
    per_kind: dict[str, tuple[float, float]]  # kind -> (mrr, r@60)
    macro_mrr: float
    macro_r60: float


def build_rankings(
    case: Case, hybrid_chunks: Sequence[Scored], sparse_rows: Sequence[Scored]
) -> CaseRankings:
    """Assemble one case's rankings from rows the runner already fetched.

    `hybrid_chunks` is `_hybrid_chunks_for`'s output (only its dense half,
    `d_rank` set, is used -- the same pool `fuse_with_bm25` fuses);
    `sparse_rows` is `sparse_only_ranking`'s bm25s ranking, best first.
    """
    dense_rows = sorted((r for r in hybrid_chunks if r.d_rank is not None), key=lambda r: r.d_rank)
    by_id: dict[str, Scored] = {r.chunk_id: r for r in sparse_rows}
    by_id.update({r.chunk_id: r for r in dense_rows})
    return CaseRankings(
        case=case,
        dense=[r.chunk_id for r in dense_rows],
        sparse=[r.chunk_id for r in sparse_rows],
        by_id=by_id,
    )


def evaluate_point(
    rankings: Sequence[CaseRankings], w_dense: float, k: int, *, budget: int
) -> GridPoint:
    """Fuse every case at (w_dense, 1 - w_dense, k), cut to `budget`, score."""
    fused_lists: list[list[Scored]] = []
    for r in rankings:
        fused = fuse_rrf(r.dense, r.sparse, w_dense=w_dense, w_sparse=1 - w_dense, k=k)
        fused_lists.append([r.by_id[key] for key, _ in fused[:budget]])
    cases = [r.case for r in rankings]
    # per_kind_table scores every arm; the dense arm is distance-sorted and a
    # fused list can carry distance=None, so it gets empty lists (unused).
    lists = {arm: fused_lists for arm in ARMS}
    lists["dense"] = [[] for _ in fused_lists]
    rows = per_kind_table(cases, lists)
    per_kind = {x.kind: (x.mrr, x.recall[60]) for x in rows if x.arm == _ARM}
    return GridPoint(
        w_dense=w_dense,
        k=k,
        per_kind=per_kind,
        macro_mrr=macro(rows, _ARM, "mrr"),
        macro_r60=macro(rows, _ARM, "r@60"),
    )


def sweep(rankings: Sequence[CaseRankings], *, budget: int) -> list[GridPoint]:
    return [evaluate_point(rankings, w, k, budget=budget) for w in W_DENSE_GRID for k in K_GRID]


def _distance_to_current(p: GridPoint) -> float:
    w, k = current_point()
    return abs(p.w_dense - w) / 0.1 + abs(math.log(p.k / k))


def best(points: Sequence[GridPoint]) -> GridPoint:
    """Highest macro MRR; ties by macro r@60, then nearest the current settings."""
    return min(
        points,
        key=lambda p: (
            -round(p.macro_mrr, 12),
            -round(p.macro_r60, 12),
            _distance_to_current(p),
        ),
    )


def _fmt_point(label: str, p: GridPoint) -> list[str]:
    lines = [
        f"  {label}: w_dense={p.w_dense} w_sparse={1 - p.w_dense:.1f} k={p.k}  "
        f"macro MRR {p.macro_mrr:.3f}  r@60 {p.macro_r60:.2f}"
    ]
    for kind, (mrr, r60) in p.per_kind.items():
        lines.append(f"    {kind:<14} MRR {mrr:.3f}  r@60 {r60:.2f}")
    return lines


def report(tune: Sequence[CaseRankings], test: Sequence[CaseRankings], *, budget: int) -> dict:
    """Print the grid and the chosen point; return the JSON `sweep` payload.

    The point is chosen on `tune` alone. `test` (possibly empty) only
    re-evaluates the chosen point and the current settings.
    """
    points = sweep(tune, budget=budget)
    chosen = best(points)
    by_cell = {(p.w_dense, p.k): p for p in points}
    # The shipped settings may sit off the grid; evaluate them explicitly then.
    cur = current_point()
    current = by_cell.get(cur) or evaluate_point(tune, *cur, budget=budget)

    print(f"RRF sweep on the tune split ({len(tune)} positive cases, bm25s sparse, no rerank):")
    print("  macro MRR; rows w_dense, cols k")
    print("  w_dense " + "".join(f"{k:>8}" for k in K_GRID))
    for w in W_DENSE_GRID:
        print(f"  {w:<7} " + "".join(f"{by_cell[(w, k)].macro_mrr:>8.3f}" for k in K_GRID))
    for line in _fmt_point("best (tune)", chosen) + _fmt_point("current (tune)", current):
        print(line)

    payload: dict = {
        "budget": budget,
        "n_tune": len(tune),
        "n_test": len(test),
        "grid": [vars(p) for p in points],
        "best_tune": vars(chosen),
        "current_tune": vars(current),
        "best_test": None,
        "current_test": None,
    }
    if test:
        chosen_test = evaluate_point(test, chosen.w_dense, chosen.k, budget=budget)
        current_test = evaluate_point(test, *cur, budget=budget)
        print(f"  held out: test split ({len(test)} positive cases)")
        for line in _fmt_point("best (test)", chosen_test) + _fmt_point(
            "current (test)", current_test
        ):
            print(line)
        payload["best_test"] = vars(chosen_test)
        payload["current_test"] = vars(current_test)
    return payload
