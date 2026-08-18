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
from typing import TypeVar

T = TypeVar("T")


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


def count_by_paper(items: Iterable[T], paper_of: Callable[[T], str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = paper_of(item)
        counts[key] = counts.get(key, 0) + 1
    return counts


def representation_rate(outcomes: Sequence[MentionOutcome]) -> float | None:
    """Fraction of MENTIONED papers contributing at least one chunk.

    Denominated in paper-slots, not cases: with a floor of 5 and two mentions,
    "one of the two papers vanished" is the failure, and a per-case boolean
    hides whether it was one paper or both. None when nothing was measured.
    """
    slots = sum(len(o.scope) for o in outcomes)
    if not slots:
        return None
    return sum(o.represented for o in outcomes) / slots


def both_represented_rate(outcomes: Sequence[MentionOutcome]) -> float | None:
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


def mean_kept(outcomes: Sequence[MentionOutcome]) -> float | None:
    if not outcomes:
        return None
    return sum(o.kept_total for o in outcomes) / len(outcomes)


def worst_kept(outcomes: Sequence[MentionOutcome]) -> int | None:
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
