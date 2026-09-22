"""Judge-vs-judge agreement scoring. Pure — no model calls, no DB."""

import pytest

from evals.groundedness.agreement import pair_verdicts, verdict_shift


def pairs_from(reference, candidate):
    return pair_verdicts({"c": reference}, {"c": candidate})


def test_a_missed_problem_is_the_number_that_decides_it():
    """The candidate calling a hallucination 'supported' hides the one failure
    this harness exists to find. A false alarm only costs reading."""
    agreement = pairs_from(
        {0: "unsupported", 1: "supported"},
        {0: "supported", 1: "unsupported"},
    )
    assert [p.index for p in agreement.false_clean] == [0]
    assert [p.index for p in agreement.false_alarm] == [1]


def test_contradicted_and_unsupported_are_the_same_side_of_the_line():
    """Both are 'this claim is a problem'. A candidate that swaps one for the
    other disagrees on detail, not on shippability."""
    agreement = pairs_from({0: "unsupported"}, {0: "contradicted"})
    assert agreement.raw == 0.0
    assert agreement.binary == 1.0
    assert agreement.false_clean == ()


def test_kappa_exposes_a_judge_that_stopped_discriminating():
    """These verdicts are heavily skewed toward supported. A candidate that
    answers 'supported' to everything scores high RAW agreement and must not
    look acceptable — kappa is what catches it."""
    reference = {i: "supported" for i in range(19)} | {19: "unsupported"}
    candidate = {i: "supported" for i in range(20)}
    agreement = pairs_from(reference, candidate)
    assert agreement.raw == 0.95
    assert agreement.kappa == pytest.approx(0.0)
    assert len(agreement.false_clean) == 1


def test_kappa_is_high_when_the_candidate_tracks_the_reference_on_a_mixed_set():
    reference = {i: "supported" for i in range(10)} | {i: "unsupported" for i in range(10, 20)}
    agreement = pairs_from(reference, dict(reference))
    assert agreement.raw == 1.0
    assert agreement.kappa == pytest.approx(1.0)


def test_kappa_needs_at_least_two_claims():
    assert pairs_from({0: "supported"}, {0: "supported"}).kappa is None


def test_claims_only_one_judge_scored_are_dropped_not_defaulted():
    """A missing verdict is a failed call. Counting it as agreement would make
    an unreliable judge look better the more often it fell over."""
    agreement = pairs_from({0: "supported", 1: "unsupported"}, {0: "supported"})
    assert agreement.n == 1


def test_verdict_shift_names_the_dangerous_direction():
    looser = pairs_from({0: "unsupported", 1: "unsupported"}, {0: "supported", 1: "supported"})
    assert "LOOSER" in verdict_shift(looser.pairs)
    stricter = pairs_from({0: "supported"}, {0: "unsupported"})
    assert "stricter" in verdict_shift(stricter.pairs)
    same = pairs_from({0: "supported"}, {0: "supported"})
    assert verdict_shift(same.pairs) == "identical on every shippability call"
