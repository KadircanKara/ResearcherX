"""Answer relevance, context precision and the judge-independence guard.

Pure -- no model calls, no DB. The judge methods that PRODUCE these verdicts
are tested in `test_evals_groundedness_relevance_judge.py` with the transport
faked.
"""

import csv
import io

import pytest

from evals.groundedness.metrics import CaseOutcome, ScoredClaim
from evals.groundedness.relevance import (
    CSV_COLUMNS,
    RelevanceOutcome,
    SelfJudgeError,
    check_judge_independence,
    context_precision,
    csv_row,
    mean_answer_relevance,
    parse_metrics,
    write_csv,
)


def relevance(case_id="c", kind="content", answer=0.8, shown=(1, 2, 3, 4), relevant=(1, 3)):
    return RelevanceOutcome(
        case_id=case_id,
        kind=kind,
        answer_relevance=answer,
        answer_relevance_reason="",
        shown=shown,
        relevant=frozenset(relevant),
    )


# -- context precision -------------------------------------------------------


def test_context_precision_is_relevant_over_shown_pooled_across_cases():
    """Pooled, not a mean of per-case ratios: a 3-chunk catalog and a 60-chunk
    catalog are not equally weighted evidence about retrieval noise."""
    outcomes = [
        relevance(shown=(1, 2, 3, 4), relevant=(1, 3)),
        relevance(shown=(1, 2), relevant=(1, 2)),
    ]
    assert context_precision(outcomes) == pytest.approx(4 / 6)


def test_precision_at_k_reads_the_catalog_in_the_order_the_model_did():
    """`shown` is CATALOG order, and its numbers are post-renumbering citation
    numbers, which are not ranks: excerpt [7] can stand first. Cutting by
    number instead of by position would score a different catalog."""
    outcome = relevance(shown=(7, 1, 9, 2), relevant=(7, 2))
    assert context_precision([outcome], k=2) == pytest.approx(1 / 2)
    assert context_precision([outcome], k=1) == pytest.approx(1.0)


def test_a_catalog_shorter_than_k_is_divided_by_what_was_shown():
    """Dividing by k would punish a well-gated two-chunk catalog for not
    padding itself out with noise."""
    outcome = relevance(shown=(1, 2), relevant=(1,))
    assert context_precision([outcome], k=10) == pytest.approx(1 / 2)


def test_an_empty_catalog_is_no_measurement_not_zero():
    assert context_precision([relevance(shown=(), relevant=())]) is None
    assert context_precision([]) is None


def test_a_case_whose_context_was_not_judged_is_left_out_of_the_denominator():
    judged = relevance(shown=(1, 2), relevant=(1,))
    unjudged = RelevanceOutcome(
        case_id="x",
        kind="content",
        answer_relevance=0.5,
        answer_relevance_reason="",
        shown=(1, 2, 3),
        relevant=None,
    )
    assert context_precision([judged, unjudged]) == pytest.approx(1 / 2)


# -- answer relevance --------------------------------------------------------


def test_mean_answer_relevance_skips_cases_that_were_not_scored():
    """off_topic cases carry None -- the right answer there is a refusal, which
    a relevance judge scores low. Averaging that in would make a system that
    correctly refuses look worse than one that invents an answer."""
    outcomes = [relevance(answer=1.0), relevance(answer=0.5), relevance(answer=None)]
    assert mean_answer_relevance(outcomes) == pytest.approx(0.75)


def test_mean_answer_relevance_of_nothing_is_none():
    assert mean_answer_relevance([relevance(answer=None)]) is None


# -- the judge must not be the answerer --------------------------------------


def test_the_same_model_judging_itself_is_refused():
    with pytest.raises(SelfJudgeError, match="opus"):
        check_judge_independence(judge_model="opus", answering_model="opus", allow_self_judge=False)


def test_a_model_name_is_compared_without_case_or_whitespace():
    with pytest.raises(SelfJudgeError):
        check_judge_independence(
            judge_model=" Opus ", answering_model="opus", allow_self_judge=False
        )


def test_self_judging_can_be_forced_and_says_so():
    note = check_judge_independence(
        judge_model="opus", answering_model="opus", allow_self_judge=True
    )
    assert "SELF-JUDGED" in note


def test_two_claude_models_run_but_carry_a_same_family_caveat():
    """sonnet grading opus is allowed -- it is the configuration this harness
    is run in through the CLI proxy -- but self-preference is measured within a
    model family too, so the report has to say which kind of run it was."""
    note = check_judge_independence(
        judge_model="sonnet", answering_model="opus", allow_self_judge=False
    )
    assert "same model family" in note
    note = check_judge_independence(
        judge_model="claude-sonnet-5", answering_model="claude-opus-5", allow_self_judge=False
    )
    assert "same model family" in note


def test_a_judge_from_another_vendor_needs_no_caveat_but_a_sibling_model_does():
    assert (
        check_judge_independence(
            judge_model="gpt-4.1", answering_model="opus", allow_self_judge=False
        )
        == ""
    )
    note = check_judge_independence(
        judge_model="gpt-4.1", answering_model="gpt-4.1-mini", allow_self_judge=False
    )
    assert "same model family" in note


# -- --metrics ---------------------------------------------------------------


def test_metrics_default_to_support_alone_so_old_runs_stay_comparable():
    assert parse_metrics(None) == frozenset({"support"})


def test_metrics_are_parsed_from_a_comma_list():
    assert parse_metrics("support, context_relevance") == frozenset(
        {"support", "context_relevance"}
    )


def test_support_is_always_measured_even_when_only_an_add_on_is_named():
    assert parse_metrics("context_relevance") == frozenset({"support", "context_relevance"})


def test_an_unknown_metric_is_refused_by_name():
    with pytest.raises(ValueError, match="faithfulness"):
        parse_metrics("support,faithfulness")


# -- csv ---------------------------------------------------------------------


def _outcome():
    claim = ScoredClaim(
        index=0,
        verdict="supported",
        markers=(1,),
        supporting_excerpts=(1,),
        supporting_papers=frozenset({"p1"}),
        marker_papers=frozenset({"p1"}),
        disclosed=False,
    )
    bad = ScoredClaim(
        index=1,
        verdict="unsupported",
        markers=(),
        supporting_excerpts=(),
        supporting_papers=frozenset(),
        marker_papers=frozenset(),
        disclosed=False,
    )
    return CaseOutcome(
        case_id="c",
        kind="content",
        claims=(claim, bad),
        stance="answered",
        evidence_present=True,
        marker_total=1,
    )


def test_a_csv_row_carries_the_question_answer_contexts_and_every_score():
    row = csv_row(
        outcome=_outcome(),
        relevance=relevance(shown=(1, 2, 3, 4), relevant=(1, 3)),
        question="What is optimised?",
        answer="Revisit time [1].",
        contexts=["[1] Joint Optimization: revisit time"],
        judge_model="sonnet",
        answering_model="opus",
    )
    assert set(row) == set(CSV_COLUMNS)
    assert row["question"] == "What is optimised?"
    assert row["claims"] == 2
    assert row["supported"] == 1
    assert row["unsupported"] == 1
    assert row["groundedness"] == pytest.approx(0.5)
    assert row["answer_relevance"] == pytest.approx(0.8)
    assert row["context_precision"] == pytest.approx(0.5)
    assert row["contexts"] == '["[1] Joint Optimization: revisit time"]'


def test_an_unmeasured_score_is_an_empty_cell_never_a_zero():
    """A zero in a score column is a measurement. A metric that was not
    requested, or that errored, must not be readable as the worst score."""
    row = csv_row(
        outcome=_outcome(),
        relevance=None,
        question="q",
        answer="a",
        contexts=[],
        judge_model="sonnet",
        answering_model="opus",
    )
    assert row["answer_relevance"] == ""
    assert row["context_precision"] == ""
    assert row["context_precision_at_10"] == ""


def test_the_csv_survives_newlines_and_commas_in_an_answer():
    row = csv_row(
        outcome=_outcome(),
        relevance=None,
        question="q",
        answer='line one,\nline "two"',
        contexts=[],
        judge_model="sonnet",
        answering_model="opus",
    )
    buffer = io.StringIO()
    write_csv(buffer, [row])
    buffer.seek(0)
    parsed = list(csv.DictReader(buffer))
    assert len(parsed) == 1
    assert parsed[0]["answer"] == 'line one,\nline "two"'
