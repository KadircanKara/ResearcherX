"""Real two-paper COMPARISON cases: schema, loading, per-side scoring.

Why this file exists, separately from `golden_set.py`. Every case in
`golden_set.json` asks about exactly ONE paper. `mention_eval.py` measures the
multi-mention path by bolting a SYNTHETIC second mention onto those cases
(the nearest other paper, or a seeded random one), which leaves the regime
that actually motivates `@A ... @B` unmeasured: a question whose EMBEDDING is
a blend of two papers, and whose answer is split across both. Adding such
questions to `golden_set.json` was rejected outright — its 30 positives and 12
negatives are the denominator of every number recorded in `README.md`, and
editing them would silently invalidate the lot. Hence a new file with its own
loader.

Ground truth is per SIDE. A comparison case carries two title needles and two
substring lists, and each side is scored independently: "did paper A's half of
the answer survive to the budget" and "did paper B's" are different questions,
and a case where only one survived is precisely the failure this set exists to
detect. `ComparisonCase.side()` projects one side into a `golden_set.Case` so
`chunk_satisfies` / `first_satisfying_rank` apply unchanged — the scoring
predicate is shared, never reimplemented.

Substrings are copied literally out of `paper_chunk_embeddings.text` and every
one was verified against the corpus before shipping (see
`.superpowers/comparison-set-report.md` for the query output). Substrings, not
chunk ids, for the reason `golden_set.py` documents: re-indexing regenerates
every id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from evals.retrieval.golden_set import Case

# Comparison cases are all positives on both sides — there is no "off_topic"
# comparison, because a negative needs no second paper to be negative about.
# The kind exists only so `golden_set.Case` (and everything keyed off
# `case.kind`) accepts a projected side.
_SIDE_KIND = "content"


class ComparisonSetError(ValueError):
    """The comparison set is malformed. Always fatal, for the same reason
    `GoldenSetError` is: a silently skipped case reads as a retrieval failure
    and corrupts the metrics."""


@dataclass(frozen=True)
class ComparisonCase:
    """One question that genuinely needs BOTH named papers.

    `a_*` and `b_*` are symmetric — nothing in the harness treats A as primary.
    The ORDER is still stable and meaningful for reporting (A is listed first
    in the mention scope), which is why they are two fields rather than a set.
    """

    id: str
    question: str
    a_title_contains: str
    a_expect_substrings: tuple[str, ...]
    b_title_contains: str
    b_expect_substrings: tuple[str, ...]
    note: str

    def side(self, which: str) -> Case:
        """Project one side into a `golden_set.Case`.

        This is what lets `chunk_satisfies` and `first_satisfying_rank` score a
        comparison case with no new predicate: one side of a comparison IS a
        single-paper content case, and duplicating the matching logic would let
        the two copies drift apart on exactly the corpus-specific edge cases
        (case folding, whitespace) the shared one already handles.
        """
        if which == "a":
            title, subs = self.a_title_contains, self.a_expect_substrings
        elif which == "b":
            title, subs = self.b_title_contains, self.b_expect_substrings
        else:
            raise ValueError(f"side must be 'a' or 'b', got {which!r}")
        return Case(
            id=f"{self.id}:{which}",
            kind=_SIDE_KIND,
            question=self.question,
            paper_title_contains=title,
            expect_substrings=subs,
        )

    @property
    def sides(self) -> tuple[str, str]:
        return ("a", "b")


def _parse_substrings(case_id: str, field: str, raw) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ComparisonSetError(
            f"case {case_id!r}: {field} must be a list, got {type(raw).__name__}"
        )
    if not raw:
        raise ComparisonSetError(f"case {case_id!r}: {field} must not be empty")
    for i, sub in enumerate(raw):
        if not isinstance(sub, str) or not sub.strip():
            raise ComparisonSetError(f"case {case_id!r}: {field}[{i}] must be a non-empty string")
    # Stripped for the reason golden_set.py documents: a trailing space copied
    # from a PDF silently fails to match where the phrase ends a chunk.
    return tuple(sub.strip() for sub in raw)


def _parse_case(raw: dict) -> ComparisonCase:
    for field in ("id", "question", "paper_a_title_contains", "paper_b_title_contains"):
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ComparisonSetError(f"case is missing required field {field!r}: {raw!r}")

    case_id = raw["id"]
    a_title = raw["paper_a_title_contains"].strip()
    b_title = raw["paper_b_title_contains"].strip()

    # Two needles that resolve to the same paper describe a ONE-paper scope
    # wearing a comparison's clothes: every metric here would report perfect
    # representation while measuring nothing the single-scope harness doesn't
    # already cover. Compared case-insensitively because `chunk_satisfies`
    # matches that way, and by containment in either direction because
    # "Partial Replanning" and "Partial Replanning for X" name one paper.
    lower_a, lower_b = a_title.lower(), b_title.lower()
    if lower_a in lower_b or lower_b in lower_a:
        raise ComparisonSetError(
            f"case {case_id!r}: paper_a_title_contains {a_title!r} and "
            f"paper_b_title_contains {b_title!r} are not distinct — a comparison case "
            "must name two different papers"
        )

    return ComparisonCase(
        id=case_id,
        question=raw["question"],
        a_title_contains=a_title,
        a_expect_substrings=_parse_substrings(
            case_id, "expect_a_substrings", raw.get("expect_a_substrings")
        ),
        b_title_contains=b_title,
        b_expect_substrings=_parse_substrings(
            case_id, "expect_b_substrings", raw.get("expect_b_substrings")
        ),
        note=raw.get("note", ""),
    )


def load_comparison_set(path: Path) -> list[ComparisonCase]:
    payload = json.loads(Path(path).read_text())
    raw_cases = payload.get("cases")
    if not raw_cases:
        raise ComparisonSetError(f"{path}: no cases defined")
    if not isinstance(raw_cases, list):
        raise ComparisonSetError(f"{path}: 'cases' must be a list, got {type(raw_cases).__name__}")
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise ComparisonSetError(
                f"{path}: each case must be an object, got {type(raw).__name__}: {raw!r}"
            )

    cases = [_parse_case(raw) for raw in raw_cases]

    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ComparisonSetError(f"duplicate case id {case.id!r}")
        seen.add(case.id)
    return cases
