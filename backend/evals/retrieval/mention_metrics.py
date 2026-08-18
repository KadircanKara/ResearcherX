"""Pure scoring and pair construction for the MULTI-MENTION measurement.

Everything here is a function of already-fetched chunks, so it is unit-testable
without Postgres or an embedding provider — same split `metrics.py` uses for
the single-scope harness.

Nothing in this module decides retrieval POLICY. The floor is
`app.services.mention_ranker.apply_per_paper_floor`, imported by
`mention_eval.py` rather than reproduced, and the per-paper cuts are
`app.services.intra_paper_ranker` / `app.services.hybrid_ranker`. The only
policy-shaped function here is `merge_round_robin`, which exists because
policy B is a design that never shipped and therefore has no production
function to import — see its docstring.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class ScopedOutcome(Protocol):
    """What the scope-shaped aggregates below actually need.

    A Protocol rather than a base class because two unrelated result types
    satisfy it: `MentionOutcome` (one answering paper plus a synthetic second
    mention) and `ComparisonOutcome` (two answering papers, a real comparison
    question). Representation and budget size mean exactly the same thing for
    both, and forking `mean_kept` into two copies would let the synthetic arms'
    numbers and the real arm's stop being comparable — which is the entire
    reason the synthetic arms are kept.
    """

    @property
    def scope(self) -> tuple[str, ...]: ...

    @property
    def kept_total(self) -> int: ...

    @property
    def represented(self) -> int: ...


@dataclass(frozen=True)
class PaperBest:
    """A paper in the project and its closest chunk to one question.

    `best` is None when the paper contributed no dense row at all — only
    possible if a paper has no embeddings for the configured model, since the
    pairing query applies no distance cutoff.
    """

    paper_id: str
    title: str
    best: float | None


def nearest_other(papers: Sequence[PaperBest], exclude: str) -> PaperBest | None:
    """The paper OTHER than `exclude` whose nearest chunk is closest to the
    question — the hardest, most realistic second mention ("compare these two",
    where the second paper is the one the question already drags in).

    `papers` must be ordered nearest-first with a deterministic tie-break (the
    pairing SQL orders by `best ASC, paper_id ASC`); this walks that order and
    takes the first eligible row, so the choice is reproducible across runs
    without any RNG. Papers with no dense row (`best is None`) are skipped —
    they cannot be "nearest" to anything.
    """
    for paper in papers:
        if paper.paper_id != exclude and paper.best is not None:
            return paper
    return None


def seeded_other(papers: Sequence[PaperBest], exclude: str, seed: str) -> PaperBest | None:
    """A second mention drawn at random from the project, seeded on the case id.

    The CONTRAST arm to `nearest_other`: a paper the question has no reason to
    touch, which is what a "@A ... also @B" turn looks like when the two papers
    are genuinely unrelated. Seeded on the case id and drawn from a sorted list
    so the pair is identical on every re-run of the harness — a random second
    paper would make each configuration's numbers incomparable to the last run's.
    """
    pool = sorted((p for p in papers if p.paper_id != exclude), key=lambda p: p.paper_id)
    if not pool:
        return None
    return random.Random(seed).choice(pool)


def admitted_papers(bests: dict[str, float | None], threshold: float) -> list[str]:
    """Policy B's admission gate: the papers whose OWN nearest chunk clears
    `threshold`, in the order given.

    A paper with no chunk at all (`None`) is never admitted. Returns ids, not
    chunks: admission is a decision about the PAPER, which is the whole
    difference between policy B and a flat distance cut over the merged pool.
    """
    return [pid for pid, best in bests.items() if best is not None and best < threshold]


def count_in_band(values: Iterable[float | None], lo: float, hi: float) -> int:
    """How many values fall in [lo, hi).

    The band that decides this whole measurement is
    [similarity_threshold, intra_paper_ceiling) = [0.75, 0.85): a mentioned
    paper whose nearest chunk lands there contributes everything when named
    ALONE and nothing when named alongside a second paper. A paper outside the
    band is treated identically by the status quo and by policy A, so the
    count of papers inside it is the upper bound on how often the two policies
    can differ at all. `None` (a paper with no chunk) never counts.
    """
    return sum(1 for v in values if v is not None and lo <= v < hi)


def merge_round_robin(lists: Sequence[Sequence[T]]) -> list[T]:
    """Interleave per-paper rankings by position: every list's 1st item (in
    list order), then every list's 2nd, and so on.

    Policy B queries each mentioned paper SEPARATELY, so each paper's list is
    ranked against itself — under hybrid retrieval those are fused RRF ranks
    computed over that paper's own dense/sparse pools, which are not
    comparable across papers (RRF scores depend on the pool a rank was drawn
    from). Sorting the union by cosine distance would also be wrong: it throws
    away the fused order that is the point of measuring the shipped hybrid
    policy, and sparse-only admissions have no distance at all.

    Position-interleaving is the neutral merge that preserves each paper's own
    order exactly. It is a HARNESS choice, not a production policy — policy B
    never shipped, so there is no merge function to import. Say so wherever
    these numbers are reported.
    """
    merged: list[T] = []
    if not lists:
        return merged
    for rank in range(max((len(items) for items in lists), default=0)):
        for items in lists:
            if rank < len(items):
                merged.append(items[rank])
    return merged


@dataclass(frozen=True)
class MentionOutcome:
    """One (case, configuration) result, after the final budget is applied."""

    case_id: str
    kind: str
    config: str
    pairing: str
    scope: tuple[str, ...]
    answer_paper_id: str | None
    kept_total: int
    kept_by_paper: dict[str, int]
    # None for off_topic cases: a negative has no satisfying chunk by
    # definition, exactly as `metrics`/`run_eval` treat their `survived`.
    answer_survived: bool | None

    @property
    def represented(self) -> int:
        return sum(1 for pid in self.scope if self.kept_by_paper.get(pid, 0) > 0)

    @property
    def answer_paper_chunks(self) -> int:
        if self.answer_paper_id is None:
            return 0
        return self.kept_by_paper.get(self.answer_paper_id, 0)

    @property
    def second_paper_chunks(self) -> int:
        """Budget taken by mentioned papers that are NOT the answering one.

        For a negative there is no answering paper, so every kept chunk counts:
        the whole point of the off_topic rows is how much of the budget a
        two-paper mention burns on a question the library cannot answer.
        """
        return self.kept_total - self.answer_paper_chunks


@dataclass(frozen=True)
class ComparisonOutcome:
    """One (real comparison case, configuration) result.

    The difference from `MentionOutcome` is not cosmetic: a comparison question
    has TWO answering papers, so "did the answer survive" is two independent
    questions and a case where only one side survived is the exact failure this
    arm exists to detect. Collapsing that into one boolean — the shape
    `MentionOutcome` uses — would report a half-answered comparison as a
    success.
    """

    case_id: str
    config: str
    scope: tuple[str, ...]
    kept_total: int
    kept_by_paper: dict[str, int]
    # side key ("a"/"b") -> the paper it names, whether its expected text
    # reached the budget, and at what 1-based rank (None = never).
    side_paper: dict[str, str]
    side_survived: dict[str, bool]
    side_rank: dict[str, int | None]

    @property
    def represented(self) -> int:
        return sum(1 for pid in self.scope if self.kept_by_paper.get(pid, 0) > 0)

    @property
    def both_survived(self) -> bool:
        return bool(self.side_survived) and all(self.side_survived.values())

    @property
    def minority_share(self) -> float | None:
        """Share of the delivered budget held by the LESS represented named
        paper. 0.0 means one named paper was shut out entirely, 0.5 means the
        budget split evenly. None when nothing was delivered.

        This is the comparison-arm analogue of `mean_second_paper_share`, but
        it is a BALANCE measure rather than a noise measure: on a real
        comparison both papers are supposed to be in context, so a small share
        is a defect here and merely a cost there.
        """
        if self.kept_total <= 0:
            return None
        return min(self.kept_by_paper.get(pid, 0) for pid in self.scope) / self.kept_total


def both_sides_survived_rate(outcomes: Sequence[ComparisonOutcome]) -> float | None:
    """Fraction of comparison cases where BOTH papers' answering text reached
    the budget — the headline metric of this arm."""
    if not outcomes:
        return None
    return sum(1 for o in outcomes if o.both_survived) / len(outcomes)


def side_survival_rate(outcomes: Sequence[ComparisonOutcome]) -> float | None:
    """Fraction of SIDES (2 per case) whose answering text reached the budget.

    Denominated in sides for the same reason `representation_rate` is
    denominated in paper-slots: it separates "one half of one comparison was
    lost" from "both halves of one comparison were lost".
    """
    sides = sum(len(o.side_survived) for o in outcomes)
    if not sides:
        return None
    return sum(sum(1 for ok in o.side_survived.values() if ok) for o in outcomes) / sides


def shut_out_rate(outcomes: Sequence[ComparisonOutcome]) -> float | None:
    """Fraction of cases where at least one NAMED paper contributed zero
    chunks. The user typed both `@` mentions; a zero here is the instruction
    being visibly ignored, regardless of whether the answer survived."""
    if not outcomes:
        return None
    return sum(1 for o in outcomes if o.represented < len(o.scope)) / len(outcomes)


def mean_minority_share(outcomes: Sequence[ComparisonOutcome]) -> float | None:
    """Mean `minority_share`, averaged over cases that delivered anything.

    Cases delivering nothing are skipped rather than counted as 0.0, matching
    `mean_second_paper_share`: an empty result is a representation failure and
    is already reported by `shut_out_rate`.
    """
    scored = [o.minority_share for o in outcomes if o.minority_share is not None]
    if not scored:
        return None
    return sum(scored) / len(scored)


def comparison_survival_regressions(
    baseline: Sequence[ComparisonOutcome], candidate: Sequence[ComparisonOutcome]
) -> list[str]:
    """`case_id:side` labels that survived under `baseline` and do not under
    `candidate` — per SIDE, because losing one half of a comparison is the
    regression that a per-case boolean would hide."""
    base = {(o.case_id, side): ok for o in baseline for side, ok in o.side_survived.items()}
    lost: list[str] = []
    for outcome in candidate:
        for side, ok in outcome.side_survived.items():
            if base.get((outcome.case_id, side)) is True and ok is False:
                lost.append(f"{outcome.case_id}:{side}")
    return lost


def count_by_paper(items: Iterable[T], paper_of: Callable[[T], str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = paper_of(item)
        counts[key] = counts.get(key, 0) + 1
    return counts


def representation_rate(outcomes: Sequence[ScopedOutcome]) -> float | None:
    """Fraction of MENTIONED papers contributing at least one chunk.

    Denominated in paper-slots, not cases: with a floor of 5 and two mentions,
    "one of the two papers vanished" is the failure, and a per-case boolean
    hides whether it was one paper or both. None when nothing was measured.
    """
    slots = sum(len(o.scope) for o in outcomes)
    if not slots:
        return None
    return sum(o.represented for o in outcomes) / slots


def both_represented_rate(outcomes: Sequence[ScopedOutcome]) -> float | None:
    """Fraction of cases where EVERY mentioned paper contributed a chunk."""
    if not outcomes:
        return None
    return sum(1 for o in outcomes if o.represented == len(o.scope)) / len(outcomes)


def answer_paper_zero_rate(outcomes: Sequence[MentionOutcome]) -> float | None:
    """Fraction of cases where the paper that HOLDS the answer contributed
    nothing — the harmful shape of a representation failure. None when no
    outcome names an answer paper (an all-negative slice)."""
    scored = [o for o in outcomes if o.answer_paper_id is not None]
    if not scored:
        return None
    return sum(1 for o in scored if o.answer_paper_chunks == 0) / len(scored)


def survival_rate(outcomes: Sequence[MentionOutcome]) -> float | None:
    """Fraction of scored cases whose answering chunk reached the final budget."""
    scored = [o for o in outcomes if o.answer_survived is not None]
    if not scored:
        return None
    return sum(1 for o in scored if o.answer_survived) / len(scored)


def mean_second_paper_share(outcomes: Sequence[MentionOutcome]) -> float | None:
    """Mean fraction of the delivered budget taken by the non-answering
    mentioned paper — the noise cost of a looser gate.

    Averaged over cases, not pooled over chunks, so one huge case cannot
    dominate. Cases that delivered nothing are skipped rather than counted as
    0.0 noise: an empty result is a representation failure, already reported
    above, and folding it in here would read as "less noise".
    """
    scored = [o for o in outcomes if o.kept_total > 0]
    if not scored:
        return None
    return sum(o.second_paper_chunks / o.kept_total for o in scored) / len(scored)


def mean_kept(outcomes: Sequence[ScopedOutcome]) -> float | None:
    if not outcomes:
        return None
    return sum(o.kept_total for o in outcomes) / len(outcomes)


def worst_kept(outcomes: Sequence[ScopedOutcome]) -> int | None:
    return max((o.kept_total for o in outcomes), default=None)


def survival_regressions(
    baseline: Sequence[MentionOutcome], candidate: Sequence[MentionOutcome]
) -> list[str]:
    """Case ids whose answering chunk survived under `baseline` and does NOT
    under `candidate` — the finding that kills a candidate policy outright.

    Matched on (case_id, pairing) so the two arms of the pairing contrast can
    never be compared against each other. Only cases present and scored in both
    slices are considered.
    """
    base = {(o.case_id, o.pairing): o.answer_survived for o in baseline}
    lost: list[str] = []
    for outcome in candidate:
        key = (outcome.case_id, outcome.pairing)
        if base.get(key) is True and outcome.answer_survived is False:
            lost.append(outcome.case_id)
    return lost
