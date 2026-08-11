"""Tests for the runner's pure helper functions. No DB, no embeddings.

`main()` itself needs Postgres + a live embedding provider and is exercised
by actually running the harness (see README.md / task reports), not by
pytest — but the decision logic it delegates to is pure and unit-tested
here, same as evals.retrieval.metrics.
"""

import argparse

import pytest

from evals.retrieval.golden_set import Case
from evals.retrieval.metrics import Scored
from evals.retrieval.run_eval import (
    _MIN_NEGATIVES_FOR_CONFIDENCE,
    _MIN_POSITIVES_FOR_CONFIDENCE,
    _display_point,
    _is_provisional,
    _model_mismatch_message,
    _off_topic_acceptance_rate,
    _positive_case_status,
    _positive_int,
)


def _s(title: str, text: str, dist: float, paper_id: str | None = None) -> Scored:
    return Scored(paper_id=paper_id or title, paper_title=title, chunk_text=text, distance=dist)


# --- _positive_int ------------------------------------------------------------


def test_positive_int_accepts_positive_values():
    assert _positive_int("1") == 1
    assert _positive_int("5") == 5


def test_positive_int_rejects_zero_and_negative():
    """simulate_retrieval's `[:k]` silently accepts k <= 0 (an empty slice),
    which would score every case an automatic miss and read as a retrieval
    failure instead of a usage error — must reject at the CLI boundary
    instead."""
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_int("0")
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_int("-3")


# --- _model_mismatch_message ---------------------------------------------------


def test_model_mismatch_message_none_when_target_model_present():
    assert _model_mismatch_message({"nomic-embed-text": 122}, "nomic-embed-text") is None


def test_model_mismatch_message_none_when_corpus_entirely_empty():
    """An empty corpus is a different, already-clear situation (the
    per-case "corpus: 0 chunks" note covers it) — not a model mismatch."""
    assert _model_mismatch_message({}, "nomic-embed-text") is None


def test_model_mismatch_message_names_the_actual_models_present():
    """The bug this guards against: every case would otherwise report "no
    paper matching ..." when the real problem is the configured
    EMBEDDING_MODEL doesn't match what's indexed — the papers ARE there."""
    msg = _model_mismatch_message({"text-embedding-3-small": 122}, "nomic-embed-text")
    assert msg is not None
    assert "nomic-embed-text" in msg
    assert "text-embedding-3-small" in msg


# --- _positive_case_status -----------------------------------------------------

CASE = Case(
    id="c1",
    kind="content",
    question="q",
    paper_title_contains="Alpha",
    expect_substrings=("target",),
)


def test_positive_case_status_none_when_winnable():
    chunks = [_s("Alpha", "the target chunk", 0.2)]
    assert _positive_case_status(CASE, chunks) is None


def test_positive_case_status_reports_missing_paper():
    """Corpus drift: the paper itself is gone."""
    chunks = [_s("Beta", "unrelated", 0.2)]
    status = _positive_case_status(CASE, chunks)
    assert status is not None
    assert "no paper matching" in status


def test_positive_case_status_reports_unwinnable_substring_not_missing_paper():
    """The case the review caught live: the paper IS present, but no chunk
    anywhere in the corpus contains the expected substring. This used to
    fall through into `positives` and silently drag recall down as an
    ordinary miss — it must be routed to `errors` with a message that names
    the real problem (missing substring), not the paper-drift message."""
    chunks = [_s("Alpha", "no matching content here", 0.2), _s("Alpha", "still nothing", 0.3)]
    status = _positive_case_status(CASE, chunks)
    assert status is not None
    assert "no paper matching" not in status
    assert "no chunk in the corpus contains" in status


# --- _is_provisional -----------------------------------------------------------


def test_is_provisional_false_when_both_sides_and_margin_clear_the_bar():
    assert (
        _is_provisional(
            n_pos=_MIN_POSITIVES_FOR_CONFIDENCE,
            n_neg=_MIN_NEGATIVES_FOR_CONFIDENCE,
            margin=0.10,
            neg_spread=0.05,
        )
        is False
    )


def test_is_provisional_true_with_plenty_of_negatives_but_few_positives():
    """The asymmetry the review caught: the gate used to check only n_neg
    and margin, never n_pos. `lo` is set by a single worst-case positive
    exactly as `hi` is set by a single closest negative, so a golden set
    with 15 negatives and only 2 positives is exactly as fragile as one with
    too few negatives — an unqualified SEPARATION FOUND from either shape
    would rest on a single witness. Plenty of negatives and a comfortable
    margin must NOT be enough on their own."""
    assert (
        _is_provisional(
            n_pos=2,
            n_neg=15,
            margin=0.20,
            neg_spread=0.01,
        )
        is True
    )


def test_is_provisional_true_with_plenty_of_positives_but_few_negatives():
    """The symmetric, already-covered case: too few negatives alone must
    also still be provisional."""
    assert (
        _is_provisional(
            n_pos=25,
            n_neg=3,
            margin=0.20,
            neg_spread=0.01,
        )
        is True
    )


def test_is_provisional_true_when_margin_does_not_clear_negative_spread():
    """Sample size alone isn't enough either — the margin must exceed the
    spread of the negatives that define it."""
    assert (
        _is_provisional(
            n_pos=_MIN_POSITIVES_FOR_CONFIDENCE,
            n_neg=_MIN_NEGATIVES_FOR_CONFIDENCE,
            margin=0.02,
            neg_spread=0.05,
        )
        is True
    )


# --- _off_topic_acceptance_rate -------------------------------------------------


def test_off_topic_acceptance_rate_none_when_no_negatives():
    assert _off_topic_acceptance_rate([], threshold=0.75) is None


def test_off_topic_acceptance_rate_none_when_every_negative_is_empty():
    """The exact bug this pins (final whole-branch review, Critical #1):
    `[[], [], []]` — one empty chunk list per off_topic case, e.g. every
    case returned zero rows from an empty `paper_chunk_embeddings` table —
    is truthy under `bool(negatives)`. The old `if negatives:` guard in
    main() computed `0 accepted / 3 = 0.0` from this shape and printed
    "accepts 0% of off-topic questions" — the exact INVERSE of the true,
    unmeasured situation (the real threshold accepts 100% here; see
    test_off_topic_acceptance_rate_computes_correctly_when_measured below
    for the live corpus's actual numbers). Must be None ("nothing measured"),
    never a computed 0.0 for this shape."""
    assert _off_topic_acceptance_rate([[], [], []], threshold=0.75) is None


def test_off_topic_acceptance_rate_computes_correctly_when_measured():
    negatives = [
        [_s("Alpha", "junk", 0.80)],  # rejected: 0.80 >= 0.75
        [_s("Beta", "junk", 0.10)],  # accepted: 0.10 < 0.75
    ]
    assert _off_topic_acceptance_rate(negatives, threshold=0.75) == 0.5


def test_off_topic_acceptance_rate_is_strict_less_than():
    """Matches production's `distance < :threshold` (strictly less than,
    chat_service.py): a chunk sitting exactly ON the threshold must not
    count as accepted."""
    negatives = [[_s("Alpha", "junk", 0.75)]]
    assert _off_topic_acceptance_rate(negatives, threshold=0.75) == 0.0


def test_off_topic_acceptance_rate_mixed_empty_and_nonempty_still_measures():
    """A mix of empty and non-empty per-case chunk lists must still be
    measured (matching `noise_floor`'s and `sweep`'s treatment of the same
    shape) — `any(negatives)` is True here, so this must NOT collapse to
    None just because one case happened to return nothing. The denominator
    stays the full case count (2), so a naive "only count usable cases" bug
    would wrongly report 1.0 instead of 0.5."""
    negatives = [[], [_s("Beta", "junk", 0.10)]]
    assert _off_topic_acceptance_rate(negatives, threshold=0.75) == 0.5


# --- _display_point --------------------------------------------------------------


def test_display_point_formats_midpoint_when_safe():
    assert _display_point(0.4543, 0.4749) == "0.4646"


def test_display_point_falls_back_to_raw_bounds_when_recommended_point_raises():
    """`recommended_point` raises ValueError when an interval is narrower
    than display precision (see its own tests in
    test_evals_retrieval_metrics.py). That must never propagate out of
    main(): uncaught, it would take out the ROBUST FINDING section printed
    after it and, with --json, the whole file dump — for a display nicety.
    Must fall back to the raw bounds instead of crashing."""
    text = _display_point(0.499991, 0.499992)
    assert "0.499991" in text
    assert "0.499992" in text


# --- targeted mode -------------------------------------------------------------


def test_scope_to_paper_keeps_only_the_matching_papers_chunks():
    from evals.retrieval.run_eval import _scope_to_paper

    chunks = [
        _s("Survey of Important Issues", "a", 0.30, paper_id="p1"),
        _s("Applications of Multi-Agent RL", "b", 0.31, paper_id="p2"),
        _s("Survey of Important Issues", "c", 0.55, paper_id="p1"),
    ]
    scoped = _scope_to_paper(chunks, "survey of important")
    assert [c.chunk_text for c in scoped] == ["a", "c"]


def test_scope_to_paper_is_case_insensitive_and_sorted_by_distance():
    from evals.retrieval.run_eval import _scope_to_paper

    chunks = [
        _s("Survey Of Important Issues", "far", 0.55, paper_id="p1"),
        _s("Survey Of Important Issues", "near", 0.30, paper_id="p1"),
    ]
    assert [c.chunk_text for c in _scope_to_paper(chunks, "SURVEY of")] == ["near", "far"]


def test_scope_to_nearest_paper_picks_the_paper_holding_the_closest_chunk():
    """The targeter-misfire simulation for off_topic cases: production would
    scope to whichever paper looked closest, so that is the paper whose
    kept-count measures the ceiling."""
    from evals.retrieval.run_eval import _scope_to_nearest_paper

    chunks = [
        _s("Far Paper", "x", 0.80, paper_id="p2"),
        _s("Near Paper", "y", 0.76, paper_id="p1"),
        _s("Near Paper", "z", 0.88, paper_id="p1"),
    ]
    scoped = _scope_to_nearest_paper(chunks)
    assert {c.paper_id for c in scoped} == {"p1"}
    assert [c.chunk_text for c in scoped] == ["y", "z"]


def test_scope_to_nearest_paper_handles_no_chunks():
    from evals.retrieval.run_eval import _scope_to_nearest_paper

    assert _scope_to_nearest_paper([]) == []


def test_production_cut_applies_ceiling_then_budget_then_delta():
    """Mirrors production's literal order: SQL filters by the ceiling and
    LIMITs to the budget, then the delta cut runs in Python. The order
    doesn't actually change the result today — ceiling, budget and delta are
    all prefix truncations of one distance-ascending sort, so any ordering
    yields the same min(n_ceiling, n_delta, budget). Production's order is
    still mirrored here so a future non-prefix change on either side (MMR,
    dedup, a rerank) surfaces as a divergence instead of hiding behind
    today's accidental equivalence."""
    from evals.retrieval.run_eval import _production_cut

    chunks = [
        _s("P", "a", 0.50, paper_id="p1"),
        _s("P", "b", 0.70, paper_id="p1"),
        _s("P", "c", 0.80, paper_id="p1"),  # beyond delta, inside ceiling
        _s("P", "d", 0.90, paper_id="p1"),  # beyond ceiling
    ]
    kept = _production_cut(chunks, ceiling=0.85, delta=0.25, budget=60)
    assert [c.chunk_text for c in kept] == ["a", "b"]


def test_production_cut_budget_binds_before_the_delta():
    from evals.retrieval.run_eval import _production_cut

    chunks = [_s("P", f"c{i}", 0.50 + i * 0.001, paper_id="p1") for i in range(10)]
    kept = _production_cut(chunks, ceiling=0.85, delta=0.25, budget=4)
    assert len(kept) == 4


def test_production_cut_on_off_topic_distances_keeps_almost_nothing():
    """The measured shape of the ceiling's job: an off-topic question's
    nearest chunk sits at 0.7518-0.8581, so a delta-only rule would keep the
    whole paper while the ceiling keeps 0-1 chunks."""
    from evals.retrieval.run_eval import _production_cut

    chunks = [_s("P", f"c{i}", 0.86 + i * 0.005, paper_id="p1") for i in range(20)]
    assert _production_cut(chunks, ceiling=0.85, delta=0.25, budget=60) == []


# --- _targeted_case_status -------------------------------------------------------

_TARGETED_CASE = Case(
    id="joint-optimization-case",
    kind="content",
    question="q",
    paper_title_contains="Joint Optimization",
    expect_substrings=("target",),
)

_TARGETED_OFF_TOPIC_CASE = Case(
    id="off-topic-case",
    kind="off_topic",
    question="q",
    paper_title_contains=None,
    expect_substrings=(),
)


def test_targeted_case_status_none_when_title_matches_exactly_one_paper():
    from evals.retrieval.run_eval import _targeted_case_status

    chunks = [
        _s("Joint Optimization of X and Y", "a", 0.30, paper_id="p1"),
        _s("Joint Optimization of X and Y", "b", 0.55, paper_id="p1"),
    ]
    assert _targeted_case_status(_TARGETED_CASE, chunks) is None


def test_targeted_case_status_flags_ambiguous_title_match():
    """The finding this test pins: a second paper whose title also contains
    the needle would silently blend its chunks into the single-paper scope,
    inflating paper_chunks/intra_rank/kept into numbers production's
    single_paper policy (chat_service._retrieve_paper_chunks) could never
    produce -- it assumes exactly one paper in scope."""
    from evals.retrieval.run_eval import _targeted_case_status

    chunks = [
        _s("Joint Optimization of X and Y", "a", 0.30, paper_id="p1"),
        _s("Joint Optimization for Z", "b", 0.40, paper_id="p2"),
    ]
    status = _targeted_case_status(_TARGETED_CASE, chunks)
    assert status is not None
    assert "2 distinct papers" in status


def test_targeted_case_status_none_for_off_topic_regardless_of_ambiguity():
    """off_topic cases scope via _scope_to_nearest_paper, which always
    returns exactly one paper by construction -- ambiguity can't apply, and
    off_topic cases carry no paper_title_contains to even check."""
    from evals.retrieval.run_eval import _targeted_case_status

    chunks = [
        _s("Paper One", "a", 0.30, paper_id="p1"),
        _s("Paper Two", "b", 0.40, paper_id="p2"),
    ]
    assert _targeted_case_status(_TARGETED_OFF_TOPIC_CASE, chunks) is None
