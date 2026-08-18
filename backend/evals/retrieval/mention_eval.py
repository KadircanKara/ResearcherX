"""Multi-mention retrieval measurement — measures, never mutates.

Run:  docker compose exec -T backend python -m evals.retrieval.mention_eval \
          --project-id <uuid>

The `-m` form is required for the same reason `run_eval.py` documents:
`pyproject.toml` packages only `app*`, so `evals` is not installed and
file-path invocation drops cwd from sys.path.

WHAT THIS MEASURES THAT `run_eval.py` DOES NOT. `run_eval.py` scopes every
case to at most ONE paper (`_scope_to_paper` / `_scope_to_nearest_paper`), so
it can measure neither the multi-mention SQL gate nor
`apply_per_paper_floor` — it does not import `mention_ranker` at all. This
module closes that gap: it builds a TWO-mention scope for every golden-set
case and runs the production mention path end to end, importing the floor
from `app.services.mention_ranker` rather than reproducing it.

THE QUESTION. `chat_service._retrieve_paper_chunks` picks its SQL cutoff from
the SIZE of the scope: `single_paper = len(paper_infos) == 1` uses the loose
`intra_paper_ceiling` (0.85), anything larger falls back to the global
`similarity_threshold` (0.75) applied to each paper individually. A paper
whose nearest chunk sits at 0.78 therefore contributes everything when named
alone and NOTHING when named alongside a second paper, and the floor cannot
repair that — it reorders candidates, it cannot resurrect rows SQL never
returned. Three arms are measured:

  status-quo   flat 0.75 over the merged two-paper scope (what ships today)
  policy-A     flat 0.85 over the merged two-paper scope (one looser gate)
  policy-B     per-paper admission: each mentioned paper is queried ALONE
               (so it gets production's single-paper treatment — the 0.85
               ceiling and its own dense/sparse pools), admitted only if its
               own nearest chunk clears `similarity_threshold`, cut against
               its own nearest chunk by production's single-paper cut, then
               merged and floored.
  policy-B*    policy B with the admission gate at `intra_paper_ceiling`
               instead, which separates "which gate admits the PAPER" from
               "which gate retains its CHUNKS". Free — same fetches as B.

THREE PAIRINGS, AND ONE OF THEM IS REAL. `nearest` and `seeded` bolt a
SYNTHETIC second mention onto a golden-set case that asks about one paper;
they are kept unchanged because every number recorded in README.md's
"Measured — 2026-08-18" block was taken with them, and dropping them would
break comparability. The `real` pairing runs `comparison_set.json` instead:
genuine "how do @A and @B differ" questions whose answer is SPLIT across two
papers, with per-paper ground truth for each half. That is the regime the
2026-08-18 measurement listed as its biggest gap — a blended query under which
BOTH papers' nearest-chunk distances can sit higher than anything the
single-paper questions produce. Its results are reported separately, with
per-SIDE survival, because a comparison in which only one half of the answer
reached the model is a failure that a single `survived` boolean hides.

POLICY B IS SIMULATED HERE. It never shipped, so there is no production
function to import for its admission decision or its merge; both live in
`mention_metrics.py` and are labelled as harness code. Everything policy B
reuses from production (the single-paper SQL gate, `keep_within_paper` /
`keep_within_rank_window`, `apply_per_paper_floor`) is imported, never
reproduced. Nothing in this module writes to the database or changes a
setting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.embedding_service import EmbeddingService
from app.services.hybrid_ranker import fuse_rrf, keep_within_rank_window
from app.services.intra_paper_ranker import keep_within_paper
from app.services.mention_ranker import apply_per_paper_floor
from evals.retrieval.comparison_set import ComparisonCase, load_comparison_set
from evals.retrieval.golden_set import Case, chunk_satisfies, load_golden_set
from evals.retrieval.mention_metrics import (
    ComparisonOutcome,
    MentionOutcome,
    PaperBest,
    admitted_papers,
    answer_paper_zero_rate,
    both_represented_rate,
    both_sides_survived_rate,
    comparison_survival_regressions,
    count_by_paper,
    count_in_band,
    mean_kept,
    mean_minority_share,
    mean_second_paper_share,
    merge_round_robin,
    nearest_other,
    representation_rate,
    seeded_other,
    shut_out_rate,
    side_survival_rate,
    survival_rate,
    survival_regressions,
    worst_kept,
)
from evals.retrieval.metrics import Scored, first_satisfying_rank

_DEFAULT_SET = Path(__file__).parent / "golden_set.json"
_DEFAULT_COMPARISON_SET = Path(__file__).parent / "comparison_set.json"

# Pair CONSTRUCTION, not retrieval: no distance cutoff and no LIMIT, because
# the second mention is chosen from the whole project the way a user picking
# from the `@` menu would be — not from whatever survived a gate. Ordered
# `best ASC, paper_id ASC` so `nearest_other` walks a deterministic list.
_PAPER_BEST_SQL = text("""
    SELECT c.paper_id AS paper_id,
           p.title AS paper_title,
           MIN(c.embedding <=> CAST(:qvec AS vector)) AS best
    FROM paper_chunk_embeddings c
    JOIN papers p ON p.id = c.paper_id
    WHERE c.model = :model
      AND p.project_id = :project_id
    GROUP BY c.paper_id, p.title
    ORDER BY best ASC, c.paper_id ASC
""")

# Winnability check, the mention-path analogue of run_eval's
# `_positive_case_status`: does ANY chunk of the answering paper contain the
# expected substrings? A golden-set defect must be routed to `errors`, never
# scored as a retrieval miss.
_PAPER_TEXT_SQL = text("""
    SELECT c.text AS chunk_text
    FROM paper_chunk_embeddings c
    WHERE c.model = :model
      AND c.paper_id = :paper_id
""")

# Mirrors chat_service._dense_only_rows exactly — same `<=>` operator, same
# `WHERE c.model = :model` filter, same jsonb scope CTE, same ORDER/LIMIT —
# with `p.title` joined on so the golden set's title predicate can be applied.
# This is the `hybrid_retrieval=False` kill-switch path.
_SCOPED_DENSE_SQL = text("""
    WITH scope AS (
        SELECT value AS paper_id
        FROM jsonb_array_elements_text(CAST(:ids AS jsonb))
    )
    SELECT c.id AS chunk_id, c.paper_id AS paper_id, p.title AS paper_title,
           c.text AS chunk_text,
           (c.embedding <=> CAST(:qvec AS vector)) AS distance
    FROM paper_chunk_embeddings c
    JOIN scope s ON s.paper_id = c.paper_id
    JOIN papers p ON p.id = c.paper_id
    WHERE c.model = :model
      AND (c.embedding <=> CAST(:qvec AS vector)) < :threshold
    ORDER BY distance ASC
    LIMIT :max_chunks
""")

# Mirrors chat_service._hybrid_rows exactly: both arms in one round trip, the
# dense LIMIT raised to the caller's pool via `max(hybrid_dense_pool, pool)`
# while the sparse LIMIT stays exactly `hybrid_sparse_pool` (that arm's
# admission rule, not a candidate bound). Fusion, the budget and every cut
# stay in Python and are IMPORTED from production.
_SCOPED_HYBRID_SQL = text("""
    WITH scope AS (
        SELECT value AS paper_id
        FROM jsonb_array_elements_text(CAST(:ids AS jsonb))
    ),
    q AS (
        SELECT websearch_to_tsquery('english', :qtext) AS tsq
    ),
    dense AS (
        SELECT c.id, c.paper_id, c.text,
               (c.embedding <=> CAST(:qvec AS vector)) AS distance,
               ROW_NUMBER() OVER (
                   ORDER BY c.embedding <=> CAST(:qvec AS vector)
               ) AS d_rank
        FROM paper_chunk_embeddings c
        JOIN scope s ON s.paper_id = c.paper_id
        WHERE c.model = :model
          AND (c.embedding <=> CAST(:qvec AS vector)) < :threshold
        ORDER BY distance ASC
        LIMIT :dense_pool
    ),
    sparse AS (
        SELECT c.id, c.paper_id, c.text,
               ROW_NUMBER() OVER (
                   ORDER BY ts_rank_cd(c.tsv, q.tsq) DESC, c.id
               ) AS s_rank
        FROM paper_chunk_embeddings c
        JOIN scope s ON s.paper_id = c.paper_id
        CROSS JOIN q
        WHERE c.model = :model
          AND c.tsv @@ q.tsq
        ORDER BY ts_rank_cd(c.tsv, q.tsq) DESC, c.id
        LIMIT :sparse_pool
    )
    SELECT COALESCE(d.id, sp.id)             AS chunk_id,
           COALESCE(d.paper_id, sp.paper_id) AS paper_id,
           p.title                           AS paper_title,
           COALESCE(d.text, sp.text)         AS chunk_text,
           d.distance                        AS distance,
           d.d_rank                          AS d_rank,
           sp.s_rank                         AS s_rank
    FROM dense d
    FULL OUTER JOIN sparse sp ON sp.id = d.id
    JOIN papers p ON p.id = COALESCE(d.paper_id, sp.paper_id)
""")


@dataclass(frozen=True)
class Arm:
    """One configuration under measurement.

    `mode` is "production" (calls the shipped mention path directly),
    "flat" (one SQL cutoff over the merged scope) or "per_paper" (policy B:
    one query per mentioned paper, admitted on its own best chunk).
    `admission` is meaningful only for "per_paper".

    ONLY the "production" arm is authoritative for what ships. The other arms
    MIRROR the SQL rather than importing it, which is what let the per-paper
    guarantee's absence hide: on 2026-08-18 the shipped path grew a second
    guarantee query and every mirrored arm silently went on measuring the
    older shape. Mirrors are kept because policies A and B do not exist in
    production and cannot be imported — but a mirror is a hypothesis, and the
    production arm is the control that catches it drifting.
    """

    name: str
    mode: str
    threshold: float
    admission: float | None


def _arms() -> list[Arm]:
    """Built at call time, not import time, so an env override
    (`SIMILARITY_THRESHOLD=...`) reaches the arms the same way it reaches
    production."""
    return [
        # First, and the only arm that imports the shipped path end to end.
        Arm("production", "production", settings.similarity_threshold, None),
        Arm("mirror-0.75", "flat", settings.similarity_threshold, None),
        Arm("policy-A", "flat", settings.intra_paper_ceiling, None),
        Arm("policy-B", "per_paper", settings.intra_paper_ceiling, settings.similarity_threshold),
        Arm(
            "policy-B*",
            "per_paper",
            settings.intra_paper_ceiling,
            settings.intra_paper_ceiling,
        ),
    ]


def _vec(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


def _row_to_scored(row) -> Scored:
    return Scored(
        paper_id=row.paper_id,
        paper_title=row.paper_title,
        chunk_text=row.chunk_text,
        distance=None if row.distance is None else float(row.distance),
        chunk_id=row.chunk_id,
        d_rank=getattr(row, "d_rank", None),
        s_rank=getattr(row, "s_rank", None),
    )


async def _fetch_scope(
    db,
    *,
    qvec: str,
    qtext: str,
    paper_ids: list[str],
    threshold: float,
    pool: int,
) -> list[Scored]:
    """Production's candidate list for a scope, in production's own order,
    with NO cut applied yet.

    Reproduces `chat_service._retrieve_paper_chunks` up to (not including) the
    `single_paper` cut: the dense-only branch when `settings.hybrid_retrieval`
    is False, otherwise both arms fused by the IMPORTED `fuse_rrf` at
    production's weights and `rrf_k`, then truncated to `pool` in Python —
    "LIMIT bounds what crosses the wire; this bounds what reaches the model",
    exactly as production comments it.

    The caller applies the cut, because which cut is correct depends on the
    arm: the flat arms get none (production applies none to a multi-paper
    scope), and policy B gets the single-paper cut per paper.
    """
    if not paper_ids:
        return []
    ids = json.dumps(paper_ids)
    if not settings.hybrid_retrieval:
        rows = (
            await db.execute(
                _SCOPED_DENSE_SQL,
                {
                    "qvec": qvec,
                    "ids": ids,
                    "model": settings.embedding_model,
                    "threshold": threshold,
                    "max_chunks": pool,
                },
            )
        ).fetchall()
        return [_row_to_scored(r) for r in rows[:pool]]

    rows = (
        await db.execute(
            _SCOPED_HYBRID_SQL,
            {
                "qvec": qvec,
                "qtext": qtext,
                "ids": ids,
                "model": settings.embedding_model,
                "threshold": threshold,
                "dense_pool": max(settings.hybrid_dense_pool, pool),
                "sparse_pool": settings.hybrid_sparse_pool,
            },
        )
    ).fetchall()
    by_id = {r.chunk_id: r for r in rows}
    dense_ranked = [
        r.chunk_id
        for r in sorted((x for x in rows if x.d_rank is not None), key=lambda x: x.d_rank)
    ]
    sparse_ranked = [
        r.chunk_id
        for r in sorted((x for x in rows if x.s_rank is not None), key=lambda x: x.s_rank)
    ]
    fused = fuse_rrf(
        dense_ranked,
        sparse_ranked,
        w_dense=settings.hybrid_dense_weight,
        w_sparse=settings.hybrid_sparse_weight,
        k=settings.hybrid_rrf_k,
    )
    return [_row_to_scored(by_id[key]) for key, _ in fused[:pool]]


def _single_paper_cut(chunks: list[Scored]) -> list[Scored]:
    """Production's single-paper cut, applied to ONE paper's own ranking.

    Budget first, cut second — production's documented order. Under hybrid the
    cut is `keep_within_rank_window` against the fused order (a fused rank has
    no distance meaning); under the kill switch it is `keep_within_paper`
    against `intra_paper_delta`. Both are IMPORTED from production.
    """
    bounded = chunks[: settings.max_context_chunks]
    if settings.hybrid_retrieval:
        return bounded[
            : keep_within_rank_window([0.0] * len(bounded), window=settings.intra_paper_rank_window)
        ]
    return bounded[
        : keep_within_paper([c.distance for c in bounded], delta=settings.intra_paper_delta)
    ]


def _best_distance(chunks: list[Scored]) -> float | None:
    """The nearest DENSE distance in a list. None when the list is empty or
    holds only sparse-only admissions — which cannot clear a distance gate,
    so policy B must not admit the paper on them."""
    distances = [c.distance for c in chunks if c.distance is not None]
    return min(distances) if distances else None


async def _run_arm(
    db,
    arm: Arm,
    *,
    qvec: str,
    qtext: str,
    scope_ids: list[str],
    embedding: list[float] | None = None,
    titles: dict[str, str] | None = None,
) -> list[Scored]:
    """The final chunk list one arm delivers to the model for one case.

    The flat arms are production's own mention path with `widened=False`:
    ONE query over the merged scope at the arm's cutoff, a candidate pool of
    `max_context_chunks + floor * len(scope)` (production's own pool — the
    floor is a no-op without it), then `apply_per_paper_floor`.

    Policy B queries each paper alone, admits it on its own nearest chunk,
    cuts it against its own ranking, merges by position, and floors the merge.
    """
    floor = settings.mention_per_paper_floor
    budget = settings.max_context_chunks

    if arm.mode == "production":
        # No mirror at all: this is the code the product runs, including the
        # per-paper guarantee query and the floor. Everything else in this
        # module is a hypothesis measured beside it.
        from app.services.chat_service import ChatService, PaperInfo

        scope = [PaperInfo(paper_id=pid, title=(titles or {}).get(pid, "")) for pid in scope_ids]
        chunks, _widened = await ChatService()._retrieve_mentioned_chunks(
            scope,
            scope,
            embedding or [],
            qtext,
            False,
        )
        return [
            Scored(
                paper_id=c.paper_id,
                paper_title=c.title,
                chunk_text=c.text,
                # The shipped path returns what the MODEL sees, which carries
                # no distance -- a fused or guaranteed row has none to report.
                distance=None,
                chunk_id=f"{c.paper_id}:{c.chunk_index}",
                d_rank=None,
                s_rank=None,
            )
            for c in chunks
        ]

    if arm.mode == "flat":
        candidates = await _fetch_scope(
            db,
            qvec=qvec,
            qtext=qtext,
            paper_ids=scope_ids,
            threshold=arm.threshold,
            pool=budget + floor * len(scope_ids),
        )
        return apply_per_paper_floor(
            candidates,
            paper_of=lambda c: c.paper_id,
            scope=scope_ids,
            floor=floor,
            budget=budget,
        )

    per_paper: dict[str, list[Scored]] = {}
    for paper_id in scope_ids:
        chunks = await _fetch_scope(
            db,
            qvec=qvec,
            qtext=qtext,
            paper_ids=[paper_id],
            threshold=arm.threshold,
            pool=budget,
        )
        per_paper[paper_id] = chunks
    bests = {pid: _best_distance(chunks) for pid, chunks in per_paper.items()}
    admitted = admitted_papers(bests, arm.admission if arm.admission is not None else arm.threshold)
    merged = merge_round_robin([_single_paper_cut(per_paper[pid]) for pid in admitted])
    return apply_per_paper_floor(
        merged,
        paper_of=lambda c: c.paper_id,
        scope=scope_ids,
        floor=floor,
        budget=budget,
    )


async def _paper_bests(db, qvec: str, project_id: str) -> list[PaperBest]:
    rows = (
        await db.execute(
            _PAPER_BEST_SQL,
            {"qvec": qvec, "model": settings.embedding_model, "project_id": project_id},
        )
    ).fetchall()
    return [
        PaperBest(
            paper_id=r.paper_id,
            title=r.paper_title,
            best=None if r.best is None else float(r.best),
        )
        for r in rows
    ]


async def _case_status(db, case: Case, papers: list[PaperBest]) -> tuple[str | None, str | None]:
    """(answer_paper_id, error). Mirrors run_eval's golden-set gates: the named
    paper must exist, must be unambiguous (production's own scope is a set of
    ids, so a needle matching two papers describes a scope the case never
    meant), and the paper must actually hold a satisfying chunk."""
    if case.is_negative:
        return None, None
    needle = (case.paper_title_contains or "").lower()
    matches = [p for p in papers if needle in p.title.lower()]
    if not matches:
        return None, f"no paper matching {case.paper_title_contains!r}"
    if len(matches) > 1:
        return None, (
            f"paper_title_contains {case.paper_title_contains!r} matches {len(matches)} "
            "distinct papers — not distinctive enough to name one mention"
        )
    paper = matches[0]
    texts = (
        await db.execute(
            _PAPER_TEXT_SQL, {"model": settings.embedding_model, "paper_id": paper.paper_id}
        )
    ).fetchall()
    if not any(chunk_satisfies(case, paper.title, r.chunk_text) for r in texts):
        return None, (
            f"paper matching {case.paper_title_contains!r} found, but no chunk of it "
            f"contains {list(case.expect_substrings)!r}"
        )
    return paper.paper_id, None


async def _resolve_side(db, case: ComparisonCase, side: str, papers: list[PaperBest]):
    """(paper_id, error) for one side of a comparison case.

    Same three gates `_case_status` applies to a golden-set positive — the
    named paper must exist, must be unambiguous, and must actually hold a chunk
    containing the expected text — applied to the projected side, so the two
    files cannot disagree about what "winnable" means. A defect in the
    comparison set must reach `errors`, never be scored as a retrieval miss.
    """
    projected = case.side(side)
    needle = (projected.paper_title_contains or "").lower()
    matches = [p for p in papers if needle in p.title.lower()]
    if not matches:
        return None, f"side {side}: no paper matching {projected.paper_title_contains!r}"
    if len(matches) > 1:
        return None, (
            f"side {side}: paper_title_contains {projected.paper_title_contains!r} matches "
            f"{len(matches)} distinct papers — not distinctive enough to name one mention"
        )
    paper = matches[0]
    texts = (
        await db.execute(
            _PAPER_TEXT_SQL, {"model": settings.embedding_model, "paper_id": paper.paper_id}
        )
    ).fetchall()
    if not any(chunk_satisfies(projected, paper.title, r.chunk_text) for r in texts):
        return None, (
            f"side {side}: paper matching {projected.paper_title_contains!r} found, but no "
            f"chunk of it contains {list(projected.expect_substrings)!r}"
        )
    return paper.paper_id, None


async def _comparison_status(
    db, case: ComparisonCase, papers: list[PaperBest]
) -> tuple[dict[str, str] | None, list[str]]:
    """({side: paper_id}, errors) for a comparison case.

    Both sides must resolve AND resolve to different papers: two needles
    landing on one paper would make the case a single-paper question wearing a
    comparison's clothes, and every representation metric would report a
    perfect score while measuring nothing new. The loader already rejects
    needles that nest, but two genuinely different needles can still hit one
    title in a corpus the loader has never seen.
    """
    resolved: dict[str, str] = {}
    errors: list[str] = []
    for side in case.sides:
        paper_id, error = await _resolve_side(db, case, side, papers)
        if error is not None:
            errors.append(f"{case.id}: {error}")
        else:
            resolved[side] = paper_id
    if errors:
        return None, errors
    if len(set(resolved.values())) < len(resolved):
        return None, [f"{case.id}: both title needles resolve to the SAME paper in this corpus"]
    return resolved, []


def _scope_for(
    case: Case, papers: list[PaperBest], answer_paper_id: str | None, pairing: str
) -> list[str] | None:
    """The two mentioned paper ids for a case under one pairing scheme.

    Positives: the answering paper plus a second one — the nearest OTHER paper
    (the hard, realistic "compare these two"), or a seeded random one (the
    contrast). Negatives have no answering paper, so the first mention is the
    paper holding the globally nearest chunk, exactly as `run_eval`'s
    `_scope_to_nearest_paper` does for single-paper scope.
    """
    if answer_paper_id is None:
        first = nearest_other(papers, exclude="")
        if first is None:
            return None
        anchor = first.paper_id
    else:
        anchor = answer_paper_id
    second = (
        nearest_other(papers, exclude=anchor)
        if pairing == "nearest"
        else seeded_other(papers, exclude=anchor, seed=case.id)
    )
    if second is None:
        return None
    return [anchor, second.paper_id]


def _outcome(
    case: Case,
    arm: Arm,
    pairing: str,
    scope_ids: list[str],
    answer_paper_id: str | None,
    kept: list,
) -> MentionOutcome:
    return MentionOutcome(
        case_id=case.id,
        kind=case.kind,
        config=arm.name,
        pairing=pairing,
        scope=tuple(scope_ids),
        answer_paper_id=answer_paper_id,
        kept_total=len(kept),
        kept_by_paper=count_by_paper(kept, lambda c: c.paper_id),
        answer_survived=(
            None if case.is_negative else first_satisfying_rank(case, kept) is not None
        ),
    )


def _comparison_outcome(
    case: ComparisonCase,
    arm: Arm,
    side_paper: dict[str, str],
    scope_ids: list[str],
    kept: list,
) -> ComparisonOutcome:
    ranks = {side: first_satisfying_rank(case.side(side), kept) for side in case.sides}
    return ComparisonOutcome(
        case_id=case.id,
        config=arm.name,
        scope=tuple(scope_ids),
        kept_total=len(kept),
        kept_by_paper=count_by_paper(kept, lambda c: c.paper_id),
        side_paper=dict(side_paper),
        side_survived={side: rank is not None for side, rank in ranks.items()},
        side_rank=ranks,
    )


def _fmt(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _report_slice(title: str, outcomes: list[MentionOutcome], arms: list[Arm]) -> None:
    print(f"\n{title}")
    print(
        f"{'arm':<14}{'repr':>8}{'both':>8}{'ans-zero':>10}{'survival':>10}"
        f"{'mean kept':>11}{'other share':>13}"
    )
    baseline = [o for o in outcomes if o.config == arms[0].name]
    for arm in arms:
        rows = [o for o in outcomes if o.config == arm.name]
        if not rows:
            continue
        print(
            f"{arm.name:<14}{_fmt(representation_rate(rows)):>8}"
            f"{_fmt(both_represented_rate(rows)):>8}"
            f"{_fmt(answer_paper_zero_rate(rows)):>10}"
            f"{_fmt(survival_rate(rows)):>10}"
            f"{_fmt(mean_kept(rows), 1):>11}"
            f"{_fmt(mean_second_paper_share(rows)):>13}"
        )
    for arm in arms[1:]:
        rows = [o for o in outcomes if o.config == arm.name]
        lost = survival_regressions(baseline, rows)
        if lost:
            print(f"  REGRESSION vs {arms[0].name} under {arm.name}: {', '.join(sorted(lost))}")


def _report_comparisons(outcomes: list[ComparisonOutcome], arms: list[Arm]) -> None:
    """The REAL-comparison arm.

    `both` is the headline: the fraction of questions where both halves of the
    answer reached the model. `side surv` splits that per side so a
    half-answered comparison is visible as one lost side rather than one lost
    case. `shut out` counts cases where a paper the USER named contributed
    nothing at all — the instruction being visibly ignored, which is a distinct
    failure from the answer being missed. `min share` is the balance of the
    delivered budget between the two named papers (0.5 = even).
    """
    n = len({o.case_id for o in outcomes})
    print(f"\nREAL comparison questions, both papers named by the case   (n={n})")
    if not outcomes:
        print("  (no comparison cases ran — see ERRORS)")
        return
    print(
        f"{'arm':<14}{'repr':>8}{'both':>8}{'side surv':>11}{'shut out':>10}"
        f"{'mean kept':>11}{'min share':>11}"
    )
    baseline = [o for o in outcomes if o.config == arms[0].name]
    for arm in arms:
        rows = [o for o in outcomes if o.config == arm.name]
        if not rows:
            continue
        print(
            f"{arm.name:<14}{_fmt(representation_rate(rows)):>8}"
            f"{_fmt(both_sides_survived_rate(rows)):>8}"
            f"{_fmt(side_survival_rate(rows)):>11}"
            f"{_fmt(shut_out_rate(rows)):>10}"
            f"{_fmt(mean_kept(rows), 1):>11}"
            f"{_fmt(mean_minority_share(rows)):>11}"
        )
    for arm in arms[1:]:
        rows = [o for o in outcomes if o.config == arm.name]
        lost = comparison_survival_regressions(baseline, rows)
        if lost:
            print(f"  REGRESSION vs {arms[0].name} under {arm.name}: {', '.join(sorted(lost))}")


def _report_comparison_per_case(outcomes: list[ComparisonOutcome], arms: list[Arm]) -> None:
    if not outcomes:
        return
    case_ids = sorted({o.case_id for o in outcomes})
    width = max(34, max(len(cid) for cid in case_ids) + 1)
    print("\nper-case (real comparisons) — 'keptA/keptB  rankA,rankB' (. = never reached)")
    header = f"{'case':<{width}}"
    for arm in arms:
        header += f"{arm.name:>22}"
    print(header)
    for case_id in case_ids:
        line = f"{case_id:<{width}}"
        for arm in arms:
            match = next(
                (o for o in outcomes if o.case_id == case_id and o.config == arm.name), None
            )
            if match is None:
                line += f"{'-':>22}"
                continue
            kept = "/".join(str(match.kept_by_paper.get(pid, 0)) for pid in match.scope)
            ranks = ",".join(
                "." if match.side_rank[side] is None else str(match.side_rank[side])
                for side in sorted(match.side_rank)
            )
            line += f"{f'{kept}  {ranks}':>22}"
        print(line)


def _report_negatives(outcomes: list[MentionOutcome], arms: list[Arm]) -> None:
    print("\noff_topic negatives, scoped to TWO papers (containment — lower is better)")
    print(f"{'arm':<14}{'mean kept':>11}{'worst kept':>12}{'papers repr':>13}")
    for arm in arms:
        rows = [o for o in outcomes if o.config == arm.name]
        if not rows:
            continue
        print(
            f"{arm.name:<14}{_fmt(mean_kept(rows), 1):>11}"
            f"{'-' if worst_kept(rows) is None else worst_kept(rows):>12}"
            f"{_fmt(representation_rate(rows)):>13}"
        )


def _worst(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _report_band(pairs: list[dict], lo: float, hi: float) -> None:
    """The diagnostic that bounds the whole question.

    A mentioned paper only behaves differently under the status quo and under
    policy A if its OWN nearest chunk lands in [similarity_threshold,
    intra_paper_ceiling). Outside that band the two gates admit exactly the
    same rows, so this count is the ceiling on how often the policy choice can
    matter — and the `worst` column says how much headroom the answering paper
    has before it can ever fall into the band.
    """
    print(f"\ngate band [{lo}, {hi}) — where the status quo and policy A can differ at all")
    print(f"{'role':<28}{'n':>5}{'in band':>9}{'worst nearest-chunk':>22}")
    roles = [
        ("answer paper", [p for p in pairs if p["role"] == "answer"]),
        ("second mention (nearest)", [p for p in pairs if p["role"] == "second-nearest"]),
        ("second mention (seeded)", [p for p in pairs if p["role"] == "second-seeded"]),
        ("off_topic anchor + second", [p for p in pairs if p["role"].startswith("negative")]),
        # The row the 2026-08-18 measurement could not produce: a paper named
        # in a REAL comparison, measured against the BLENDED question that
        # names both. If a genuine two-paper query pushes either paper's
        # nearest chunk into the band, the gate choice stops being academic.
        ("comparison paper (real)", [p for p in pairs if p["role"].startswith("comparison")]),
    ]
    for label, rows in roles:
        values = [r["best"] for r in rows]
        print(
            f"{label:<28}{len(rows):>5}{count_in_band(values, lo, hi):>9}"
            f"{_fmt(_worst(values), 4):>22}"
        )


def _report_per_case(outcomes: list[MentionOutcome], arms: list[Arm], pairing: str) -> None:
    rows = [o for o in outcomes if o.pairing == pairing]
    if not rows:
        return
    case_ids = sorted({o.case_id for o in rows})
    width = max(28, max(len(cid) for cid in case_ids) + 1)
    print(f"\nper-case ({pairing} pairing) — 'kept(answer paper/total)  survived'")
    header = f"{'case':<{width}}"
    for arm in arms:
        header += f"{arm.name:>18}"
    print(header)
    for case_id in case_ids:
        line = f"{case_id:<{width}}"
        for arm in arms:
            match = next((o for o in rows if o.case_id == case_id and o.config == arm.name), None)
            if match is None:
                line += f"{'-':>18}"
                continue
            flag = "-" if match.answer_survived is None else ("y" if match.answer_survived else "N")
            line += f"{f'{match.answer_paper_chunks}/{match.kept_total} {flag}':>18}"
        print(line)


async def main() -> None:  # noqa: PLR0912 — a report script, not a library API
    parser = argparse.ArgumentParser(description="Measure multi-mention retrieval policy.")
    parser.add_argument(
        "--project-id",
        required=True,
        help="scope the corpus to one project's papers, matching production's own scoping",
    )
    parser.add_argument("--set", type=Path, default=_DEFAULT_SET)
    parser.add_argument(
        "--comparison-set",
        type=Path,
        default=_DEFAULT_COMPARISON_SET,
        help="real two-paper comparison questions (the non-synthetic arm)",
    )
    parser.add_argument(
        "--skip-comparisons",
        action="store_true",
        help="run only the synthetic pairings, e.g. to reproduce a pre-2026-08-18 number",
    )
    parser.add_argument("--json", type=Path, default=None, help="also dump per-case results here")
    parser.add_argument(
        "--per-case",
        action="store_true",
        help="print the per-case table for each pairing, not just the summary",
    )
    args = parser.parse_args()

    cases = load_golden_set(args.set)
    comparisons = [] if args.skip_comparisons else load_comparison_set(args.comparison_set)
    svc = EmbeddingService()
    arms = _arms()
    pairings = ("nearest", "seeded")

    outcomes: list[MentionOutcome] = []
    comparison_outcomes: list[ComparisonOutcome] = []
    # One row per (case, mentioned paper): the paper's own nearest chunk, which
    # is what every gate under measurement actually tests. Feeds _report_band.
    pairs: list[dict] = []
    errors: list[str] = []
    corpus_note = ""

    async with SessionLocal() as db:
        for case in cases:
            embedding = await svc.embed(case.question, task_type="RETRIEVAL_QUERY")
            qvec = _vec(embedding)
            papers = await _paper_bests(db, qvec, args.project_id)
            if not corpus_note:
                corpus_note = f"{len(papers)} papers with embeddings"
            answer_paper_id, error = await _case_status(db, case, papers)
            if error is not None:
                errors.append(f"{case.id}: {error}")
                continue
            best_by_id = {p.paper_id: p.best for p in papers}
            for pairing in pairings:
                scope_ids = _scope_for(case, papers, answer_paper_id, pairing)
                if scope_ids is None:
                    errors.append(f"{case.id}: no second paper available for pairing {pairing!r}")
                    continue
                for position, paper_id in enumerate(scope_ids):
                    # The first mention (the answering paper, or an off_topic
                    # case's nearest paper) is the same under both pairings;
                    # record it once so the band table's `n` is 30, not 60.
                    if position == 0 and pairing != pairings[0]:
                        continue
                    if case.is_negative:
                        role = f"negative-{position}"
                    elif position == 0:
                        role = "answer"
                    else:
                        role = f"second-{pairing}"
                    pairs.append(
                        {
                            "case_id": case.id,
                            "pairing": pairing,
                            "role": role,
                            "paper_id": paper_id,
                            "best": best_by_id.get(paper_id),
                        }
                    )
                titles_by_id = {p.paper_id: p.title for p in papers}
                for arm in arms:
                    kept = await _run_arm(
                        db,
                        arm,
                        qvec=qvec,
                        qtext=case.question,
                        scope_ids=scope_ids,
                        embedding=embedding,
                        titles=titles_by_id,
                    )
                    outcomes.append(_outcome(case, arm, pairing, scope_ids, answer_paper_id, kept))

        for comparison in comparisons:
            # The blended question is embedded ONCE and drives both the scope
            # resolution and every arm, exactly as a production turn would:
            # the whole point of this arm is the query vector that sits between
            # two papers rather than on one.
            embedding = await svc.embed(comparison.question, task_type="RETRIEVAL_QUERY")
            qvec = _vec(embedding)
            papers = await _paper_bests(db, qvec, args.project_id)
            side_paper, case_errors = await _comparison_status(db, comparison, papers)
            if side_paper is None:
                errors.extend(case_errors)
                continue
            best_by_id = {p.paper_id: p.best for p in papers}
            scope_ids = [side_paper[side] for side in comparison.sides]
            for side in comparison.sides:
                pairs.append(
                    {
                        "case_id": comparison.id,
                        "pairing": "real",
                        "role": f"comparison-{side}",
                        "paper_id": side_paper[side],
                        "best": best_by_id.get(side_paper[side]),
                    }
                )
            titles_by_id = {p.paper_id: p.title for p in papers}
            for arm in arms:
                kept = await _run_arm(
                    db,
                    arm,
                    qvec=qvec,
                    qtext=comparison.question,
                    scope_ids=scope_ids,
                    embedding=embedding,
                    titles=titles_by_id,
                )
                comparison_outcomes.append(
                    _comparison_outcome(comparison, arm, side_paper, scope_ids, kept)
                )

    print(
        f"\ncorpus: {corpus_note}   model: {settings.embedding_model}   project: {args.project_id}"
    )
    print(
        f"gates: similarity_threshold={settings.similarity_threshold} "
        f"intra_paper_ceiling={settings.intra_paper_ceiling} "
        f"floor={settings.mention_per_paper_floor} budget={settings.max_context_chunks} "
        f"hybrid={settings.hybrid_retrieval}"
    )
    print("(two mentions per case; policy B's admission and merge are SIMULATED in the harness)")
    print(
        f"({len(cases)} golden-set cases x 2 SYNTHETIC pairings, "
        f"{len(comparisons)} REAL comparison questions)"
    )

    if errors:
        print("\nERRORS (golden-set problems, not retrieval failures):")
        for err in errors:
            print(f"  ! {err}")

    for pairing in pairings:
        positives = [o for o in outcomes if o.pairing == pairing and o.answer_paper_id is not None]
        label = "nearest OTHER paper (hard case)" if pairing == "nearest" else "seeded random paper"
        _report_slice(
            f"positives, second mention = {label}   (n={len({o.case_id for o in positives})})",
            positives,
            arms,
        )
        if args.per_case:
            _report_per_case(positives, arms, pairing)

    if not args.skip_comparisons:
        _report_comparisons(comparison_outcomes, arms)
        if args.per_case:
            _report_comparison_per_case(comparison_outcomes, arms)

    negatives = [o for o in outcomes if o.answer_paper_id is None and o.pairing == "nearest"]
    _report_negatives(negatives, arms)
    _report_band(pairs, settings.similarity_threshold, settings.intra_paper_ceiling)

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "project_id": args.project_id,
                    "model": settings.embedding_model,
                    "settings": {
                        "similarity_threshold": settings.similarity_threshold,
                        "intra_paper_ceiling": settings.intra_paper_ceiling,
                        "intra_paper_rank_window": settings.intra_paper_rank_window,
                        "intra_paper_delta": settings.intra_paper_delta,
                        "mention_per_paper_floor": settings.mention_per_paper_floor,
                        "max_context_chunks": settings.max_context_chunks,
                        "hybrid_retrieval": settings.hybrid_retrieval,
                    },
                    "errors": errors,
                    "pairs": pairs,
                    "outcomes": [
                        {
                            "case_id": o.case_id,
                            "kind": o.kind,
                            "config": o.config,
                            "pairing": o.pairing,
                            "scope": list(o.scope),
                            "answer_paper_id": o.answer_paper_id,
                            "kept_total": o.kept_total,
                            "kept_by_paper": o.kept_by_paper,
                            "answer_survived": o.answer_survived,
                        }
                        for o in outcomes
                    ],
                    "comparison_outcomes": [
                        {
                            "case_id": o.case_id,
                            "config": o.config,
                            "scope": list(o.scope),
                            "kept_total": o.kept_total,
                            "kept_by_paper": o.kept_by_paper,
                            "side_paper": o.side_paper,
                            "side_survived": o.side_survived,
                            "side_rank": o.side_rank,
                        }
                        for o in comparison_outcomes
                    ],
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
