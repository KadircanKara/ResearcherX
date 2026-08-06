"""Metadata extraction accuracy against the live database. Measures, never mutates.

Run:  docker compose exec -T backend python -m evals.metadata.run_eval

The `-m` form is required: pyproject.toml packages only `app*`, so `evals` is
not installed and file-path invocation drops cwd from sys.path.

Exit code is 1 when any field is `wrong` or `hallucinated`, so this can be used
as a gate. `missed` alone exits 0 — a field the extractor left empty is a
weaker failure than one it filled in wrongly.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from app.db.models import Paper
from app.db.session import SessionLocal
from evals.metadata.compare import Verdict, compare_authors, compare_scalar
from evals.metadata.golden import GoldenSetError, MetadataCase, load_golden_set

_DEFAULT_SET = Path(__file__).parent / "golden_set.json"
_FIELDS = ("authors", "year", "venue")


def _match(case: MetadataCase, papers: list[Paper]) -> Paper:
    hits = [p for p in papers if case.paper_title_contains.lower() in (p.title or "").lower()]
    if not hits:
        raise GoldenSetError(
            f"no paper title contains {case.paper_title_contains!r} — "
            "the golden set names a paper this database does not have"
        )
    if len(hits) > 1:
        raise GoldenSetError(
            f"{case.paper_title_contains!r} matches {len(hits)} papers; "
            "make the substring more distinctive"
        )
    return hits[0]


def _verdicts(case: MetadataCase, paper: Paper) -> dict[str, Verdict]:
    return {
        "authors": compare_authors(list(case.authors), list(paper.authors or [])),
        "year": compare_scalar(case.year, paper.year),
        "venue": compare_scalar(case.venue, paper.venue),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", type=Path, default=_DEFAULT_SET, help="golden set JSON path")
    args = parser.parse_args()

    cases = load_golden_set(args.set)

    async with SessionLocal() as db:
        papers = list((await db.execute(select(Paper))).scalars().all())

    counts: dict[Verdict, int] = {
        "correct": 0,
        "wrong": 0,
        "missed": 0,
        "hallucinated": 0,
    }

    print(f"{len(cases)} cases against {len(papers)} papers\n")
    header = f"{'paper':<42} {'source':<9} " + " ".join(f"{f:<13}" for f in _FIELDS)
    print(header)
    print("-" * len(header))

    for case in cases:
        paper = _match(case, papers)
        verdicts = _verdicts(case, paper)
        for verdict in verdicts.values():
            counts[verdict] += 1
        cells = " ".join(f"{verdicts[f]:<13}" for f in _FIELDS)
        print(f"{(paper.title or '')[:41]:<42} {paper.metadata_source:<9} {cells}")

    total = sum(counts.values())
    print(
        f"\n{counts['correct']}/{total} correct  "
        f"{counts['wrong']} wrong  {counts['missed']} missed  "
        f"{counts['hallucinated']} HALLUCINATED"
    )

    if counts["hallucinated"]:
        print(
            "\nA hallucinated field is a value invented for something the paper "
            "does not state. Tighten _META_SYSTEM in title_extraction_service.py."
        )
    if counts["missed"]:
        print(
            "\nA missed field was present in the paper and not extracted. "
            "Check whether it falls outside the first 3000 characters."
        )

    return 1 if counts["wrong"] or counts["hallucinated"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
