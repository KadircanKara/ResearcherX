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
class Case:
    id: str
    kind: str
    question: str
    paper_title_contains: str | None
    expect_substrings: tuple[str, ...]

    @property
    def is_negative(self) -> bool:
        return self.kind == "off_topic"


def _parse_case(raw: dict) -> Case:
    for field in ("id", "kind", "question"):
        if not raw.get(field):
            raise GoldenSetError(f"case is missing required field {field!r}: {raw!r}")

    case_id, kind = raw["id"], raw["kind"]
    if kind not in _KINDS:
        raise GoldenSetError(
            f"case {case_id!r}: unknown kind {kind!r}, expected one of {sorted(_KINDS)}"
        )

    title = raw.get("paper_title_contains")
    subs = tuple(raw.get("expect_substrings") or ())

    if kind == "off_topic":
        if title or subs:
            raise GoldenSetError(
                f"case {case_id!r}: off_topic cases must not set paper_title_contains "
                "or expect_substrings — they assert that nothing relevant exists"
            )
    else:
        if not title:
            raise GoldenSetError(f"case {case_id!r}: {kind} case needs paper_title_contains")
        if not subs:
            raise GoldenSetError(f"case {case_id!r}: {kind} case needs expect_substrings")

    return Case(
        id=case_id,
        kind=kind,
        question=raw["question"],
        paper_title_contains=title,
        expect_substrings=subs,
    )


def load_golden_set(path: Path) -> list[Case]:
    payload = json.loads(Path(path).read_text())
    raw_cases = payload.get("cases")
    if not raw_cases:
        raise GoldenSetError(f"{path}: no cases defined")

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
    so a single common word can't carry a case.
    """
    if case.paper_title_contains is None:
        return False
    if case.paper_title_contains.lower() not in paper_title.lower():
        return False
    text = chunk_text.lower()
    return all(sub.lower() in text for sub in case.expect_substrings)
