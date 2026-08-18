"""Measures the LEXICAL RESOLVER, not retrieval.

`run_eval.py` measures whether the answering chunk reaches the budget once a
scope exists. `mention_eval.py` measures what a scope of user-picked papers
retrieves. Neither can see the rung above them: given a question and nothing
else, which papers does `paper_resolver.resolve_papers` name?

That rung is the one place scope is still derived from the question rather
than from a click, so it is the one place a wrong answer scopes retrieval to a
paper the user never asked for -- exactly the failure that got the LLM paper
targeter deleted (9/30 correct against 11/30 wrongly scoped). The resolver is
allowed to exist only because it never guesses: an ambiguous span, an author
covering several papers, or an over-large result all fall through to global
retrieval. THE NEGATIVE CASES ARE THEREFORE THE POINT OF THIS HARNESS. A run
that scores every positive and quietly resolves the negatives too has measured
a resolver that guesses.

Imports the production `resolve_papers` directly. A harness that reimplements
the policy it measures cannot catch the policy drifting -- that is how the
per-paper floor shipped as a no-op and stayed one through ten reviews (see
CLAUDE.md, and `mention_eval.py`'s `production` arm).

    docker compose exec -T backend python -m evals.retrieval.resolver_eval \
        --project-id <uuid>

Reads only. Never writes to the database, and makes no LLM or embedding call:
resolution is pure text matching, so a full run costs one SELECT.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.models import Paper
from app.db.session import SessionLocal
from app.services.paper_resolver import ResolvablePaper, resolve_papers

_DEFAULT_SET = Path(__file__).parent / "resolver_set.json"


class ResolverSetError(Exception):
    """The case file is malformed. Raised rather than skipped: a silently
    dropped case reads as a passing run."""


@dataclass(frozen=True)
class ResolverCase:
    id: str
    question: str
    # Title substrings the resolution must produce, order-insensitive. EMPTY
    # means the resolver must return nothing at all.
    expect_titles: tuple[str, ...]

    @property
    def is_negative(self) -> bool:
        return not self.expect_titles


def load_resolver_set(path: Path) -> list[ResolverCase]:
    payload = json.loads(Path(path).read_text())
    raw_cases = payload.get("cases")
    if not raw_cases:
        raise ResolverSetError(f"{path}: no cases defined")

    cases: list[ResolverCase] = []
    seen: set[str] = set()
    for raw in raw_cases:
        case_id = raw.get("id")
        question = raw.get("question")
        for field, value in (("id", case_id), ("question", question)):
            if not isinstance(value, str) or not value.strip():
                raise ResolverSetError(f"case is missing required field {field!r}: {raw!r}")
        if case_id in seen:
            raise ResolverSetError(f"duplicate case id {case_id!r}")
        seen.add(case_id)

        titles = raw.get("expect_titles")
        if titles is None or not isinstance(titles, list):
            raise ResolverSetError(f"case {case_id!r}: expect_titles must be a list")
        for i, title in enumerate(titles):
            if not isinstance(title, str) or not title.strip():
                raise ResolverSetError(
                    f"case {case_id!r}: expect_titles[{i}] must be a non-empty string"
                )
        # Stripped for the same reason the golden set strips: a trailing space
        # copied from a PDF silently fails to match.
        cases.append(ResolverCase(case_id, question, tuple(title.strip() for title in titles)))
    return cases


def resolution_matches(case: ResolverCase, resolved_titles: list[str]) -> bool:
    """True when the resolution is exactly what the case demands.

    Set equality, not containment: resolving the right paper PLUS a second one
    is a failure, because that second paper's chunks displace the answer's
    within a fixed budget. A negative case passes only on an empty resolution.
    """
    if case.is_negative:
        return not resolved_titles
    if len(resolved_titles) != len(case.expect_titles):
        return False
    unmatched = list(resolved_titles)
    for expected in case.expect_titles:
        hit = next((t for t in unmatched if expected.lower() in t.lower()), None)
        if hit is None:
            return False
        unmatched.remove(hit)
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the lexical paper resolver.")
    parser.add_argument("--project-id", required=True, help="project whose papers form the corpus")
    parser.add_argument("--set", type=Path, default=_DEFAULT_SET)
    args = parser.parse_args()

    cases = load_resolver_set(args.set)

    async with SessionLocal() as db:
        rows = (
            (await db.execute(select(Paper).where(Paper.project_id == args.project_id)))
            .scalars()
            .all()
        )
    if not rows:
        raise SystemExit(f"project {args.project_id} has no papers")

    papers = [
        ResolvablePaper(
            paper_id=p.id,
            title=p.title or "",
            authors=list(p.authors or []),
            year=p.year,
        )
        for p in rows
    ]
    titles_by_id = {p.paper_id: p.title for p in papers}

    print(
        f"corpus: {len(papers)} papers   project: {args.project_id}   "
        f"cap: max_resolved_papers={settings.max_resolved_papers}"
    )

    positives = [c for c in cases if not c.is_negative]
    negatives = [c for c in cases if c.is_negative]
    correct = {"positive": 0, "negative": 0}
    failures: list[str] = []

    for case in cases:
        resolved = resolve_papers(case.question, papers, max_papers=settings.max_resolved_papers)
        resolved_titles = [titles_by_id.get(pid, pid) for pid in resolved]
        ok = resolution_matches(case, resolved_titles)
        bucket = "negative" if case.is_negative else "positive"
        if ok:
            correct[bucket] += 1
        else:
            want = "(fall through)" if case.is_negative else ", ".join(case.expect_titles)
            got = ", ".join(t[:52] for t in resolved_titles) or "(fall through)"
            failures.append(f"  {case.id}\n      want: {want}\n      got : {got}")

    print(f"\nresolved correctly   : {correct['positive']}/{len(positives)}")
    print(
        f"fell through as told : {correct['negative']}/{len(negatives)}"
        "   <- the safety property: a resolver that guesses fails HERE"
    )
    if failures:
        print("\nfailures:")
        print("\n".join(failures))
    else:
        print("\nno failures.")


if __name__ == "__main__":
    asyncio.run(main())
