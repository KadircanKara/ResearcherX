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


def _s(title: str, text: str, dist: float, paper_id: str | None = None) -> Scored:
    """paper_id defaults to the title so existing per-paper-by-title tests
    don't need updating; tests that care about id-vs-title pass it
    explicitly."""
    return Scored(paper_id=paper_id or title, paper_title=title, chunk_text=text, distance=dist)


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
    """With k=1, each paper contributes only its nearest chunk, so a paper's
    position in `by_paper`'s insertion order already coincides with its
    distance rank -- this input alone can't distinguish "sorted at the end"
    from "left in insertion order." Use k=2 so a paper's second chunk can
    land farther out than another paper's chunk, which only the final
    distance-sort (not insertion order) places correctly."""
    chunks = [_s("Beta", "b1", 0.30), _s("Alpha", "a1", 0.10), _s("Alpha", "a2", 0.50)]
    got = simulate_retrieval(chunks, k=2)
    assert [c.chunk_text for c in got] == ["a1", "b1", "a2"]


def test_simulate_retrieval_interleaves_papers_by_distance():
    got = simulate_retrieval(
        [_s("Alpha", "a1", 0.10), _s("Alpha", "a2", 0.90), _s("Beta", "b1", 0.20)], k=2
    )
    assert [c.chunk_text for c in got] == ["a1", "b1", "a2"]


def test_simulate_retrieval_groups_by_paper_id_not_title():
    """Production keys the per-paper query on paper_id (chat_service.py
    queries `WHERE paper_id = :paper_id`). Two distinct papers that happen
    to share a title -- e.g. both have a missing title defaulted to "" --
    must not collapse into one shared top-k budget."""
    chunks = [
        _s("", "p1a", 0.10, paper_id="p1"),
        _s("", "p1b", 0.11, paper_id="p1"),
        _s("", "p2a", 0.15, paper_id="p2"),
        _s("", "p2b", 0.16, paper_id="p2"),
    ]
    got = simulate_retrieval(chunks, k=1)
    assert [c.chunk_text for c in got] == ["p1a", "p2a"]


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
