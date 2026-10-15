"""The score-cutoff sweep's pure table logic."""

import pytest

from evals.retrieval.score_cutoff import ScoredCase, _order, budget_recall, cutoff_rows


def _case(scores, hits, negative=False):
    return ScoredCase(case_id="c", negative=negative, scores=tuple(scores), hits=tuple(hits))


def test_budget_recall_counts_a_hit_only_inside_the_budget():
    cases = [
        _case([0.9, 0.8, 0.7], [False, False, True]),  # answer at rank 3
        _case([0.9, 0.8, 0.7], [True, False, False]),  # answer at rank 1
        _case([0.1], [False], negative=True),  # never counted
    ]
    assert budget_recall(cases, 2) == pytest.approx(0.5)
    assert budget_recall(cases, 3) == pytest.approx(1.0)


def test_a_cutoff_trades_answer_recall_against_empty_off_topic_catalogs():
    cases = [
        _case([0.9, 0.2], [False, True]),  # answer scored low
        _case([0.6, 0.1], [True, False]),
        _case([0.4, 0.05], [False, False], negative=True),
        _case([0.05, 0.01], [False, False], negative=True),
    ]
    rows = {r["threshold"]: r for r in cutoff_rows(cases, (0.0, 0.3, 0.5), budget=10)}

    assert rows[0.0]["recall"] == 1.0 and rows[0.0]["neg_empty"] == 0.0
    assert rows[0.3]["recall"] == 0.5  # the low-scored answer is cut
    assert rows[0.3]["neg_empty"] == 0.5 and rows[0.3]["neg_kept"] == 0.5
    assert rows[0.5]["neg_empty"] == 1.0
    assert rows[0.5]["pos_kept"] == 1.0


def test_an_unscored_candidate_survives_only_the_no_cutoff_control():
    cases = [_case([0.9, None], [False, True])]
    rows = {r["threshold"]: r for r in cutoff_rows(cases, (0.0, 0.01), budget=10)}
    assert rows[0.0]["recall"] == 1.0
    assert rows[0.01]["recall"] == 0.0


def test_scorer_order_is_by_score_with_ties_and_the_unscored_tail_in_fused_order():
    assert _order([0.2, None, 0.9, 0.2, None]) == [2, 0, 3, 1, 4]
