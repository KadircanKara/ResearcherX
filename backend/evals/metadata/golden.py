"""Golden metadata: schema and loader.

Papers are addressed by a distinctive substring of their title, not by id: ids
are per-database, and this file has to be readable against a fresh dev DB, a
teammate's, or prod.

A malformed or unmatched case is always fatal. A silently skipped case reads as
a pass and quietly shrinks the thing being measured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class GoldenSetError(ValueError):
    """The golden set is malformed. Always fatal."""


@dataclass(frozen=True)
class MetadataCase:
    paper_title_contains: str
    authors: tuple[str, ...]
    year: int | None
    venue: str | None


def _parse_case(raw: object) -> MetadataCase:
    if not isinstance(raw, dict):
        raise GoldenSetError(f"case is not an object: {raw!r}")

    key = raw.get("paper_title_contains")
    if not isinstance(key, str) or not key.strip():
        raise GoldenSetError(f"case is missing paper_title_contains: {raw!r}")

    authors = raw.get("authors", [])
    if not isinstance(authors, list) or any(not isinstance(a, str) for a in authors):
        raise GoldenSetError(f"authors must be a list of strings: {raw!r}")

    year = raw.get("year")
    if year is not None and (isinstance(year, bool) or not isinstance(year, int)):
        raise GoldenSetError(f"year must be an integer or null: {raw!r}")

    venue = raw.get("venue")
    if venue is not None and not isinstance(venue, str):
        raise GoldenSetError(f"venue must be a string or null: {raw!r}")

    return MetadataCase(
        paper_title_contains=key.strip(),
        authors=tuple(authors),
        year=year,
        venue=venue,
    )


def load_golden_set(path: Path) -> list[MetadataCase]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenSetError(f"cannot read golden set at {path}: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise GoldenSetError(f"golden set must be a non-empty list: {path}")
    return [_parse_case(item) for item in raw]
