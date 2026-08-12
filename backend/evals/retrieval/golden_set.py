"""Golden set for the retrieval eval harness: schema, loading, scoring predicate.

Ground truth is expressed as substrings rather than chunk ids on purpose:
`index_chunks` deletes and reinserts every row for a paper on re-index, so ids
are regenerated each time — id-based ground truth would rot on the first
re-embed, which is exactly when this harness is most needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_KINDS = {"content", "metadata", "figure", "off_topic"}


class GoldenSetError(ValueError):
    """The golden set is malformed. Always fatal — a silently skipped case
    reads as a retrieval failure and corrupts the metrics."""


@dataclass(frozen=True)
class PaperExpectation:
    """One paper a case expects to contribute, and what it must contribute."""

    title_contains: str
    expect_substrings: tuple[str, ...]


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    question: str
    expect_papers: tuple[PaperExpectation, ...]

    @property
    def is_negative(self) -> bool:
        return self.kind == "off_topic"

    @property
    def paper_title_contains(self) -> str | None:
        """The first expected paper's needle, or None for off_topic.

        Kept so every single-paper consumer (chunk_satisfies, _scope_to_paper,
        _targeted_case_status, metrics.py) needs no change: a scalar case
        parses into exactly one expectation, so this is that expectation.
        """
        return self.expect_papers[0].title_contains if self.expect_papers else None

    @property
    def expect_substrings(self) -> tuple[str, ...]:
        return self.expect_papers[0].expect_substrings if self.expect_papers else ()


def _parse_case(raw: dict) -> Case:
    for field in ("id", "kind", "question"):
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise GoldenSetError(f"case is missing required field {field!r}: {raw!r}")

    case_id, kind = raw["id"], raw["kind"]
    if kind not in _KINDS:
        raise GoldenSetError(
            f"case {case_id!r}: unknown kind {kind!r}, expected one of {sorted(_KINDS)}"
        )

    title = raw.get("paper_title_contains")
    raw_papers = raw.get("expect_papers")

    if raw_papers is not None and (title or raw.get("expect_substrings")):
        raise GoldenSetError(
            f"case {case_id!r}: sets both expect_papers and the scalar "
            "paper_title_contains/expect_substrings — pick one form"
        )

    if kind == "off_topic":
        if title or raw.get("expect_substrings") or raw_papers:
            raise GoldenSetError(
                f"case {case_id!r}: off_topic cases must not set paper_title_contains, "
                "expect_substrings or expect_papers — they assert that nothing "
                "relevant exists"
            )
        return Case(id=case_id, kind=kind, question=raw["question"], expect_papers=())

    if raw_papers is None:
        # Scalar form: exactly one expectation. Every existing case is this.
        raw_papers = [{"title_contains": title, "expect_substrings": raw.get("expect_substrings")}]
    if not isinstance(raw_papers, list) or not raw_papers:
        raise GoldenSetError(f"case {case_id!r}: expect_papers must be a non-empty list")

    expectations = tuple(
        _parse_expectation(case_id, i, entry) for i, entry in enumerate(raw_papers)
    )
    return Case(id=case_id, kind=kind, question=raw["question"], expect_papers=expectations)


def _parse_expectation(case_id: str, index: int, raw: object) -> PaperExpectation:
    """One entry of expect_papers. Same rules the scalar form always had, just
    applied per paper: a needle AND at least one substring, every substring a
    non-empty string, each stripped."""
    if not isinstance(raw, dict):
        raise GoldenSetError(
            f"case {case_id!r}: expect_papers[{index}] must be an object, got {type(raw).__name__}"
        )
    needle = raw.get("title_contains")
    if not isinstance(needle, str) or not needle.strip():
        raise GoldenSetError(
            f"case {case_id!r}: expect_papers[{index}] needs a non-empty title_contains"
        )
    subs = raw.get("expect_substrings")
    if not isinstance(subs, list) or not subs:
        raise GoldenSetError(
            f"case {case_id!r}: expect_papers[{index}] needs a non-empty expect_substrings"
        )
    for i, sub in enumerate(subs):
        if not isinstance(sub, str) or not sub.strip():
            raise GoldenSetError(
                f"case {case_id!r}: expect_papers[{index}].expect_substrings[{i}] "
                "must be a non-empty string"
            )
    # Stripped, not just validated: a trailing space copied in from a source
    # PDF would otherwise silently fail to match text where the phrase sits at
    # a line or chunk boundary.
    return PaperExpectation(
        title_contains=needle.strip(),
        expect_substrings=tuple(sub.strip() for sub in subs),
    )


def load_golden_set(path: Path) -> list[Case]:
    payload = json.loads(Path(path).read_text())
    raw_cases = payload.get("cases")
    if not raw_cases:
        raise GoldenSetError(f"{path}: no cases defined")
    if not isinstance(raw_cases, list):
        raise GoldenSetError(f"{path}: 'cases' must be a list, got {type(raw_cases).__name__}")
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise GoldenSetError(
                f"{path}: each case must be an object, got {type(raw).__name__}: {raw!r}"
            )

    cases = [_parse_case(raw) for raw in raw_cases]

    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise GoldenSetError(f"duplicate case id {case.id!r}")
        seen.add(case.id)
    return cases


def chunk_satisfies(case: Case, paper_title: str, chunk_text: str) -> bool:
    """True when this chunk is a correct hit for the case.

    Requires the expected paper AND every expected substring — all, not any,
    so a single common word can't carry a case. Always returns False for
    off_topic cases (they have no paper_title_contains to match) — this is
    intentional, not an oversight: it lets the runner call this uniformly
    across all kinds without branching on `is_negative`.
    """
    if case.paper_title_contains is None:
        return False
    if case.paper_title_contains.lower() not in paper_title.lower():
        return False
    text = chunk_text.lower()
    return all(sub.lower() in text for sub in case.expect_substrings)
