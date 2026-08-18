"""Measures paper SCOPING, not chunk retrieval.

`run_eval.py` answers "did the answering chunk reach the budget". This answers
the question one layer above it: **was the right paper even offered to the
targeter, and what did the targeter then do with the list?** A wrong answer
here is invisible to run_eval — retrieval scoped to a confidently wrong paper
retrieves plenty of chunks, all from the wrong paper.

Imports production's own `ChatService._shortlist_papers`, so it can never
measure a shortlist policy production does not run.

    docker compose exec -T backend python -m evals.retrieval.shortlist_eval \
        --project-id <uuid>
    docker compose exec -T backend python -m evals.retrieval.shortlist_eval \
        --project-id <uuid> --no-llm      # candidate recall only, no LLM calls

Two case sets are measured, and the split is the point:

- **golden** (`golden_set.json` positives) — content-worded questions. Most
  name no paper at all, so the CORRECT targeter outcome for them is `empty`
  (unscoped global retrieval), not `correct`.
- **scope** (`scope_set.json`) — title-referential questions ("the paper that
  compares evolutionary algorithms against reinforcement learning"). These
  DO identify one paper, and the dense arm structurally cannot rank them: it
  ranks on body text, and the identifying words are in the title.

Read `WRONG` as the harm metric. A missing candidate is only damaging if the
targeter then names a paper that does not hold the answer; abstaining is safe.
"""

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.agents.paper_targeter import PaperTargeterAgent, TargeterInput
from app.core.config import settings
from app.db.models import Paper
from app.db.session import SessionLocal
from app.services.chat_service import ChatService, PaperInfo

_HERE = Path(__file__).parent
_GOLDEN_SET = _HERE / "golden_set.json"
_SCOPE_SET = _HERE / "scope_set.json"


@dataclass(frozen=True)
class ScopeCase:
    id: str
    question: str
    paper_title_contains: str


def _load_cases(golden: Path, scope: Path) -> dict[str, list[ScopeCase]]:
    """Positives from both sets, keyed by set name.

    off_topic cases are excluded: they have no expected paper, so neither
    candidate recall nor targeter correctness is defined for them.
    """
    golden_cases = [
        ScopeCase(c["id"], c["question"], c["paper_title_contains"])
        for c in json.loads(golden.read_text())["cases"]
        if c["kind"] != "off_topic" and c.get("paper_title_contains")
    ]
    scope_cases = [
        ScopeCase(c["id"], c["question"], c["paper_title_contains"])
        for c in json.loads(scope.read_text())["cases"]
    ]
    return {"golden": golden_cases, "scope": scope_cases}


def _resolve(case: ScopeCase, titles: dict[str, str]) -> str | None:
    """The one paper whose title contains the case's marker, or None.

    Ambiguity is never resolved by guessing: two matches means the marker no
    longer identifies a paper on this corpus and the case must be reported as
    unusable rather than scored against an arbitrary one of them.
    """
    hits = [
        pid for pid, title in titles.items() if case.paper_title_contains.lower() in title.lower()
    ]
    return hits[0] if len(hits) == 1 else None


async def main() -> None:
    parser = argparse.ArgumentParser(description="Measure paper scoping (shortlist + targeter).")
    parser.add_argument("--project-id", required=True, help="project whose papers form the corpus")
    parser.add_argument("--set", type=Path, default=_GOLDEN_SET)
    parser.add_argument("--scope-set", type=Path, default=_SCOPE_SET)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="skip the targeter calls and report candidate recall only",
    )
    args = parser.parse_args()

    svc = ChatService()
    agent = PaperTargeterAgent()

    async with SessionLocal() as db:
        rows = (
            (await db.execute(select(Paper).where(Paper.project_id == args.project_id)))
            .scalars()
            .all()
        )
    if not rows:
        raise SystemExit(f"project {args.project_id} has no papers")
    titles = {p.id: p.title for p in rows}
    infos = [PaperInfo(paper_id=p.id, title=p.title) for p in rows]

    print(
        f"corpus: {len(infos)} papers | dense cap {settings.targeter_dense_candidates}"
        f" + lexical cap {settings.targeter_lexical_candidates}"
    )

    for label, cases in _load_cases(args.set, args.scope_set).items():
        offered = correct = empty = wrong = 0
        unusable: list[str] = []
        misses: list[str] = []
        wrongs: list[str] = []

        for case in cases:
            want = _resolve(case, titles)
            if want is None:
                unusable.append(case.id)
                continue

            embedding = await svc._embedding_svc.embed(case.question, task_type="RETRIEVAL_QUERY")
            async with SessionLocal() as db:
                candidates, _ = await svc._shortlist_papers(db, infos, embedding, case.question)

            if any(c.paper_id == want for c in candidates):
                offered += 1
            else:
                misses.append(case.id)

            if args.no_llm:
                continue
            picked = await agent.run(
                TargeterInput(
                    query=case.question,
                    candidates=[{"paper_id": c.paper_id, "title": c.title} for c in candidates],
                    prior_messages=[],
                )
            )
            if picked == want:
                correct += 1
            elif not picked:
                empty += 1
            else:
                wrong += 1
                wrongs.append(case.id)

        n = len(cases) - len(unusable)
        print(f"\n== {label} (n={n})")
        print(
            f"  candidate recall : {offered}/{n}"
            + (f"  missed: {', '.join(misses)}" if misses else "")
        )
        if not args.no_llm:
            print(f"  targeter correct : {correct}/{n}")
            print(f"  targeter empty   : {empty}/{n}   (unscoped — safe)")
            print(
                f"  targeter WRONG   : {wrong}/{n}" + (f"   {', '.join(wrongs)}" if wrongs else "")
            )
        if unusable:
            print(f"  UNUSABLE (title marker matched 0 or >1 papers): {', '.join(unusable)}")


if __name__ == "__main__":
    asyncio.run(main())
