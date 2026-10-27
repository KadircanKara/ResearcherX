"""Read-only check of every golden-set case against the live dev database.

For each positive case: exactly one paper must hold chunks satisfying it (the
shared `chunk_satisfies` rule), and the question's wording must pass the
leakage checker for its kind. Off-topic cases are never a failure; the nearest
chunks are printed so a human can confirm the corpus really does not cover
them. Exit 1 on any problem.

    docker compose exec -T backend python -m evals.retrieval.validate_golden_set \
        --project-id <uuid> [--set PATH] [--offtopic-nearest 5]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.services.bm25_tokenizer import terms
from evals.retrieval.bm25_arm import CorpusChunk, index_text
from evals.retrieval.golden_set import Case, GoldenSetError, chunk_satisfies, load_golden_set
from evals.retrieval.leakage import check

_DEFAULT_SET = Path(__file__).parent / "golden_set.json"


def document_frequencies(texts: Iterable[str]) -> tuple[dict[str, int], int]:
    """Per-term document frequency and the document count.

    A document counts once per distinct term, however often it repeats.
    """
    df: dict[str, int] = defaultdict(int)
    n_docs = 0
    for t in texts:
        n_docs += 1
        for term in set(terms(t)):
            df[term] += 1
    return dict(df), n_docs


def satisfying_papers(case: Case, chunks: Iterable[CorpusChunk]) -> dict[str, list[CorpusChunk]]:
    """paper_id -> the chunks of that paper which satisfy `case`."""
    out: dict[str, list[CorpusChunk]] = defaultdict(list)
    for c in chunks:
        if chunk_satisfies(case, c.paper_title, c.text):
            out[c.paper_id].append(c)
    return dict(out)


def validate_case(
    case: Case,
    by_paper: Mapping[str, Sequence[CorpusChunk]],
    df: Mapping[str, int],
    n_docs: int,
) -> list[str]:
    """Problems with one positive case; an empty list means it is sound."""
    if not by_paper:
        return [f"{case.id}: no chunk satisfies the case (paper and every expected substring)"]
    if len(by_paper) > 1:
        titles = sorted({chunks[0].paper_title for chunks in by_paper.values()})
        return [f"{case.id}: satisfied by {len(by_paper)} papers: " + "; ".join(titles)]
    chunks = next(iter(by_paper.values()))
    evidence = "\n".join(
        [chunks[0].paper_title, *(f"{c.section_text or ''}\n{c.text}" for c in chunks)]
    )
    result = check(case, evidence, df, n_docs)
    if result.ok:
        return []
    shared = ", ".join(sorted(result.shared_rare)) or "none"
    return [f"{case.id} ({case.kind}): {result.reason} [shared rare tokens: {shared}]"]


_NEAREST_SQL = text("""
    SELECT p.title AS paper_title, c.text AS chunk_text,
           (c.embedding <=> CAST(:qvec AS vector)) AS distance
    FROM paper_chunk_embeddings c
    JOIN papers p ON p.id = c.paper_id
    WHERE c.model = :model
      AND p.project_id = :project_id
    ORDER BY distance ASC
    LIMIT :n
""")


async def _print_nearest(db, svc, case: Case, project_id: str, n: int) -> None:
    from evals.retrieval.run_eval import _vec

    embedding = await svc.embed(case.question, task_type="RETRIEVAL_QUERY")
    rows = (
        await db.execute(
            _NEAREST_SQL,
            {
                "qvec": _vec(embedding),
                "model": settings.embedding_model,
                "project_id": project_id,
                "n": n,
            },
        )
    ).fetchall()
    print(f"\n[off_topic] {case.id}: {case.question}")
    for r in rows:
        snippet = " ".join(r.chunk_text.split())[:200]
        print(f"  {float(r.distance):.4f}  {r.paper_title}\n          {snippet}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--set", type=Path, default=_DEFAULT_SET)
    parser.add_argument("--offtopic-nearest", type=int, default=5)
    args = parser.parse_args()

    try:
        cases = load_golden_set(args.set)
    except GoldenSetError as exc:
        print(f"ERROR: {exc}")
        return 1

    from app.db.session import SessionLocal
    from app.services.embedding_service import EmbeddingService
    from evals.retrieval.run_eval import _CORPUS_SQL

    problems: list[str] = []
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                _CORPUS_SQL,
                {"model": settings.embedding_model, "project_id": args.project_id},
            )
        ).fetchall()
        chunks = [
            CorpusChunk(r.chunk_id, r.paper_id, r.paper_title, r.text, r.section_text) for r in rows
        ]
        if not chunks:
            print(f"ERROR: no chunks at model {settings.embedding_model!r} in this project")
            return 1
        df, n_docs = document_frequencies(index_text(c) for c in chunks)

        positives = [c for c in cases if not c.is_negative]
        for case in positives:
            problems.extend(validate_case(case, satisfying_papers(case, chunks), df, n_docs))

        negatives = [c for c in cases if c.is_negative]
        if negatives and args.offtopic_nearest > 0:
            svc = EmbeddingService()
            for case in negatives:
                await _print_nearest(db, svc, case, args.project_id, args.offtopic_nearest)

    print(f"\n{len(positives)} positive, {len(negatives)} off_topic, {n_docs} chunks")
    for p in problems:
        print(f"PROBLEM {p}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
