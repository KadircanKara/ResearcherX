"""Groundedness scoring. Pure — no DB, no LLM."""

import pytest

from evals.groundedness.metrics import (
    CaseOutcome,
    ScoredClaim,
    abstention_rate,
    disclosed_claim_rate,
    undisclosed_hallucination_rate,
    citation_coverage,
    citation_precision,
    claim_support_rate,
    clean_answer_rate,
    contradiction_rate,
    hallucinated_claim_rate,
    regressions,
    split_by_evidence,
    unjudged_markers,
)


def claim(
    verdict="supported",
    *,
    markers=(),
    supporting=("paper-a",),
    marker_papers=(),
    index=0,
):
    return ScoredClaim(
        index=index,
        verdict=verdict,
        markers=tuple(markers),
        supporting_excerpts=tuple(range(len(supporting))),
        supporting_papers=frozenset(supporting),
        marker_papers=frozenset(marker_papers),
    )


def case(*claims, case_id="c", kind="content", stance="answered", evidence=True, marker_total=None):
    total = marker_total if marker_total is not None else sum(len(c.markers) for c in claims)
    return CaseOutcome(
        case_id=case_id,
        kind=kind,
        claims=tuple(claims),
        stance=stance,
        evidence_present=evidence,
        marker_total=total,
    )


def test_no_claim_sentences_leave_every_denominator():
    """Headings and refusals are not assertions; counting them as supported
    would inflate groundedness by however much prose the model wrote."""
    outcome = case(claim("supported"), claim("no_claim", index=1), claim("no_claim", index=2))
    assert claim_support_rate([outcome]) == 1.0
    assert len(outcome.checkable) == 1


def test_support_rate_pools_claims_not_cases():
    """A fifteen-claim answer and a two-claim answer are not equal evidence."""
    many = case(*[claim("unsupported", index=i) for i in range(9)], case_id="many")
    one = case(claim("supported"), case_id="one")
    assert claim_support_rate([many, one]) == pytest.approx(0.1)


def test_contradiction_is_reported_apart_from_unsupported():
    outcomes = [case(claim("contradicted"), claim("unsupported", index=1))]
    assert contradiction_rate(outcomes) == 0.5


def test_a_marker_on_an_unsupported_claim_is_never_correct():
    """There is no support for it to point at, and the marker's whole job is
    telling the reader the claim came from that paper."""
    outcome = case(claim("unsupported", markers=(1,), marker_papers=("paper-a",)))
    assert citation_precision([outcome]) == 0.0


def test_a_marker_pointing_at_a_paper_that_does_not_support_the_claim_is_wrong():
    """The shape `strip_misattributed_citations` structurally cannot see: the
    prose names nobody, so the strip leaves the marker alone."""
    outcome = case(
        claim("supported", markers=(2,), supporting=("paper-a",), marker_papers=("paper-b",))
    )
    assert citation_precision([outcome]) == 0.0


def test_a_marker_pointing_at_a_supporting_paper_is_correct():
    outcome = case(
        claim("supported", markers=(1,), supporting=("paper-a",), marker_papers=("paper-a",))
    )
    assert citation_precision([outcome]) == 1.0


def test_coverage_counts_supported_claims_that_carry_any_marker():
    """An uncited supported claim is not a hallucination, but the reader
    cannot tell it apart from one."""
    outcomes = [
        case(
            claim("supported", markers=(1,), marker_papers=("paper-a",)),
            claim("supported", index=1),
        )
    ]
    assert citation_coverage(outcomes) == 0.5


def test_unjudged_markers_surfaces_markers_the_harness_could_not_score():
    outcome = case(claim("supported", markers=(1,), marker_papers=("paper-a",)), marker_total=3)
    assert unjudged_markers([outcome]) == 2


def test_clean_answer_rate_is_per_case_not_per_claim():
    dirty = case(claim("supported"), claim("unsupported", index=1), case_id="dirty")
    clean = case(claim("supported"), case_id="clean")
    assert clean_answer_rate([dirty, clean]) == 0.5


def test_a_case_with_no_checkable_claim_is_clean():
    """Correct for a refusal, which asserts nothing — `abstention_rate` is what
    separates 'refused' from 'answered and said nothing wrong'."""
    refusal = case(claim("no_claim"), stance="refused", kind="off_topic", evidence=None)
    assert refusal.is_clean
    assert abstention_rate([refusal]) == 1.0


def test_evidence_split_separates_retrieval_failures_from_generation_failures():
    reached = case(claim("supported"), case_id="a", evidence=True)
    missed = case(claim("unsupported"), case_id="b", evidence=False)
    negative = case(claim("no_claim"), case_id="c", kind="off_topic", evidence=None)
    with_evidence, without = split_by_evidence([reached, missed, negative])
    assert [o.case_id for o in with_evidence] == ["a"]
    assert [o.case_id for o in without] == ["b"]


def test_regressions_are_reported_per_case_not_per_rate():
    """Two runs can hold the same support rate while a different set of
    answers is broken."""
    before = {
        "a": case(claim("supported"), case_id="a"),
        "b": case(claim("unsupported"), case_id="b"),
    }
    after = {
        "a": case(claim("unsupported"), case_id="a"),
        "b": case(claim("supported"), case_id="b"),
    }
    assert regressions(before, after) == ["a"]


def test_disclosed_claims_leave_the_hallucination_rate_but_stay_visible():
    """A split, never an exemption: production's prompt sanctions the ungrounded
    text, so it is not a hallucination — but it is still ungrounded text on a
    reader's screen, and `disclosed_claim_rate` is what keeps it reported."""
    outcome = case(
        claim("supported"),
        claim("unsupported", index=1, markers=(), supporting=()),
        ScoredClaim(
            index=2,
            verdict="unsupported",
            markers=(),
            supporting_excerpts=(),
            supporting_papers=frozenset(),
            marker_papers=frozenset(),
            disclosed=True,
        ),
    )
    assert hallucinated_claim_rate([outcome]) == pytest.approx(2 / 3)
    assert undisclosed_hallucination_rate([outcome]) == 0.5
    assert disclosed_claim_rate([outcome]) == pytest.approx(1 / 3)


def test_an_answer_that_only_hallucinates_under_a_disclaimer_is_clean_undisclosed():
    """The pair of flags is what separates 'the prompt told it to' from 'it made
    something up'."""
    outcome = case(
        ScoredClaim(
            index=0,
            verdict="unsupported",
            markers=(),
            supporting_excerpts=(),
            supporting_papers=frozenset(),
            marker_papers=frozenset(),
            disclosed=True,
        )
    )
    assert not outcome.is_clean
    assert outcome.is_clean_undisclosed
