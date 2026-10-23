"""Would a relevance-score cutoff after the rerank stage help, and at what budget?

    docker compose exec -T backend python -m evals.retrieval.score_cutoff \\
        --project-id <uuid> [--scorer cohere|jev-noul|both]

Two questions production has not measured, answered on one set of calls:

1. BUDGET. Recall of the answering chunk at budgets 10/20/30/60 in the
   scorer's order. If recall@20 equals recall@60, the other 40 excerpts only
   cost tokens.
2. CUTOFF. Drop every candidate the scorer rated below a threshold before the
   budget applies. On positives that can only LOSE the answer; on off_topic
   questions it is the point -- a question the library cannot answer should
   reach the model with nothing, so the model refuses. The table pairs the
   two, so a threshold is chosen on both columns at once, never on one.

Candidates come from the harness's `_hybrid_chunks_for` (production's fused
order). Cohere scores the first RERANK_CANDIDATES, as `rerank_items` would;
Jev noul scores EVERY candidate, as arm E's fusion stage does. A candidate a
scorer never saw has no score and is dropped by any cutoff above zero.

The cutoff is NOT a production policy. Nothing here is imported by `app`;
the pure table logic is `cutoff_rows` / `budget_recall`, unit-tested.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.db.session import SessionLocal
from app.local_rag.rerank import CohereReranker
from app.services.embedding_service import EmbeddingService
from app.services.jev_reranker import JevReranker
from evals.retrieval.golden_set import Case, chunk_satisfies, load_golden_set
from evals.retrieval.run_eval import _DEFAULT_SET, _hybrid_chunks_for

BUDGETS = (10, 20, 30, 60)
THRESHOLDS = {
    "cohere": (0.0, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5),
    "jev-noul": (0.0, 0.1, 0.3, 0.5, 0.7, 0.9),
}


@dataclass(frozen=True)
class ScoredCase:
    """One question, its candidates in the scorer's order, with scores.

    `scores[i]` is None for a candidate the scorer never saw. `hits[i]` is
    whether candidate i satisfies the case (always False for off_topic)."""

    case_id: str
    negative: bool
    scores: tuple[float | None, ...]
    hits: tuple[bool, ...]


def _kept(sc: ScoredCase, threshold: float, budget: int) -> list[int]:
    """Indices that survive the cutoff, then the budget. Threshold 0 keeps
    everything, scored or not -- it is the no-cutoff control."""
    if threshold <= 0:
        keep = list(range(len(sc.scores)))
    else:
        keep = [i for i, s in enumerate(sc.scores) if s is not None and s >= threshold]
    return keep[:budget]


def budget_recall(cases: Sequence[ScoredCase], budget: int) -> float:
    positives = [c for c in cases if not c.negative]
    if not positives:
        return 0.0
    found = sum(any(c.hits[i] for i in _kept(c, 0.0, budget)) for c in positives)
    return found / len(positives)


def cutoff_rows(
    cases: Sequence[ScoredCase], thresholds: Sequence[float], budget: int
) -> list[dict]:
    """One row per threshold: answer recall and kept-excerpt count on
    positives; share left EMPTY and mean kept on off_topic negatives."""
    positives = [c for c in cases if not c.negative]
    negatives = [c for c in cases if c.negative]
    rows = []
    for t in thresholds:
        kept_pos = [_kept(c, t, budget) for c in positives]
        kept_neg = [_kept(c, t, budget) for c in negatives]
        rows.append(
            {
                "threshold": t,
                "recall": (
                    sum(any(c.hits[i] for i in k) for c, k in zip(positives, kept_pos))
                    / len(positives)
                    if positives
                    else 0.0
                ),
                "pos_kept": sum(map(len, kept_pos)) / len(positives) if positives else 0.0,
                "neg_empty": (sum(not k for k in kept_neg) / len(negatives) if negatives else 0.0),
                "neg_kept": sum(map(len, kept_neg)) / len(negatives) if negatives else 0.0,
            }
        )
    return rows


async def _score(scorer, question: str, texts: list[str], head: int) -> list[float | None]:
    """Scores for texts[:head] by index, None past the head or on no-opinion."""
    head = min(head, len(texts))
    results = await scorer.rerank(question, texts[:head], top_n=head) if head else []
    scores: list[float | None] = [None] * len(texts)
    for r in results or []:
        scores[r.index] = r.relevance_score
    return scores


def _order(scores: list[float | None]) -> list[int]:
    """Scorer order: scored first by score (stable on ties, which keep the
    fused order), then the unscored tail in fused order -- `rerank_items`'s
    head/tail shape."""
    scored = [i for i, s in enumerate(scores) if s is not None]
    unscored = [i for i, s in enumerate(scores) if s is None]
    return sorted(scored, key=lambda i: -scores[i]) + unscored


def _scored_case(case: Case, chunks, scores: list[float | None]) -> ScoredCase:
    order = _order(scores)
    return ScoredCase(
        case_id=case.id,
        negative=case.is_negative,
        scores=tuple(scores[i] for i in order),
        hits=tuple(
            chunk_satisfies(case, chunks[i].paper_title, chunks[i].chunk_text) for i in order
        ),
    )


def _print_scorer(name: str, cases: list[ScoredCase], budget: int) -> None:
    n_pos = sum(not c.negative for c in cases)
    n_neg = len(cases) - n_pos
    print(f"\n== {name}   ({n_pos} positives, {n_neg} off_topic)")
    print(
        "  budget recall (no cutoff): "
        + "   ".join(f"@{b} {budget_recall(cases, b):.2f}" for b in BUDGETS)
    )
    print(f"  cutoff at budget {budget}:")
    print(f"  {'threshold':>9} {'recall':>7} {'pos_kept':>9} {'neg_empty':>10} {'neg_kept':>9}")
    for r in cutoff_rows(cases, THRESHOLDS[name], budget):
        print(
            f"  {r['threshold']:>9.2f} {r['recall']:>7.2f} {r['pos_kept']:>9.1f} "
            f"{r['neg_empty']:>10.2f} {r['neg_kept']:>9.1f}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Rerank-score cutoff and budget sweep.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--set", type=Path, default=_DEFAULT_SET)
    parser.add_argument("--scorer", choices=("cohere", "jev-noul", "both"), default="both")
    parser.add_argument("--budget", type=int, default=settings.max_context_chunks)
    args = parser.parse_args()

    names = ["cohere", "jev-noul"] if args.scorer == "both" else [args.scorer]
    scorers = {}
    if "cohere" in names:
        if not settings.cohere_api_key:
            raise SystemExit("cohere needs COHERE_API_KEY")
        scorers["cohere"] = (
            CohereReranker(api_key=settings.cohere_api_key, model=settings.cohere_rerank_model),
            settings.rerank_candidates,
        )
    if "jev-noul" in names:
        if not settings.decisions_api_key:
            raise SystemExit("jev-noul needs DECISIONS_API_KEY")
        scorers["jev-noul"] = (
            JevReranker(
                api_key=settings.decisions_api_key,
                model=settings.jev_model,
                base_url=settings.decisions_base_url,
                question="noul",
                concurrency=settings.jev_concurrency,
                timeout_s=settings.jev_timeout_s,
                deadline_s=settings.jev_deadline_s,
            ),
            None,  # every candidate, as arm E's fusion stage
        )

    cases = load_golden_set(args.set)
    svc = EmbeddingService()
    out: dict[str, list[ScoredCase]] = {n: [] for n in scorers}
    no_opinion = {n: 0 for n in scorers}
    async with SessionLocal() as db:
        for i, case in enumerate(cases, 1):
            chunks = await _hybrid_chunks_for(
                db, svc, case, args.project_id, threshold=settings.similarity_threshold
            )
            texts = [c.chunk_text for c in chunks]
            for name, (scorer, head) in scorers.items():
                scores = await _score(scorer, case.question, texts, head or len(texts))
                if texts and all(s is None for s in scores):
                    no_opinion[name] += 1
                out[name].append(_scored_case(case, chunks, scores))
            print(f"  [{i}/{len(cases)}] {case.id} candidates={len(chunks)}", flush=True)

    print(
        f"\nrerank_candidates={settings.rerank_candidates}  budget={args.budget}  "
        f"similarity_threshold={settings.similarity_threshold}"
    )
    for name, scored in out.items():
        _print_scorer(name, scored, args.budget)
        if no_opinion[name]:
            print(f"  WARNING: {no_opinion[name]} case(s) got no scores (failed open)")


if __name__ == "__main__":
    asyncio.run(main())
