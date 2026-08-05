"""Retrieval eval harness — measures, never mutates.

Run:  docker compose exec -T backend python -m retrieval_eval.run_eval

The `-m` form is required: `pyproject.toml` packages only `app*`, so `retrieval_eval` is
not installed and file-path invocation drops cwd from sys.path.

One query per case pulls EVERY chunk with its cosine distance; all scoring
happens in retrieval_eval.metrics, so the threshold sweep costs no extra queries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.embedding_service import EmbeddingService
from retrieval_eval.golden_set import Case, load_golden_set
from retrieval_eval.metrics import (
    THRESHOLDS,
    Scored,
    best_satisfying_distance,
    first_satisfying_rank,
    mean_reciprocal_rank,
    noise_floor,
    recall_at_k,
    separating_threshold,
    simulate_retrieval,
    sweep,
)

_DEFAULT_SET = Path(__file__).parent / "golden_set.json"

# Mirrors chat_service._retrieve_paper_chunks: same <=> operator, same model
# filter. Deliberately NO distance cutoff and no LIMIT — the sweep needs the
# full ranking, and the corpus is small enough that this is one cheap query.
_SQL = text("""
    SELECT c.paper_id AS paper_id,
           p.title AS paper_title,
           c.text AS chunk_text,
           (c.embedding <=> CAST(:qvec AS vector)) AS distance
    FROM paper_chunk_embeddings c
    JOIN papers p ON p.id = c.paper_id
    WHERE c.model = :model
    ORDER BY distance ASC
""")


def _vec(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


async def _chunks_for(db, svc: EmbeddingService, case: Case) -> list[Scored]:
    embedding = await svc.embed(case.question, task_type="RETRIEVAL_QUERY")
    rows = (
        await db.execute(_SQL, {"qvec": _vec(embedding), "model": settings.embedding_model})
    ).fetchall()
    return [
        Scored(
            paper_id=r.paper_id,
            paper_title=r.paper_title,
            chunk_text=r.chunk_text,
            distance=float(r.distance),
        )
        for r in rows
    ]


def _corpus_note(chunks: list[Scored]) -> str:
    # Grouped by paper_id, not title: production groups retrieval per
    # paper_id (WHERE paper_id = :paper_id), and two papers could in
    # principle share a title, which would understate the paper count.
    papers = len({c.paper_id for c in chunks})
    return f"{len(chunks)} chunks across {papers} papers"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Measure retrieval quality.")
    parser.add_argument("--k", type=int, default=5, help="chunks per paper (default: 5)")
    parser.add_argument("--set", type=Path, default=_DEFAULT_SET)
    parser.add_argument("--json", type=Path, default=None, help="also dump results here")
    args = parser.parse_args()

    cases = load_golden_set(args.set)
    svc = EmbeddingService()

    positives: list[tuple[Case, list[Scored]]] = []
    negatives: list[list[Scored]] = []
    errors: list[str] = []
    per_case: list[dict] = []

    async with SessionLocal() as db:
        corpus_note = ""
        for case in cases:
            chunks = await _chunks_for(db, svc, case)
            if not corpus_note:
                corpus_note = _corpus_note(chunks)

            if case.is_negative:
                negatives.append(chunks)
                best = min((c.distance for c in chunks), default=None)
                per_case.append({"id": case.id, "kind": case.kind, "best_distance": best})
                continue

            # Corpus drift is an error, not a retrieval failure — distinguish
            # "the paper is gone" from "retrieval got worse".
            if not any(case.paper_title_contains.lower() in c.paper_title.lower() for c in chunks):
                errors.append(f"{case.id}: no paper matching {case.paper_title_contains!r}")
                continue

            positives.append((case, chunks))
            rank = first_satisfying_rank(case, simulate_retrieval(chunks, args.k))
            per_case.append(
                {
                    "id": case.id,
                    "kind": case.kind,
                    "rank": rank,
                    "best_distance": best_satisfying_distance(case, chunks),
                }
            )

    print(f"\ncorpus: {corpus_note}   model: {settings.embedding_model}   k={args.k}")
    print("(small, thematically clustered corpus — numbers are indicative, not conclusive)\n")

    if errors:
        print("ERRORS (corpus drift, not retrieval failures):")
        for err in errors:
            print(f"  ! {err}")
        print()

    # recall_at_k / mean_reciprocal_rank raise ValueError on an empty case
    # list. That happens for real when every positive case hit corpus drift
    # above (golden set references a paper no longer in the library) — report
    # it plainly instead of letting the ValueError propagate.
    if positives:
        print(
            f"recall@{args.k}: {recall_at_k(positives, args.k):.2f}    "
            f"MRR: {mean_reciprocal_rank(positives, args.k):.3f}"
        )
    else:
        print(f"recall@{args.k}: n/a    MRR: n/a")
        print(
            "  No positive cases could be scored — every content/metadata/figure case "
            "hit corpus drift (see ERRORS above)."
        )
    floor = noise_floor(negatives)
    print(f"noise floor (best off-topic distance): {floor if floor is None else round(floor, 4)}\n")

    print(f"{'case':<28}{'kind':<10}{'rank':>6}{'best_dist':>12}")
    for row in per_case:
        rank = row.get("rank")
        dist = row.get("best_distance")
        print(
            f"{row['id']:<28}{row['kind']:<10}"
            f"{'-' if rank is None else rank:>6}"
            f"{'-' if dist is None else round(dist, 4):>12}"
        )

    # sweep() raises ValueError when there are no usable negatives (including
    # the [[], []] all-empty shape, via `if not any(negatives)`), and even if
    # it didn't, an all-empty-positives sweep would blow up inside sweep()'s
    # own recall_at_k call. Decide up front whether the sweep can run at all,
    # using the same any(negatives) test the metrics module uses so the
    # runner and the library agree on what "usable" means.
    usable_negatives = any(negatives)
    print(f"\nthreshold sweep (k={args.k}):")
    rows = []
    if not usable_negatives:
        print("  skipped")
        print(
            "\nNO RECOMMENDATION: the golden set has no usable off_topic cases (either "
            "there are none, or every off_topic case returned zero chunks), so recall "
            "is trivially maximised by retrieving everything. Add negatives."
        )
    elif not positives:
        print("  skipped")
        print(
            "\nNO RECOMMENDATION: no positive cases were scored (see ERRORS above), so "
            "content recall cannot be measured at any threshold."
        )
    else:
        print(f"{'threshold':>10}{'content_recall':>16}{'offtopic_accept':>18}")
        rows = sweep(positives, negatives, args.k, THRESHOLDS)
        for row in rows:
            print(
                f"{row.threshold:>10.2f}{row.content_recall:>16.2f}{row.off_topic_false_accept:>18.2f}"
            )

        best = separating_threshold(rows)
        if best is None:
            print("\nNO THRESHOLD SEPARATES CONTENT FROM NOISE.")
            print("  No cutoff achieves full content recall with zero off-topic acceptance.")
            print("  An absolute cosine cutoff is the wrong instrument for this model —")
            print("  the next lever is reranking or hybrid retrieval, not a better constant.")
        else:
            print(f"\nRECOMMENDED similarity_threshold: {best:.2f}")
            print(f"  (currently {settings.similarity_threshold})")

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "model": settings.embedding_model,
                    "k": args.k,
                    "corpus": corpus_note,
                    "cases": per_case,
                    "errors": errors,
                    "sweep": [vars(r) for r in rows],
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
