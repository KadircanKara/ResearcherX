"""Tests for the eval harness's pure scoring functions. No DB, no embeddings."""

import pytest

from evals.retrieval.golden_set import Case
from evals.retrieval.metrics import (
    Scored,
    SeparatingInterval,
    best_satisfying_distance,
    diagnose_separation,
    first_satisfying_rank,
    leave_one_out_lo,
    mean_reciprocal_rank,
    noise_floor,
    order_statistic_risk,
    recall_at_k,
    recommended_point,
    separating_threshold,
    sweep,
    simulate_retrieval,
    topk_satisfying_distance,
    SweepRow,
)

CASE = Case(
    id="c",
    kind="content",
    question="q",
    paper_title_contains="Alpha",
    expect_substrings=("target",),
)


def _s(title: str, text: str, dist: float, paper_id: str | None = None) -> Scored:
    """paper_id defaults to the title as a convenience for tests that don't
    care about the distinction; pass it explicitly for a test that needs a
    paper_id different from its title (e.g. two papers sharing a title)."""
    return Scored(paper_id=paper_id or title, paper_title=title, chunk_text=text, distance=dist)


def test_simulate_retrieval_takes_a_global_top_k_not_per_paper():
    """Mirrors chat_service._retrieve_paper_chunks: one global top-k.

    Per-paper simulation is why recall here was structurally close to
    invariant under corpus growth — every paper kept its own budget no matter
    how large the library got, so growth could not crowd anything out.
    """
    chunks = [
        _s("Alpha", "a1", 0.10),
        _s("Alpha", "a2", 0.11),
        _s("Alpha", "a3", 0.12),
        _s("Beta", "b1", 0.50),
        _s("Beta", "b2", 0.51),
    ]
    got = simulate_retrieval(chunks, k=4)
    assert [c.chunk_text for c in got] == ["a1", "a2", "a3", "b1"]


def test_simulate_retrieval_sorts_result_by_distance():
    """The input list is deliberately NOT in distance order (Beta's 0.30
    comes before Alpha's nearer 0.10) -- a naive `chunks[:k]` that skipped
    sorting entirely would return `["b1", "a1"]` here, wrong both in order
    and in which chunk ends up first. k=2 also drops the farthest chunk
    (Alpha's 0.50) from the result, proving the cut applies AFTER the sort,
    not before it."""
    chunks = [_s("Beta", "b1", 0.30), _s("Alpha", "a1", 0.10), _s("Alpha", "a2", 0.50)]
    got = simulate_retrieval(chunks, k=2)
    assert [c.chunk_text for c in got] == ["a1", "b1"]


def test_simulate_retrieval_interleaves_papers_by_distance():
    got = simulate_retrieval(
        [_s("Alpha", "a1", 0.10), _s("Alpha", "a2", 0.90), _s("Beta", "b1", 0.20)], k=2
    )
    assert [c.chunk_text for c in got] == ["a1", "b1"]


def test_first_satisfying_rank_is_one_based():
    retrieved = [_s("Alpha", "no match", 0.1), _s("Alpha", "the target here", 0.2)]
    assert first_satisfying_rank(CASE, retrieved) == 2


def test_first_satisfying_rank_none_when_absent():
    assert first_satisfying_rank(CASE, [_s("Alpha", "nothing", 0.1)]) is None


def test_recall_at_k_counts_cases_with_a_hit():
    hit = (CASE, [_s("Alpha", "the target", 0.1)])
    miss = (CASE, [_s("Alpha", "nope", 0.1)])
    assert recall_at_k([hit, miss], k=5) == 0.5


def test_recall_at_k_respects_k():
    """A hit beyond k is not a hit — the LLM never sees it."""
    chunks = [_s("Alpha", "nope", 0.1), _s("Alpha", "the target", 0.2)]
    assert recall_at_k([(CASE, chunks)], k=1) == 0.0
    assert recall_at_k([(CASE, chunks)], k=2) == 1.0


def test_mrr_uses_reciprocal_of_first_hit_rank():
    first = (CASE, [_s("Alpha", "the target", 0.1)])
    second = (CASE, [_s("Alpha", "no", 0.1), _s("Alpha", "the target", 0.2)])
    assert mean_reciprocal_rank([first, second], k=5) == (1.0 + 0.5) / 2


def test_mrr_respects_k():
    """A hit beyond k must not contribute a reciprocal-rank score — MRR needs
    to see the same top-k the LLM would, just like recall_at_k."""
    chunks = [_s("Alpha", "nope", 0.1), _s("Alpha", "the target", 0.2)]
    assert mean_reciprocal_rank([(CASE, chunks)], k=1) == 0.0


def test_best_satisfying_distance_picks_the_closest_hit():
    """The closest hit is listed FIRST here, not last -- a min()-> [-1]
    (last-of-list) mutation would then return the farther one and fail."""
    chunks = [_s("Alpha", "the target near", 0.20), _s("Alpha", "the target far", 0.40)]
    assert best_satisfying_distance(CASE, chunks) == 0.20


def test_noise_floor_takes_the_closest_chunk_within_each_case():
    """Both the inner min (closest chunk within one case) and the outer min
    (closest across cases) are exercised with the closest value listed
    FIRST, not last -- a min -> [-1] mutation at either level would then
    pick the farther value and fail."""
    assert noise_floor([[_s("A", "x", 0.44), _s("A", "y", 0.80)]]) == 0.44
    assert noise_floor([[_s("A", "x", 0.44)], [_s("B", "y", 0.80)]]) == 0.44


def test_sweep_reports_recall_and_false_accept_per_threshold():
    positives = [(CASE, [_s("Alpha", "the target", 0.50)])]
    negatives = [[_s("Alpha", "junk", 0.60)]]
    rows = sweep(positives, negatives, k=5, thresholds=(0.45, 0.55, 0.65))
    assert rows[0] == SweepRow(threshold=0.45, content_recall=0.0, off_topic_false_accept=0.0)
    assert rows[1] == SweepRow(threshold=0.55, content_recall=1.0, off_topic_false_accept=0.0)
    assert rows[2] == SweepRow(threshold=0.65, content_recall=1.0, off_topic_false_accept=1.0)


def test_sweep_cutoff_is_strict_matching_production_distance_lt_threshold():
    """Production filters `distance < :threshold` (strictly less than). A
    chunk sitting exactly ON the threshold must be excluded from both the
    positive and negative sides."""
    rows = sweep(
        [(CASE, [_s("Alpha", "the target", 0.50)])],
        [[_s("Alpha", "junk", 0.50)]],
        k=5,
        thresholds=(0.50,),
    )
    assert rows[0] == SweepRow(0.50, 0.0, 0.0)


def test_sweep_raises_without_negatives():
    """With no negatives, every threshold would trivially report zero
    false-accept, letting separating_threshold return a separation that was
    never measured. Must raise instead of silently fabricating that number."""
    positives = [(CASE, [_s("Alpha", "the target", 0.50)])]
    with pytest.raises(ValueError):
        sweep(positives, [], k=5, thresholds=(0.55, 0.65, 0.75))


def test_sweep_raises_when_all_negatives_are_empty():
    """off_topic cases that each returned zero chunks are indistinguishable
    from having no negatives at all — a bare `if not negatives` check would
    miss this shape (the outer list itself is non-empty)."""
    positives = [(CASE, [_s("Alpha", "the target", 0.50)])]
    with pytest.raises(ValueError):
        sweep(positives, [[], []], k=5, thresholds=(0.55, 0.65, 0.75))


def test_recall_at_k_raises_on_no_cases():
    """Zero cases scored is not the same as zero recall — reporting 0.0 would
    read as 'measured and failed' instead of 'nothing was measured'."""
    with pytest.raises(ValueError):
        recall_at_k([], k=5)


def test_mrr_raises_on_no_cases():
    with pytest.raises(ValueError):
        mean_reciprocal_rank([], k=5)


def test_separating_threshold_finds_full_recall_zero_false_accept():
    rows = [
        SweepRow(0.45, 0.0, 0.0),
        SweepRow(0.55, 1.0, 0.0),
        SweepRow(0.65, 1.0, 1.0),
    ]
    assert separating_threshold(rows) == 0.55


def test_separating_threshold_none_when_populations_overlap():
    """The expected real-world result: no cutoff both admits content and
    rejects noise. Must return None rather than a best-effort number."""
    rows = [SweepRow(0.45, 0.0, 0.0), SweepRow(0.55, 0.5, 1.0), SweepRow(0.65, 1.0, 1.0)]
    assert separating_threshold(rows) is None


def test_separating_threshold_finds_none_but_close_gap_the_grid_can_miss():
    """Documents why diagnose_separation exists: a grid samples fixed points,
    so a real but narrow separating interval can fall entirely between two
    grid points and be reported as "no separation" even though one exists.
    Grid at 0.10 (miss) and 0.20 (over-admits) straddles the true interval
    (0.12, 0.18] without ever sampling inside it."""
    rows = [
        SweepRow(0.10, 0.0, 0.0),
        SweepRow(0.20, 1.0, 1.0),
    ]
    assert separating_threshold(rows) is None


def test_separating_threshold_is_order_independent():
    """Must return the LOWEST qualifying threshold even when rows aren't
    ascending -- a first-match-in-iteration-order implementation would
    return whichever qualifying row happens to come first, which for a
    descending list is the most permissive (highest) cutoff, not the
    lowest."""
    rows = [
        SweepRow(0.65, 1.0, 0.0),
        SweepRow(0.55, 1.0, 0.0),
        SweepRow(0.45, 0.0, 0.0),
    ]
    assert separating_threshold(rows) == 0.55


# --- topk_satisfying_distance / diagnose_separation -------------------------

CASE_P1 = Case(
    id="p1-case",
    kind="content",
    question="q",
    paper_title_contains="P1",
    expect_substrings=("target chunk",),
)


def test_topk_satisfying_distance_matches_best_when_within_top_k():
    chunks = [_s("Alpha", "no match", 0.1), _s("Alpha", "the target here", 0.2)]
    assert topk_satisfying_distance(CASE, chunks, k=5) == 0.2
    assert topk_satisfying_distance(CASE, chunks, k=5) == best_satisfying_distance(CASE, chunks)


def test_topk_satisfying_distance_none_when_crowded_out_of_top_k():
    """Counterexample for the bug code review caught: best_satisfying_distance
    ignores whether nearer chunks crowd the satisfying chunk out of the
    GLOBAL top-k (production applies no per-paper ceiling -- see
    metrics.simulate_retrieval). Here 5 irrelevant chunks are all nearer than
    the one satisfying chunk, so the satisfying chunk misses EVERY global
    top-5 for every possible threshold -- not because of the threshold, but
    because 5 competitors already outrank it regardless of any cutoff.
    best_satisfying_distance happily reports 0.10 (it exists in the corpus);
    the top-k-aware function must report None, because no threshold ever
    retrieves it at k=5.
    """
    chunks = [
        _s("P1", "irrelevant", 0.05),
        _s("P1", "irrelevant", 0.06),
        _s("P1", "irrelevant", 0.07),
        _s("P1", "irrelevant", 0.08),
        _s("P1", "irrelevant", 0.09),
        _s("P1", "the target chunk", 0.10),
    ]
    assert best_satisfying_distance(CASE_P1, chunks) == 0.10
    assert topk_satisfying_distance(CASE_P1, chunks, k=5) is None


def test_diagnose_separation_finds_interval_and_names_the_deciding_cases():
    positives = [
        (CASE, [_s("Alpha", "the target", 0.20)]),
        (
            Case(
                id="c2",
                kind="content",
                question="q",
                paper_title_contains="Beta",
                expect_substrings=("target",),
            ),
            [_s("Beta", "the target", 0.30)],
        ),
    ]
    negatives = [("n1", [_s("Gamma", "junk", 0.60)]), ("n2", [_s("Gamma", "junk", 0.70)])]
    d = diagnose_separation(positives, negatives, k=5)
    assert d.blocked_case_ids == ()
    assert d.lo_case_id == "c2"  # 0.30 is the worse (farther) of the two positives
    assert d.hi_case_id == "n1"  # 0.60 is the closer (worse) of the two negatives
    assert d.interval == SeparatingInterval(lo=0.30, hi=0.60)


def test_diagnose_separation_reports_blocked_case_instead_of_a_wrong_interval():
    """If diagnose_separation used best_satisfying_distance (the naive
    approach) instead of topk_satisfying_distance, it would report a
    seemingly-safe interval starting just above 0.10 here -- and ANY
    threshold recommended from that interval achieves ZERO recall for this
    case, because its satisfying chunk never survives the GLOBAL top-5 cut
    regardless of T (production applies no per-paper ceiling; see the
    counterexample test above). The correct behavior is to refuse to name an
    interval at all, and say which case blocks it.
    """
    positive_chunks = [
        _s("P1", "irrelevant", 0.05),
        _s("P1", "irrelevant", 0.06),
        _s("P1", "irrelevant", 0.07),
        _s("P1", "irrelevant", 0.08),
        _s("P1", "irrelevant", 0.09),
        _s("P1", "the target chunk", 0.10),
    ]
    negative_chunks = [_s("P2", "junk", 0.50)]
    d = diagnose_separation([(CASE_P1, positive_chunks)], [("neg1", negative_chunks)], k=5)
    assert d.blocked_case_ids == (CASE_P1.id,)
    assert d.interval is None

    # Prove the naive alternative really would have been wrong: at the
    # threshold a best_satisfying_distance-based formula would recommend,
    # actual recall for this case is 0, not 1.
    naive_lo = best_satisfying_distance(CASE_P1, positive_chunks)
    naive_threshold = naive_lo + 0.01
    filtered = [c for c in positive_chunks if c.distance < naive_threshold]
    assert first_satisfying_rank(CASE_P1, simulate_retrieval(filtered, k=5)) is None


def test_diagnose_separation_none_interval_when_no_usable_negatives():
    positives = [(CASE, [_s("Alpha", "the target", 0.20)])]
    d = diagnose_separation(positives, [], k=5)
    assert d.lo == 0.20
    assert d.hi is None
    assert d.hi_case_id is None
    assert d.blocked_case_ids == ()
    assert d.interval is None


def test_diagnose_separation_none_interval_when_lo_at_or_above_hi():
    """A genuine, correctly-computed non-separation: lo and hi are both
    populated (this is NOT a missing-input case), but .interval is still
    None because lo >= hi means no threshold can separate the populations."""
    positives = [(CASE, [_s("Alpha", "the target", 0.60)])]
    negatives = [("n1", [_s("Gamma", "junk", 0.50)])]
    d = diagnose_separation(positives, negatives, k=5)
    assert d.lo == 0.60
    assert d.hi == 0.50
    assert d.interval is None


def test_leave_one_out_lo_drops_the_named_case():
    positives = [
        (CASE, [_s("Alpha", "the target", 0.50)]),
        (
            Case(
                id="c2",
                kind="content",
                question="q",
                paper_title_contains="Beta",
                expect_substrings=("target",),
            ),
            [_s("Beta", "the target", 0.20)],
        ),
    ]
    assert leave_one_out_lo(positives, excluding=CASE.id, k=5) == 0.20


def test_leave_one_out_lo_none_when_nothing_remains():
    positives = [(CASE, [_s("Alpha", "the target", 0.50)])]
    assert leave_one_out_lo(positives, excluding=CASE.id, k=5) is None


# --- recommended_point -------------------------------------------------------


def test_recommended_point_uses_midpoint_when_safe():
    assert recommended_point(0.4543, 0.4749, decimals=4) == 0.4646


def test_recommended_point_raises_when_rounding_would_escape_the_interval():
    """The review's own example: interval (0.4980, 0.4999] has true midpoint
    0.49895. At 2 decimal places that rounds to 0.50 -- past `hi` -- which is
    exactly the bug that made the original grid-threshold printer misleading.
    The function must refuse to return it, not silently hand back a number
    outside the interval it claims to describe."""
    naive = round((0.4980 + 0.4999) / 2, 2)
    assert naive > 0.4999  # prove the naive rounding really is broken here
    with pytest.raises(ValueError):
        recommended_point(0.4980, 0.4999, decimals=2)


def test_recommended_point_is_safe_at_default_precision_for_the_same_interval():
    """The same interval as above, but at this module's actual precision
    (4dp, per the default) -- must succeed and stay in bounds."""
    value = recommended_point(0.4980, 0.4999)
    assert 0.4980 < value <= 0.4999


def test_recommended_point_raises_on_empty_interval():
    with pytest.raises(ValueError):
        recommended_point(0.50, 0.50)
    with pytest.raises(ValueError):
        recommended_point(0.60, 0.50)


def test_recommended_point_raises_when_interval_narrower_than_precision():
    """An interval narrower than 10**-decimals may have no representable
    point at that precision -- must raise rather than print one outside
    (lo, hi]."""
    with pytest.raises(ValueError):
        recommended_point(0.499991, 0.499992, decimals=4)


# --- order_statistic_risk -----------------------------------------------------


def test_order_statistic_risk_matches_hand_derived_example():
    """The exact case from the review: 8 positives, 3 negatives -> a 9th
    positive has a 1-in-9 chance of exceeding the current max, a 4th
    negative has a 1-in-4 chance of falling below the current floor, and
    together there's about a 1-in-3 (33%) chance at least one happens."""
    p_pos, p_neg, p_either = order_statistic_risk(n_positives=8, n_negatives=3)
    assert p_pos == pytest.approx(1 / 9)
    assert p_neg == pytest.approx(1 / 4)
    assert p_either == pytest.approx(1 - (8 / 9) * (3 / 4))
    assert p_either == pytest.approx(0.3333, abs=0.001)


def test_order_statistic_risk_raises_without_at_least_one_case_each_side():
    with pytest.raises(ValueError):
        order_statistic_risk(n_positives=0, n_negatives=3)
    with pytest.raises(ValueError):
        order_statistic_risk(n_positives=3, n_negatives=0)
