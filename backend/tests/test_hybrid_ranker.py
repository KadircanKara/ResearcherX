"""fuse_rrf + keep_within_rank_window: the entire hybrid ranking policy.

Pure on purpose, exactly like intra_paper_ranker: production
(chat_service) and the eval harness call the SAME functions, so the
policy cannot drift between what ships and what is measured.
"""

import pytest

from app.services.hybrid_ranker import fuse_rrf, keep_within_rank_window


def test_both_arms_empty_fuses_to_nothing():
    assert fuse_rrf([], [], w_dense=0.7, w_sparse=0.3, k=60) == []


def test_empty_sparse_arm_preserves_dense_order():
    """The stopword-only question. websearch_to_tsquery returns an empty
    query, `@@` matches nothing, and fusion must collapse to exactly the
    dense ordering rather than doing something surprising."""
    fused = fuse_rrf(["a", "b", "c"], [], w_dense=0.7, w_sparse=0.3, k=60)
    assert [key for key, _ in fused] == ["a", "b", "c"]


def test_empty_dense_arm_preserves_sparse_order():
    fused = fuse_rrf([], ["a", "b", "c"], w_dense=0.7, w_sparse=0.3, k=60)
    assert [key for key, _ in fused] == ["a", "b", "c"]


def test_zero_sparse_weight_is_identical_to_dense_only():
    """The kill switch's arithmetic guarantee: w_sparse=0 must reproduce the
    dense ordering even when the sparse arm disagrees violently."""
    fused = fuse_rrf(["a", "b"], ["b", "a"], w_dense=1.0, w_sparse=0.0, k=60)
    assert [key for key, _ in fused] == ["a", "b"]


def test_scores_are_the_weighted_reciprocal_ranks():
    """Hand-computed. 'a' is dense rank 1 and sparse rank 2; 'b' is the
    reverse. With k=60: a = 0.7/61 + 0.3/62, b = 0.7/62 + 0.3/61."""
    fused = dict(fuse_rrf(["a", "b"], ["b", "a"], w_dense=0.7, w_sparse=0.3, k=60))
    assert fused["a"] == pytest.approx(0.7 / 61 + 0.3 / 62)
    assert fused["b"] == pytest.approx(0.7 / 62 + 0.3 / 61)


def test_a_chunk_in_only_the_sparse_arm_still_scores():
    """The whole point of the change. A chunk the dense gate rejected is
    admitted by the sparse arm and contributes only its sparse term."""
    fused = dict(fuse_rrf(["a"], ["z"], w_dense=0.7, w_sparse=0.3, k=60))
    assert fused["z"] == pytest.approx(0.3 / 61)


def test_sparse_agreement_can_outrank_a_better_dense_chunk():
    """'b' is dense rank 2 but sparse rank 1; 'a' is dense rank 1 and absent
    from the sparse arm entirely. At 70/30 the agreement wins, which is the
    behaviour the rank-53 failure needs."""
    fused = fuse_rrf(["a", "b"], ["b"], w_dense=0.7, w_sparse=0.3, k=60)
    assert [key for key, _ in fused] == ["b", "a"]


def test_matching_rank_order_reproduces_regardless_of_input_order():
    """NOT a score-tie test -- 'a'/'b' at the same rank in both arms score
    0.5/61 vs 0.5/62, which are unequal, so this never exercises the
    tie-break tuple at all. What it verifies instead: `fuse_rrf` is a pure
    function of (dense_ranked, sparse_ranked), so feeding the same relative
    order back in a different input order reproduces the same relative
    output order -- i.e. it is not accidentally keying off dict/set
    iteration order. See test_exact_score_ties_break_by_dense_then_sparse_
    then_insertion_order below for an actual tie."""
    first = fuse_rrf(["a", "b"], ["a", "b"], w_dense=0.5, w_sparse=0.5, k=60)
    second = fuse_rrf(["b", "a"], ["b", "a"], w_dense=0.5, w_sparse=0.5, k=60)
    assert [key for key, _ in first] == ["a", "b"]
    assert [key for key, _ in second] == ["b", "a"]


def test_exact_score_ties_break_by_dense_then_sparse_then_insertion_order():
    """A genuine tie: 'a' is dense rank 1 / sparse rank 2 and 'b' is dense
    rank 2 / sparse rank 1, so both score 0.5/61 + 0.5/62 exactly -- unlike
    the same-order case above, this cannot be told apart by score alone.
    Ties are now reachable in production too (see the `, c.id` tiebreaker
    added to the sparse CTE's ORDER BY), so this must pin the documented
    resolution: dense rank first, which puts 'a' (dense rank 1) ahead of
    'b' (dense rank 2)."""
    fused = fuse_rrf(["a", "b"], ["b", "a"], w_dense=0.5, w_sparse=0.5, k=60)
    a_score = 0.5 / 61 + 0.5 / 62
    b_score = 0.5 / 62 + 0.5 / 61
    assert a_score == b_score
    assert [key for key, _ in fused] == ["a", "b"]


def test_duplicate_keys_within_one_arm_take_the_best_rank():
    """Defensive: the SQL should never emit a key twice in one arm, but if a
    future join does, the nearer rank must win rather than the later one
    overwriting it."""
    fused = dict(fuse_rrf(["a", "a"], [], w_dense=1.0, w_sparse=0.0, k=60))
    assert fused["a"] == pytest.approx(1.0 / 61)


def test_window_of_zero_keeps_nothing():
    assert keep_within_rank_window([0.9, 0.8], window=0) == 0


def test_window_larger_than_the_list_keeps_everything():
    assert keep_within_rank_window([0.9, 0.8], window=10) == 2


def test_window_truncates_to_its_size():
    assert keep_within_rank_window([0.9, 0.8, 0.7, 0.6], window=2) == 2


def test_window_on_empty_input_keeps_nothing():
    assert keep_within_rank_window([], window=10) == 0
