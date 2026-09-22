"""Judge-vs-judge agreement: can a cheaper model do this job?

The judge is ~82% of a groundedness run's cost (measured 2026-08-22: $2.30 of
$2.82 on gpt-4.1, against $0.46 on gpt-4.1-mini and $0.11 on gpt-4.1-nano),
so "is mini good enough" is the highest-value question this harness can be
asked about itself. It is an EMPIRICAL question, and this module is how it gets
answered -- never by picking the model that produces the nicer-looking number.

Pure (no model calls, no DB): it scores two verdict sets that
`run_eval --compare-judge` already collected, so the policy is unit-testable.

WHY RAW AGREEMENT IS NOT ENOUGH. Groundedness verdicts are heavily skewed --
the measured support rate is near 1.00, so two judges that both said
"supported" to everything would agree ~95% of the time while being worth
nothing. Cohen's kappa corrects for exactly that: it asks how much better than
chance the agreement is, given each judge's own marginal habits. High raw
agreement with a near-zero kappa is the signature of a judge that has stopped
discriminating, which is the specific failure a cheap model is expected to
have.

DISAGREEMENT IS DIRECTIONAL, and the direction decides shippability. A cheap
judge that calls a hallucination "supported" hides the failure this harness
exists to find; one that calls a supported claim "unsupported" only costs false
alarms an engineer will notice. `false_clean` counts the first kind and is the
number to read first.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

# The distinction that matters for shippability; everything else is detail.
_BAD = ("unsupported", "contradicted")


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


@dataclass(frozen=True)
class ClaimPair:
    """One claim, as graded by the reference judge and the candidate judge."""

    case_id: str
    index: int
    reference: str
    candidate: str

    @property
    def agrees(self) -> bool:
        return self.reference == self.candidate

    @property
    def agrees_on_shippability(self) -> bool:
        """Both judges put the claim on the same side of the only line that
        changes a decision: is this claim a problem or not."""
        return (self.reference in _BAD) == (self.candidate in _BAD)


@dataclass(frozen=True)
class Agreement:
    pairs: tuple[ClaimPair, ...]

    @property
    def n(self) -> int:
        return len(self.pairs)

    @property
    def raw(self) -> float | None:
        return _rate(sum(1 for p in self.pairs if p.agrees), self.n)

    @property
    def binary(self) -> float | None:
        """Agreement on problem/not-a-problem, collapsing the four verdicts."""
        return _rate(sum(1 for p in self.pairs if p.agrees_on_shippability), self.n)

    @property
    def false_clean(self) -> tuple[ClaimPair, ...]:
        """The reference found a problem and the candidate did not.

        THE NUMBER THAT DECIDES IT. Every one of these is a hallucination the
        cheaper judge would have let through, which is the one thing this
        harness cannot be allowed to miss.
        """
        return tuple(p for p in self.pairs if p.reference in _BAD and p.candidate not in _BAD)

    @property
    def false_alarm(self) -> tuple[ClaimPair, ...]:
        """The candidate found a problem the reference did not. Costs an
        engineer some reading, never a shipped hallucination."""
        return tuple(p for p in self.pairs if p.candidate in _BAD and p.reference not in _BAD)

    @property
    def kappa(self) -> float | None:
        """Cohen's kappa over the four verdict labels.

        None for fewer than two pairs. Perfect agreement on a set carrying a
        single label is 1.0 by convention -- and is also the degenerate case,
        which is why `n` and the confusion matrix print beside it and it is
        never read alone.
        """
        if self.n < 2:
            return None
        observed = self.raw or 0.0
        ref = Counter(p.reference for p in self.pairs)
        cand = Counter(p.candidate for p in self.pairs)
        expected = sum(ref[label] * cand[label] for label in set(ref) | set(cand)) / (self.n**2)
        if expected == 1.0:
            return 1.0 if observed == 1.0 else 0.0
        return (observed - expected) / (1 - expected)

    @property
    def confusion(self) -> dict[tuple[str, str], int]:
        return dict(Counter((p.reference, p.candidate) for p in self.pairs))


def pair_verdicts(
    reference: Mapping[str, Mapping[int, str]],
    candidate: Mapping[str, Mapping[int, str]],
) -> Agreement:
    """Align two judges' verdicts by (case, claim index).

    Claims only one judge scored are DROPPED, not defaulted: a missing verdict
    is a failed call, and scoring it as agreement would make an unreliable
    judge look better the more often it fell over.
    """
    pairs = tuple(
        ClaimPair(
            case_id=case_id,
            index=index,
            reference=verdict,
            candidate=candidate[case_id][index],
        )
        for case_id, verdicts in sorted(reference.items())
        if case_id in candidate
        for index, verdict in sorted(verdicts.items())
        if index in candidate[case_id]
    )
    return Agreement(pairs=pairs)


def verdict_shift(pairs: Sequence[ClaimPair]) -> str:
    """One-line human summary of which way the candidate leans."""
    stricter = sum(1 for p in pairs if p.candidate in _BAD and p.reference not in _BAD)
    looser = sum(1 for p in pairs if p.reference in _BAD and p.candidate not in _BAD)
    if stricter == looser == 0:
        return "identical on every shippability call"
    if looser > stricter:
        return f"LOOSER than the reference ({looser} problems missed, {stricter} invented)"
    return f"stricter than the reference ({stricter} extra problems, {looser} missed)"
