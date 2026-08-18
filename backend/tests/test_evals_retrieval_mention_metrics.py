"""Tests for the multi-mention harness's pure helpers. No DB, no embeddings."""

from evals.retrieval.mention_metrics import (
    MentionOutcome,
    PaperBest,
    admitted_papers,
    answer_paper_zero_rate,
    both_represented_rate,
    count_by_paper,
    count_in_band,
    mean_kept,
    mean_second_paper_share,
    merge_round_robin,
    nearest_other,
    representation_rate,
    seeded_other,
    survival_rate,
    survival_regressions,
    worst_kept,
)

PAPERS = [
    PaperBest(paper_id="a", title="Alpha", best=0.40),
    PaperBest(paper_id="b", title="Beta", best=0.55),
    PaperBest(paper_id="c", title="Gamma", best=0.78),
    PaperBest(paper_id="d", title="Delta", best=None),
]


def _outcome(
    *,
    case_id="c1",
    config="status-quo",
    pairing="nearest",
    scope=("a", "b"),
    answer_paper_id="a",
    kept_by_paper=None,
    answer_survived=True,
):
    kept_by_paper = {"a": 40, "b": 20} if kept_by_paper is None else kept_by_paper
    return MentionOutcome(
        case_id=case_id,
        kind="content",
        config=config,
        pairing=pairing,
        scope=scope,
        answer_paper_id=answer_paper_id,
        kept_total=sum(kept_by_paper.values()),
        kept_by_paper=kept_by_paper,
        answer_survived=answer_survived,
    )


def test_nearest_other_takes_the_first_eligible_paper_in_the_given_order():
    assert nearest_other(PAPERS, exclude="a").paper_id == "b"
    assert nearest_other(PAPERS, exclude="b").paper_id == "a"


def test_nearest_other_skips_papers_with_no_dense_row():
    only_d = [PAPERS[0], PAPERS[3]]
    assert nearest_other(only_d, exclude="a") is None


def test_nearest_other_returns_none_when_every_paper_is_excluded():
    assert nearest_other([PAPERS[0]], exclude="a") is None


def test_seeded_other_is_reproducible_for_the_same_seed():
    first = seeded_other(PAPERS, exclude="a", seed="case-1")
    second = seeded_other(PAPERS, exclude="a", seed="case-1")
    assert first == second
    assert first.paper_id != "a"


def test_seeded_other_never_returns_the_anchor_and_none_when_pool_is_empty():
    assert seeded_other([PAPERS[0]], exclude="a", seed="x") is None


def test_seeded_other_is_independent_of_input_order():
    """The pool is sorted before drawing, so a differently-ordered paper list
    (e.g. a re-run where two papers tie on distance) picks the same paper."""
    forward = seeded_other(PAPERS, exclude="a", seed="s")
    backward = seeded_other(list(reversed(PAPERS)), exclude="a", seed="s")
    assert forward.paper_id == backward.paper_id


def test_admitted_papers_gates_on_the_papers_own_best_chunk():
    bests = {"a": 0.40, "b": 0.78, "c": None}
    assert admitted_papers(bests, 0.75) == ["a"]
    assert admitted_papers(bests, 0.85) == ["a", "b"]


def test_admitted_papers_never_admits_a_paper_with_no_dense_chunk():
    assert admitted_papers({"c": None}, 2.0) == []


def test_count_in_band_is_half_open_and_ignores_papers_with_no_chunk():
    """[lo, hi): a paper AT similarity_threshold is already excluded by the
    status quo (`distance < threshold`), so it belongs in the band; one at the
    ceiling is excluded by both policies and does not."""
    values = [0.74, 0.75, 0.80, 0.85, None]
    assert count_in_band(values, 0.75, 0.85) == 2


def test_count_in_band_is_zero_when_nothing_falls_inside():
    assert count_in_band([0.1, 0.2, None], 0.75, 0.85) == 0


def test_merge_round_robin_interleaves_by_position_and_preserves_per_paper_order():
    assert merge_round_robin([["a1", "a2", "a3"], ["b1", "b2"]]) == [
        "a1",
        "b1",
        "a2",
        "b2",
        "a3",
    ]


def test_merge_round_robin_handles_empty_input_and_empty_lists():
    assert merge_round_robin([]) == []
    assert merge_round_robin([[], []]) == []
    assert merge_round_robin([[], ["b1"]]) == ["b1"]


def test_count_by_paper_counts_every_item():
    items = [("a", 1), ("b", 2), ("a", 3)]
    assert count_by_paper(items, lambda i: i[0]) == {"a": 2, "b": 1}


def test_outcome_properties_split_the_budget_between_answer_and_other_papers():
    outcome = _outcome(kept_by_paper={"a": 12, "b": 8})
    assert outcome.represented == 2
    assert outcome.answer_paper_chunks == 12
    assert outcome.second_paper_chunks == 8


def test_a_negative_outcome_counts_every_chunk_as_non_answer():
    outcome = _outcome(answer_paper_id=None, kept_by_paper={"a": 5}, answer_survived=None)
    assert outcome.answer_paper_chunks == 0
    assert outcome.second_paper_chunks == 5


def test_representation_rate_is_denominated_in_paper_slots_not_cases():
    """One case where both papers contributed and one where only one did is
    3/4, not 1/2 — a per-case boolean would hide which shape the failure had."""
    outcomes = [
        _outcome(case_id="c1", kept_by_paper={"a": 30, "b": 30}),
        _outcome(case_id="c2", kept_by_paper={"a": 60}),
    ]
    assert representation_rate(outcomes) == 0.75
    assert both_represented_rate(outcomes) == 0.5


def test_representation_rate_is_none_when_nothing_was_measured():
    assert representation_rate([]) is None
    assert both_represented_rate([]) is None
    assert mean_kept([]) is None
    assert worst_kept([]) is None


def test_answer_paper_zero_rate_counts_only_the_answering_paper_being_shut_out():
    outcomes = [
        _outcome(case_id="c1", kept_by_paper={"a": 60}),
        _outcome(case_id="c2", kept_by_paper={"b": 60}),
    ]
    assert answer_paper_zero_rate(outcomes) == 0.5


def test_answer_paper_zero_rate_is_none_for_an_all_negative_slice():
    outcomes = [_outcome(answer_paper_id=None, answer_survived=None)]
    assert answer_paper_zero_rate(outcomes) is None


def test_survival_rate_ignores_unscored_negatives():
    outcomes = [
        _outcome(case_id="c1", answer_survived=True),
        _outcome(case_id="c2", answer_survived=False),
        _outcome(case_id="n1", answer_paper_id=None, answer_survived=None),
    ]
    assert survival_rate(outcomes) == 0.5


def test_mean_second_paper_share_skips_cases_that_delivered_nothing():
    """An empty result is a representation failure, already reported; folding
    it in here as 0.0 noise would read as the looser gate being cleaner."""
    outcomes = [
        _outcome(case_id="c1", kept_by_paper={"a": 30, "b": 30}),
        _outcome(case_id="c2", kept_by_paper={}),
    ]
    assert mean_second_paper_share(outcomes) == 0.5


def test_mean_second_paper_share_is_none_when_every_case_is_empty():
    assert mean_second_paper_share([_outcome(kept_by_paper={})]) is None


def test_survival_regressions_names_cases_the_candidate_policy_loses():
    baseline = [_outcome(case_id="c1", answer_survived=True)]
    candidate = [_outcome(case_id="c1", config="policy-A", answer_survived=False)]
    assert survival_regressions(baseline, candidate) == ["c1"]


def test_survival_regressions_ignores_a_case_the_baseline_already_lost():
    baseline = [_outcome(case_id="c1", answer_survived=False)]
    candidate = [_outcome(case_id="c1", config="policy-A", answer_survived=False)]
    assert survival_regressions(baseline, candidate) == []


def test_survival_regressions_never_compares_across_pairings():
    """The seeded pairing is a different scope from the nearest one; matching
    on case id alone would report a phantom regression between the two arms."""
    baseline = [_outcome(case_id="c1", pairing="nearest", answer_survived=True)]
    candidate = [_outcome(case_id="c1", config="policy-A", pairing="seeded", answer_survived=False)]
    assert survival_regressions(baseline, candidate) == []
