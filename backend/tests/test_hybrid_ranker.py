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


def test_ties_break_deterministically_by_dense_rank():
    """Two chunks with identical fused scores must not reorder between runs;
    a nondeterministic retrieval order makes the eval harness unrepeatable."""
    first = fuse_rrf(["a", "b"], ["a", "b"], w_dense=0.5, w_sparse=0.5, k=60)
    second = fuse_rrf(["b", "a"], ["b", "a"], w_dense=0.5, w_sparse=0.5, k=60)
    assert [key for key, _ in first] == ["a", "b"]
    assert [key for key, _ in second] == ["b", "a"]


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
