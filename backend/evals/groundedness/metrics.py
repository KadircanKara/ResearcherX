"""Pure scoring for the groundedness harness.

Everything here is a function of already-collected verdicts, so the policy is
unit-testable without a model or a database -- the same split
`evals/retrieval/metrics.py` uses.

CITATION CORRECTNESS IS DERIVED, NEVER JUDGED. The judge is asked one question
(does this excerpt set support this claim) and names the excerpts that carry
it. Whether the marker the model actually wrote points at one of those papers
is then arithmetic. Two reasons this is not a second judge question: a model
asked to grade provenance and attribution in one pass lets the easy half
contaminate the hard half, and a derived answer can be recomputed from a
stored verdict when the policy changes, where a judged one would need the
whole run repeated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

# Verdicts that assert something checkable. `no_claim` is excluded from every
# denominator below: headings, transitions and refusals are not claims, and
# counting them as supported would inflate groundedness by exactly as much
# prose as the model happened to write.
_CHECKABLE = ("supported", "unsupported", "contradicted")


@dataclass(frozen=True)
class ScoredClaim:
    index: int
    verdict: str
    markers: tuple[int, ...]
    supporting_excerpts: tuple[int, ...]
    # Papers the judge's supporting excerpts belong to, and papers this
    # claim's own markers point at. Both resolved by the caller, which owns
    # the excerpt catalog.
    supporting_papers: frozenset[str]
    marker_papers: frozenset[str]
    # Stands after the prompt-sanctioned hand-off to general knowledge (see
    # `claims._HANDOFF_MARKERS`). Ungrounded by design, so it is counted apart
    # from hallucination rather than with it -- and never dropped, because it
    # is still ungrounded text on a reader's screen.
    disclosed: bool = False

    @property
    def is_checkable(self) -> bool:
        return self.verdict in _CHECKABLE

    @property
    def is_grounded(self) -> bool:
        return self.verdict == "supported"

    @property
    def correct_markers(self) -> int:
        """Markers standing behind this claim that point at a paper the judge
        found support in.

        A marker on an UNSUPPORTED or CONTRADICTED claim is never correct,
        whatever it points at: there is no support for it to point to, and the
        marker's whole function is to tell the reader the claim came from that
        paper. This is the same failure `strip_misattributed_citations` fixes
        deterministically for the case where the prose names a different
        paper; here it is caught for the case the strip structurally cannot
        see -- prose that names nobody and cites a paper that does not say it.
        """
        if not self.is_grounded:
            return 0
        return sum(1 for _ in self.markers if self.marker_papers & self.supporting_papers)


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    kind: str
    claims: tuple[ScoredClaim, ...]
    stance: str
    # Did the text the golden set expects actually reach the model? Positives
    # only. Without this split, a retrieval miss is scored as a generation
    # failure and the harness blames the wrong subsystem -- the single most
    # important column in the report.
    evidence_present: bool | None
    marker_total: int

    @property
    def checkable(self) -> tuple[ScoredClaim, ...]:
        return tuple(c for c in self.claims if c.is_checkable)

    @property
    def undisclosed(self) -> tuple[ScoredClaim, ...]:
        return tuple(c for c in self.checkable if not c.disclosed)

    @property
    def is_clean(self) -> bool:
        """No unsupported and no contradicted claim.

        A case with zero checkable claims is clean by this definition. That is
        correct for a negative (a refusal asserts nothing) and is why
        `abstention_rate` exists separately for positives -- a positive that
        refused is clean AND useless, and only the two numbers together say so.
        """
        return all(c.is_grounded for c in self.checkable)

    @property
    def is_clean_undisclosed(self) -> bool:
        """No unsupported or contradicted claim OUTSIDE a hand-off.

        The per-case counterpart of `undisclosed_hallucination_rate`: an
        off_topic answer that declined and then answered from general knowledge
        is clean by this measure and dirty by `is_clean`, and the pair is what
        separates "the prompt told it to" from "it made something up".
        """
        return all(c.is_grounded for c in self.undisclosed)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def claim_support_rate(outcomes: Sequence[CaseOutcome]) -> float | None:
    """Supported / checkable, pooled over every claim in every case.

    Denominated in CLAIMS, not cases: one long answer with fifteen claims and
    one short answer with two are not equally strong evidence, and a per-case
    mean would weight them as if they were.
    """
    claims = [c for o in outcomes for c in o.checkable]
    return _rate(sum(1 for c in claims if c.is_grounded), len(claims))


def hallucinated_claim_rate(outcomes: Sequence[CaseOutcome]) -> float | None:
    """(unsupported + contradicted) / checkable. The complement of
    `claim_support_rate`, reported separately because it is the number that
    decides whether the system is shippable."""
    claims = [c for o in outcomes for c in o.checkable]
    return _rate(sum(1 for c in claims if not c.is_grounded), len(claims))


def undisclosed_hallucination_rate(outcomes: Sequence[CaseOutcome]) -> float | None:
    """(unsupported + contradicted) claims NOT covered by the hand-off, over
    checkable claims that are likewise not covered by it.

    THE REAL HALLUCINATION NUMBER. Production's own system prompt tells the
    model to decline and then answer from general knowledge when the corpus
    does not cover a question, so on the off_topic negatives most unsupported
    claims are the prompt working exactly as written. Pooling the two reports a
    design decision as a defect; this rate excludes it, and
    `disclosed_claim_rate` reports how much was excluded so nothing is hidden.
    """
    claims = [c for o in outcomes for c in o.checkable if not c.disclosed]
    return _rate(sum(1 for c in claims if not c.is_grounded), len(claims))


def disclosed_claim_rate(outcomes: Sequence[CaseOutcome]) -> float | None:
    """Share of checkable claims standing after a hand-off to general knowledge.

    Read as EXPOSURE, not as an error rate: these are sentences the reader sees
    presented as an answer, carrying no citation and no support, after a single
    disclaiming sentence they may not connect to what follows.
    """
    claims = [c for o in outcomes for c in o.checkable]
    return _rate(sum(1 for c in claims if c.disclosed), len(claims))


def contradiction_rate(outcomes: Sequence[CaseOutcome]) -> float | None:
    """Claims the excerpts actively contradict, as a share of checkable claims.

    Split out of the hallucination rate because the two are different product
    failures: an unsupported claim is the model reaching past its evidence, a
    contradicted one is the model misreading evidence it was given, and only
    the second is fixable by a better prompt.
    """
    claims = [c for o in outcomes for c in o.checkable]
    return _rate(sum(1 for c in claims if c.verdict == "contradicted"), len(claims))


def clean_answer_rate(outcomes: Sequence[CaseOutcome]) -> float | None:
    """Fraction of cases with no unsupported or contradicted claim at all.

    The user-facing number: a reader does not experience a 0.94 claim rate,
    they experience an answer that either does or does not contain something
    the papers never said.
    """
    return _rate(sum(1 for o in outcomes if o.is_clean), len(outcomes))


def citation_precision(outcomes: Sequence[CaseOutcome]) -> float | None:
    """Correct markers / markers standing behind a checkable claim.

    The denominator is deliberately not `marker_total`: a marker inside a
    sentence too short to be a claim (see `claims._MIN_CLAIM_CHARS`) has no
    claim to be right about. `unjudged_markers` reports that gap rather than
    letting it vanish.
    """
    claims = [c for o in outcomes for c in o.checkable]
    total = sum(len(c.markers) for c in claims)
    return _rate(sum(c.correct_markers for c in claims), total)


def unjudged_markers(outcomes: Sequence[CaseOutcome]) -> int:
    """Markers the harness showed the reader but could not score."""
    judged = sum(len(c.markers) for o in outcomes for c in o.checkable)
    return sum(o.marker_total for o in outcomes) - judged


def citation_coverage(outcomes: Sequence[CaseOutcome]) -> float | None:
    """Supported claims carrying at least one marker, over all supported claims.

    A supported claim with no marker is not a hallucination -- but the reader
    cannot tell it apart from one, which is the whole reason this product
    shows chips.
    """
    grounded = [c for o in outcomes for c in o.checkable if c.is_grounded]
    return _rate(sum(1 for c in grounded if c.markers), len(grounded))


def abstention_rate(outcomes: Sequence[CaseOutcome]) -> float | None:
    """Fraction of cases where the assistant refused.

    Read it in opposite directions per kind, which is why the runner reports it
    per kind and never pooled: on `off_topic` negatives a refusal is the
    correct behaviour and 1.00 is the target, on positives a refusal is a
    failure to answer a question the corpus does answer.
    """
    return _rate(sum(1 for o in outcomes if o.stance == "refused"), len(outcomes))


def split_by_evidence(
    outcomes: Sequence[CaseOutcome],
) -> tuple[list[CaseOutcome], list[CaseOutcome]]:
    """(evidence reached the model, evidence did not) over positives.

    THE MOST IMPORTANT SPLIT IN THIS HARNESS. `evals/retrieval` already
    measured that 4 of 30 positives have no satisfying chunk inside the budget.
    Scoring those answers as generation failures would blame the generator for
    a retrieval miss and send the next fix to the wrong subsystem. Pooled
    numbers are still printed, but every conclusion is drawn from the left
    half.
    """
    with_evidence = [o for o in outcomes if o.evidence_present is True]
    without = [o for o in outcomes if o.evidence_present is False]
    return with_evidence, without


def regressions(
    baseline: Mapping[str, CaseOutcome], candidate: Mapping[str, CaseOutcome]
) -> list[str]:
    """Case ids clean in `baseline` and not clean in `candidate`.

    Per case, not per rate: two runs can hold the same support rate while a
    different set of answers is broken, and only a case-level diff shows it.
    """
    return sorted(
        case_id
        for case_id, before in baseline.items()
        if before.is_clean and case_id in candidate and not candidate[case_id].is_clean
    )
