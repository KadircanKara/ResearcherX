"""Tests for the eval harness's pure scoring functions. No DB, no embeddings."""

import pytest

from retrieval_eval.golden_set import Case
from retrieval_eval.metrics import (
    Scored,
    best_satisfying_distance,
    first_satisfying_rank,
    mean_reciprocal_rank,
    noise_floor,
    recall_at_k,
    separating_threshold,
    sweep,
    simulate_retrieval,
    SweepRow,
)

CASE = Case(
    id="c",
    kind="content",
    question="q",
    paper_title_contains="Alpha",
    expect_substrings=("target",),
)


def _s(title: str, text: str, dist: float) -> Scored:
    return Scored(paper_title=title, chunk_text=text, distance=dist)


def test_simulate_retrieval_takes_top_k_per_paper_not_globally():
    """Mirrors chat_service._retrieve_paper_chunks: k per paper, concatenated.
    A global top-k would starve every paper but the closest one."""
    chunks = [
        _s("Alpha", "a1", 0.10),
        _s("Alpha", "a2", 0.11),
        _s("Alpha", "a3", 0.12),
        _s("Beta", "b1", 0.50),
        _s("Beta", "b2", 0.51),
    ]
    got = simulate_retrieval(chunks, k=2)
    assert [c.chunk_text for c in got] == ["a1", "a2", "b1", "b2"]


def test_simulate_retrieval_sorts_result_by_distance():
    chunks = [_s("Beta", "b1", 0.20), _s("Alpha", "a1", 0.10)]
    assert [c.chunk_text for c in simulate_retrieval(chunks, k=1)] == ["a1", "b1"]


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


def test_best_satisfying_distance_picks_the_closest_hit():
    chunks = [_s("Alpha", "the target far", 0.40), _s("Alpha", "the target near", 0.20)]
    assert best_satisfying_distance(CASE, chunks) == 0.20


def test_noise_floor_is_the_closest_any_negative_got():
    assert noise_floor([[_s("Alpha", "x", 0.55)], [_s("Beta", "y", 0.44)]]) == 0.44


def test_sweep_reports_recall_and_false_accept_per_threshold():
    positives = [(CASE, [_s("Alpha", "the target", 0.50)])]
    negatives = [[_s("Alpha", "junk", 0.60)]]
    rows = sweep(positives, negatives, k=5, thresholds=(0.45, 0.55, 0.65))
    assert rows[0] == SweepRow(threshold=0.45, content_recall=0.0, off_topic_false_accept=0.0)
    assert rows[1] == SweepRow(threshold=0.55, content_recall=1.0, off_topic_false_accept=0.0)
    assert rows[2] == SweepRow(threshold=0.65, content_recall=1.0, off_topic_false_accept=1.0)


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
