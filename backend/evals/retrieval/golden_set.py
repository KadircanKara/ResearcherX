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

# Kinds are named for the retriever each case is meant to exercise: a pure
# paraphrase (dense should win), a paraphrase that also carries a distinctive
# term (`paraphrase_keyword`, hybrid should win), a bare keyword query, and
# off-topic negatives.
POSITIVE_KINDS = ("paraphrase", "paraphrase_keyword", "keyword")
_KINDS = {*POSITIVE_KINDS, "off_topic"}
_SPLITS = ("tune", "test")


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
    split: str = "tune"
    keyword: str | None = None

    @property
    def is_negative(self) -> bool:
        return self.kind == "off_topic"


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

    split = raw.get("split")
    if split not in _SPLITS:
        raise GoldenSetError(
            f"case {case_id!r}: split must be one of {list(_SPLITS)}, got {split!r}"
        )

    keyword = raw.get("keyword")
    if kind == "paraphrase_keyword" or keyword is not None:
        if kind not in ("paraphrase_keyword", "keyword"):
            raise GoldenSetError(
                f"case {case_id!r}: keyword is only allowed on paraphrase_keyword "
                f"and keyword cases, not {kind!r}"
            )
        if not isinstance(keyword, str) or not keyword.strip():
            raise GoldenSetError(f"case {case_id!r}: {kind} case needs a non-empty keyword")
        keyword = keyword.strip()
        if keyword.lower() not in raw["question"].lower():
            raise GoldenSetError(
                f"case {case_id!r}: keyword {keyword!r} does not appear in the question"
            )

    title = raw.get("paper_title_contains")

    raw_subs = raw.get("expect_substrings")
    if raw_subs is not None and not isinstance(raw_subs, list):
        raise GoldenSetError(
            f"case {case_id!r}: expect_substrings must be a list, got {type(raw_subs).__name__}"
        )
    for i, sub in enumerate(raw_subs or []):
        if not isinstance(sub, str) or not sub.strip():
            raise GoldenSetError(
                f"case {case_id!r}: expect_substrings[{i}] must be a non-empty string"
            )
    # Stripped, not just validated: a trailing/leading space copied in from a
    # source PDF (e.g. "revisit time ") would otherwise silently fail to
    # match text where the phrase sits at a line or chunk boundary.
    subs = tuple(sub.strip() for sub in (raw_subs or ()))

    if kind == "off_topic":
        if title or subs:
            raise GoldenSetError(
                f"case {case_id!r}: off_topic cases must not set paper_title_contains "
                "or expect_substrings — they assert that nothing relevant exists"
            )
    else:
        if not (title or "").strip():
            raise GoldenSetError(f"case {case_id!r}: {kind} case needs paper_title_contains")
        if not subs:
            raise GoldenSetError(f"case {case_id!r}: {kind} case needs expect_substrings")

    return Case(
        id=case_id,
        kind=kind,
        question=raw["question"],
        paper_title_contains=title,
        expect_substrings=subs,
        split=split,
        keyword=keyword,
    )


def load_golden_set(path: Path, *, split: str = "all") -> list[Case]:
    if split != "all" and split not in _SPLITS:
        raise GoldenSetError(f"split must be 'all' or one of {list(_SPLITS)}, got {split!r}")
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
    if split == "all":
        return cases
    return [c for c in cases if c.split == split]


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
