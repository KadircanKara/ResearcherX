"""Pure scoring for the retrieval eval harness.

Everything here is a function of already-fetched chunks, so the metrics are
unit-testable without Postgres or an embedding provider. The runner does one
query per case and feeds the full chunk list to these functions — the sweep
therefore costs no extra queries.
"""

from __future__ import annotations

from dataclasses import dataclass

from evals.retrieval.golden_set import Case, chunk_satisfies

# Swept range. Below 0.30 nothing survives; above 0.90 nothing is excluded.
THRESHOLDS: tuple[float, ...] = tuple(round(0.30 + 0.05 * i, 2) for i in range(13))


@dataclass(frozen=True)
class Scored:
    paper_id: str
    paper_title: str
    chunk_text: str
    # `float | None`: a sparse-only admission (hybrid arm) has no cosine
    # distance. Every distance-space function below (best_satisfying_distance,
    # noise_floor, sweep, separating_threshold, and simulate_retrieval's
    # DEFAULT presorted=False path) still types this as float and will raise
    # TypeError on None -- by design, NOT an oversight. Those functions must
    # keep consuming the DENSE arm exclusively (plain `_chunks_for`, never
    # `_hybrid_chunks_for`). The hybrid arm may only be fed to `recall_at_k`
    # / `mean_reciprocal_rank` with `presorted=True` (see simulate_retrieval's
    # docstring), `rescued_count` / `rescue_eligible_count`, and the targeted
    # survival cut -- none of which touch `.distance` on a hybrid list. See
    # run_eval.py's --hybrid branch for the enforcement.
    distance: float | None
    # Hybrid-only. `distance`/`d_rank` are None for a chunk the dense arm
    # never returned -- a sparse-only admission, which is the entire point of
    # the hybrid arm.
    chunk_id: str = ""
    d_rank: int | None = None
    s_rank: int | None = None


@dataclass(frozen=True)
class SweepRow:
    threshold: float
    content_recall: float
    off_topic_false_accept: float


def simulate_retrieval(chunks: list[Scored], k: int, *, presorted: bool = False) -> list[Scored]:
    """Reproduce the SET of chunks chat_service._retrieve_paper_chunks fetches:
    a GLOBAL top-k across the whole project, nearest first.

    Production applies no per-paper ceiling. Cosine similarity spreads the
    result across papers by itself — measured on a 100-paper library, a global
    top-40 spanned 14-25 distinct papers. Simulating top-k PER PAPER, as this
    did until 2026-08-10, gave every paper its own budget regardless of library
    size, which is why recall@k here was structurally close to invariant under
    corpus growth.

    `presorted=True` skips the distance sort and takes `chunks[:k]` as-is.
    This is the hybrid arm's path: `chunks` there is already in FUSED rank
    order (from `run_eval._hybrid_chunks_for`/`fuse_rrf`) and may contain
    `distance=None` entries (sparse-only admissions) that `sorted(..., key=
    lambda c: c.distance)` would raise `TypeError` on — and even without the
    None values, re-sorting by the real, partially-missing cosine distance
    would destroy the fused order that is the entire point of measuring the
    hybrid arm. The dense path's behavior (the default, `presorted=False`) is
    unchanged.
    """
    if presorted:
        return chunks[:k]
    return sorted(chunks, key=lambda c: c.distance)[:k]


def first_satisfying_rank(case: Case, retrieved: list[Scored]) -> int | None:
    """1-based rank of the first correct chunk, or None if there isn't one."""
    for rank, chunk in enumerate(retrieved, start=1):
        if chunk_satisfies(case, chunk.paper_title, chunk.chunk_text):
            return rank
    return None


def recall_at_k(
    case_chunks: list[tuple[Case, list[Scored]]], k: int, *, presorted: bool = False
) -> float:
    """Fraction of cases with a satisfying chunk within the global top-k.

    This is the retrieval CEILING, not what production returns: no distance
    cutoff is applied here, only a global top-k. Production also filters by
    `similarity_threshold` before top-k (see `sweep`), so production recall is
    always <= this number. Do not report this figure as "what production
    achieves" — pair it with `sweep`'s content_recall column for that.

    `presorted` is forwarded to `simulate_retrieval` — see its docstring.
    Pass `presorted=True` for the hybrid arm's already-fused chunk lists.
    """
    if not case_chunks:
        raise ValueError("no cases to score")
    hits = sum(
        1
        for case, chunks in case_chunks
        if first_satisfying_rank(case, simulate_retrieval(chunks, k, presorted=presorted))
        is not None
    )
    return hits / len(case_chunks)


def mean_reciprocal_rank(
    case_chunks: list[tuple[Case, list[Scored]]], k: int, *, presorted: bool = False
) -> float:
    """Mean of 1/rank over each case's first satisfying chunk, ranked by
    `simulate_retrieval`'s distance order (nearest first, across the whole
    project).

    Since 2026-08-10 this also matches production's own order: chat_service.py
    fetches with `ORDER BY distance ASC LIMIT :max_chunks` and never re-sorts,
    so a chunk's rank here is the same position it lands in the LLM's prompt.
    Like `recall_at_k`, no distance cutoff is applied, so this is still a
    ceiling, not what `similarity_threshold` filtering leaves production with.

    `presorted` is forwarded to `simulate_retrieval` — see its docstring.
    Pass `presorted=True` for the hybrid arm's already-fused chunk lists.
    """
    if not case_chunks:
        raise ValueError("no cases to score")
    total = 0.0
    for case, chunks in case_chunks:
        rank = first_satisfying_rank(case, simulate_retrieval(chunks, k, presorted=presorted))
        if rank is not None:
            total += 1.0 / rank
    return total / len(case_chunks)


def rescued_count(case_chunks: list[tuple[Case, list[Scored]]], k: int) -> int:
    """Positives whose satisfying chunk reached the model ONLY via the sparse arm.

    This is the number that justifies hybrid retrieval. `recall@k` can improve
    from reordering alone; `rescued` counts the cases where dense retrieval
    did not return the answering chunk at all and the lexical arm did. If it
    is zero, `ts_rank_cd` is not adding a signal and the change should not
    ship (the next lever would be a real BM25 index, which is a base-image
    change -- see the design doc).

    `chunks` must already be in FUSED order; only the first `k` count, because
    only those reach the model. Deliberately does not touch `.distance` --
    the whole reason this function exists separately from the distance-space
    metrics above is that a sparse-only admission has none.
    """
    rescued = 0
    for case, chunks in case_chunks:
        for chunk in chunks[:k]:
            if not chunk_satisfies(case, chunk.paper_title, chunk.chunk_text):
                continue
            if chunk.d_rank is None and chunk.s_rank is not None:
                rescued += 1
            break
    return rescued


def rescue_eligible_count(dense_case_chunks: list[tuple[Case, list[Scored]]], k: int) -> int:
    """Positives whose answering chunk is ABSENT from the dense arm's own
    admitted set within `k` — the only cases a sparse-only admission could
    possibly rescue.

    `rescued_count` alone is a misleading headline on its own: with dense
    recall well under 1.0, most positives are already dense hits and are
    mechanically ineligible to register a rescue (their first satisfying
    chunk came from the dense arm, so `rescued_count`'s `d_rank is None`
    check can never fire for them). Reporting `rescued_count` without this
    denominator reads as "hybrid barely helps" when the true statement may be
    "hybrid rescued every case dense actually missed" — very different
    verdicts for the same numerator.

    Takes DENSE `Scored` lists (plain distances, from `_chunks_for`) and
    reuses `simulate_retrieval`'s ordinary distance-sorted top-k — the same
    admitted-set definition `recall_at_k` already uses for the dense arm — so
    this denominator is directly comparable to the dense recall figure
    reported alongside it.
    """
    eligible = 0
    for case, chunks in dense_case_chunks:
        if first_satisfying_rank(case, simulate_retrieval(chunks, k)) is None:
            eligible += 1
    return eligible


def best_satisfying_distance(case: Case, chunks: list[Scored]) -> float | None:
    """Closest distance among correct chunks — the raw material for tuning."""
    hits = [c.distance for c in chunks if chunk_satisfies(case, c.paper_title, c.chunk_text)]
    return min(hits) if hits else None


def noise_floor(negative_chunks: list[list[Scored]]) -> float | None:
    """Closest distance any off-topic question achieved against any chunk.

    A threshold is only meaningful if it sits below this and above the content
    cases' best distances.
    """
    bests = [min(c.distance for c in chunks) for chunks in negative_chunks if chunks]
    return min(bests) if bests else None


def sweep(
    positives: list[tuple[Case, list[Scored]]],
    negatives: list[list[Scored]],
    k: int,
    thresholds: tuple[float, ...] = THRESHOLDS,
) -> list[SweepRow]:
    """Content recall vs off-topic false-accept at each candidate cutoff.

    Raises if no negative case actually contributed a chunk: with none, every
    threshold reports zero false-accept and `separating_threshold` would
    return a number that was never measured. `if not negatives` alone would
    miss the all-empty-sublists shape (off_topic cases that each returned
    zero chunks) — `noise_floor` already treats that shape as "nothing
    measured" (it returns None), so this check is made to agree with it via
    `any(negatives)` rather than a plain truthiness check on the outer list.
    """
    if not any(negatives):
        raise ValueError(
            "sweep needs off_topic cases: with no negatives every threshold reports "
            "zero false-accept and separating_threshold returns a number that was "
            "never measured"
        )
    rows: list[SweepRow] = []
    for threshold in thresholds:
        kept_pos = [
            (case, [c for c in chunks if c.distance < threshold]) for case, chunks in positives
        ]
        recall = recall_at_k(kept_pos, k)
        accepted = sum(1 for chunks in negatives if any(c.distance < threshold for c in chunks))
        false_accept = accepted / len(negatives)
        rows.append(
            SweepRow(
                threshold=threshold, content_recall=recall, off_topic_false_accept=false_accept
            )
        )
    return rows


def separating_threshold(rows: list[SweepRow]) -> float | None:
    """Lowest cutoff with full content recall and zero off-topic acceptance.

    None means no cutoff separates the two populations — which is a finding,
    not a failure: it says an absolute cosine cutoff is the wrong instrument
    for this model, and the next lever is reranking or hybrid retrieval.

    Takes the min over all qualifying rows rather than the first one in
    `rows` — correct regardless of `rows`' order, so a caller passing
    thresholds in descending order still gets the lowest qualifying cutoff
    instead of the most permissive one.
    """
    return min(
        (
            row.threshold
            for row in rows
            if row.content_recall == 1.0 and row.off_topic_false_accept == 0.0
        ),
        default=None,
    )


# --- Closed-form separation -------------------------------------------------
#
# `sweep`/`separating_threshold` above SAMPLE a fixed threshold grid. A grid
# can straddle a real separating interval entirely: on this harness's own
# first live measurement, the true interval was 0.0207 wide against a 0.05
# grid step, and the grid found nothing where an exact interval existed. The
# functions below compute the exact bounds instead — what the grid was
# approximating all along — and are the authoritative answer; the grid stays
# useful only as a coarse visual cross-check.


def topk_satisfying_distance(case: Case, chunks: list[Scored], k: int) -> float | None:
    """Distance of the chunk `first_satisfying_rank` finds within this case's
    own global top-k, or None if no satisfying chunk survives that cut —
    even when `best_satisfying_distance` finds one elsewhere in the corpus.

    This is the value a separating threshold actually needs, and it is NOT
    the same number `best_satisfying_distance` reports: `best_satisfying_distance`
    is the globally-nearest satisfying chunk in the whole corpus, ignoring
    whether nearer chunks from ANY paper would crowd it out of the top-k
    that `simulate_retrieval` (and hence production) actually returns.

    Why the max of THIS value over positives is the correct separating lower
    bound, and the max of `best_satisfying_distance` is not: distance-
    threshold filtering (`distance < T`) and top-k selection are both "keep
    the nearest prefix" operations on the same distance-sorted list, so they
    commute. Concretely — a chunk already in the unfiltered top-k survives
    filtering at threshold T iff its own distance < T, because every nearer
    competitor also has distance < T and so also survives (nothing is
    promoted past it). And no chunk OUTSIDE the unfiltered top-k can ever
    enter a thresholded top-k, because thresholding only removes candidates
    — it can never let a chunk leapfrog a nearer competitor that already
    excluded it in the unfiltered ranking. So a case whose satisfying chunk
    misses the unfiltered top-k can never achieve recall at ANY threshold:
    this function returns None for exactly that case, and callers
    (`diagnose_separation`) must treat that as "no threshold exists for this
    case," never silently fall back to a different, more optimistic number.
    """
    retrieved = simulate_retrieval(chunks, k)
    rank = first_satisfying_rank(case, retrieved)
    if rank is None:
        return None
    return retrieved[rank - 1].distance


@dataclass(frozen=True)
class SeparatingInterval:
    """(lo, hi]: content_recall(T) == 1.0 for every T > lo, and
    off_topic_false_accept(T) == 0.0 for every T <= hi. Any T with
    lo < T <= hi separates content from noise for this measurement."""

    lo: float
    hi: float


@dataclass(frozen=True)
class SeparationDiagnosis:
    """Full accounting of the closed-form separation computation: not just
    whether an interval exists, but which single case sets each bound, and
    which case(s) make the computation impossible outright. A caller that
    reports only "found" / "not found" repeats the mistake this replaces —
    a razor-thin interval set by one case on each side reads very
    differently from a wide, well-supported one, and the case ids are what
    let a report say which situation it's in.

    `lo`/`hi` are populated whenever they're computable AT ALL — including
    when `lo >= hi` (a real, measured non-separation, not a missing input).
    Use the `interval` property, not a bare `lo is not None`, to ask "is
    there a threshold that separates content from noise": that property is
    the one place all three failure modes (blocked case, no negatives,
    lo >= hi) collapse to `None`, so callers can't accidentally treat a
    computed-but-empty interval as valid.
    """

    lo: float | None
    hi: float | None
    lo_case_id: str | None
    hi_case_id: str | None
    blocked_case_ids: tuple[str, ...]

    @property
    def interval(self) -> SeparatingInterval | None:
        if self.blocked_case_ids or self.lo is None or self.hi is None or self.lo >= self.hi:
            return None
        return SeparatingInterval(lo=self.lo, hi=self.hi)


def diagnose_separation(
    positives: list[tuple[Case, list[Scored]]],
    negatives: list[tuple[str, list[Scored]]],
    k: int,
) -> SeparationDiagnosis:
    """Closed-form replacement for sampling `sweep()` on a threshold grid.

    `negatives` pairs each off_topic case's id with its chunks — unlike
    `sweep`'s bare `list[list[Scored]]` — purely so the diagnosis can name
    which case sets `hi`, the same reporting need `positives` already serves
    by carrying `Case` instead of bare chunk lists.

    A positive case with no satisfying chunk within its own global top-k
    makes `lo` undefined outright (see `topk_satisfying_distance`) — not
    "very high", *undefined*, because no threshold recovers it. That case's
    id is returned in `blocked_case_ids` with `lo=None`; callers must not
    report a number in that situation, and must not silently drop the case
    and compute one from the rest (that reintroduces the exact bug this
    function exists to prevent — see the counterexample in
    `test_evals_retrieval_metrics.py`).

    When there are no positives or no usable negatives, the corresponding
    bound is `None` too. Otherwise both `lo` and `hi` are always populated —
    even when `lo >= hi`, which is a genuine, correctly-computed finding
    (no threshold separates content from noise here), not a missing-input
    case. Use `.interval` to collapse all of these to a single "is there a
    usable threshold" answer.
    """
    blocked = tuple(
        case.id for case, chunks in positives if topk_satisfying_distance(case, chunks, k) is None
    )
    if not positives or blocked:
        return SeparationDiagnosis(
            lo=None, hi=None, lo_case_id=None, hi_case_id=None, blocked_case_ids=blocked
        )

    lo_case_id, lo = max(
        ((case.id, topk_satisfying_distance(case, chunks, k)) for case, chunks in positives),
        key=lambda pair: pair[1],
    )

    neg_bests = [
        (case_id, min(c.distance for c in chunks)) for case_id, chunks in negatives if chunks
    ]
    if not neg_bests:
        return SeparationDiagnosis(
            lo=lo, hi=None, lo_case_id=lo_case_id, hi_case_id=None, blocked_case_ids=()
        )
    hi_case_id, hi = min(neg_bests, key=lambda pair: pair[1])

    return SeparationDiagnosis(
        lo=lo, hi=hi, lo_case_id=lo_case_id, hi_case_id=hi_case_id, blocked_case_ids=()
    )


def leave_one_out_lo(
    positives: list[tuple[Case, list[Scored]]], excluding: str, k: int
) -> float | None:
    """max(`topk_satisfying_distance`) over `positives`, excluding the case
    with id `excluding` — the sensitivity check for "how much does dropping
    ONE case move the lower bound." Returns None if excluding that case
    leaves nothing to score, or if any remaining case is itself blocked (see
    `topk_satisfying_distance`) — the same undefined-lo condition
    `diagnose_separation` guards against.
    """
    remaining = [(case, chunks) for case, chunks in positives if case.id != excluding]
    if not remaining:
        return None
    values = [topk_satisfying_distance(case, chunks, k) for case, chunks in remaining]
    if any(v is None for v in values):
        return None
    return max(values)


def recommended_point(lo: float, hi: float, decimals: int = 4) -> float:
    """The midpoint of (lo, hi], rounded to `decimals` places for display —
    validated, not just computed.

    A naive `round((lo + hi) / 2, decimals)` can round OUTSIDE the interval
    on a narrow one: interval (0.4980, 0.4999] has true midpoint 0.49895,
    which rounds to 0.50 at 2 decimal places — already past `hi`. It happens
    to be safe at 4 decimal places (0.499) for that particular interval, but
    nothing guarantees that in general, so the fix isn't "use more decimals"
    on faith — it's checking the actual rounded value before handing it to a
    reader. Raises ValueError when it doesn't fit, rather than silently
    printing a number outside the interval it claims to describe.
    """
    if lo >= hi:
        raise ValueError(f"empty interval: lo={lo} >= hi={hi}")
    value = round((lo + hi) / 2, decimals)
    if not (lo < value <= hi):
        raise ValueError(
            f"midpoint of ({lo}, {hi}] rounds to {value} at {decimals} decimal place(s), "
            "which falls outside the interval — use more decimals or report the raw bounds"
        )
    return value


def order_statistic_risk(n_positives: int, n_negatives: int) -> tuple[float, float, float]:
    """Probability that one more case of each kind would already break a
    separating interval this narrow, under the assumption that new cases are
    exchangeable with the existing ones (i.e. drawn the same way).

    If the existing negatives' closest distances are exchangeable draws, a
    fresh (n+1)th negative is — by symmetry — equally likely to be the
    smallest of the n+1, so it lands below the current floor (shrinking
    `hi`) with probability 1/(n+1). Same argument for a fresh positive
    landing above the current worst-case distance (raising `lo`):
    probability 1/(n+1). Returns `(p_positive_flips, p_negative_flips,
    p_either)`, where `p_either` treats the two events as independent:
    `1 - (1 - p_pos) * (1 - p_neg)`.

    This is a plausibility check, not a rigorous power calculation —
    "exchangeable" assumes new cases are drawn the same way as the existing
    ones, which is exactly the assumption `README.md` tells a case author to
    violate on purpose (real off_topic questions should be drawn from
    NEAR-domain topics, not the easiest negatives already in the set, which
    would only make `p_negative_flips` an underestimate of the real risk).
    """
    if n_positives < 1 or n_negatives < 1:
        raise ValueError("need at least one existing case on each side")
    p_pos = 1 / (n_positives + 1)
    p_neg = 1 / (n_negatives + 1)
    p_either = 1 - (1 - p_pos) * (1 - p_neg)
    return p_pos, p_neg, p_either
