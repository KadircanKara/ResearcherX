"""Re-index paper chunks under the currently configured embedding model.

The paper-side counterpart to reembed_messages.py. Changing EMBEDDING_MODEL or
either EMBEDDING_*_PREFIX invalidates every stored vector — retrieval filters on
`model` in SQL, so stale rows don't error, they just silently stop being
retrievable. Nothing detects that; this script is the documented repair.

Run:  docker compose exec -T backend python -m scripts.reindex_papers

The `-m` form is required: pyproject.toml packages only `app*`, so `scripts` is
not installed and file-path invocation drops cwd from sys.path.

No PDF re-fetch is needed: `ingest()` persists the extracted markdown to
`papers.extracted_text`, so chunking replays from the database. Papers ingested
by hand have no extracted_text and are replayed through the manual path instead,
matching how each was originally indexed.
"""

import argparse
import asyncio

from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.paper_ingest_service import (
    _chunk_text,
    _extract_figure_captions,
    index_chunks,
    index_manual,
)

# Papers with no chunk at the configured model. A paper that genuinely indexes
# to zero chunks (empty text) is selected on every run; that is deliberate —
# treating "no rows" as "already done" would permanently hide a paper whose
# ingest silently produced nothing.
_STALE_SQL = text("""
    SELECT p.id, p.title, p.extracted_text, p.abstract, p.body
    FROM papers p
    WHERE NOT EXISTS (
        SELECT 1 FROM paper_chunk_embeddings c
        WHERE c.paper_id = p.id AND c.model = :model
    )
    ORDER BY p.created_at
""")


async def _reindex_one(row) -> int:
    """Replay the indexing path this paper was originally ingested through."""
    async with SessionLocal() as db:
        if row.extracted_text:
            md = row.extracted_text
            chunks = _chunk_text(md) + _extract_figure_captions(md)
            return await index_chunks(db, row.id, chunks)
        return await index_manual(db, row.id, row.abstract, row.body)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be re-indexed, embed nothing",
    )
    args = parser.parse_args()

    async with SessionLocal() as db:
        rows = (await db.execute(_STALE_SQL, {"model": settings.embedding_model})).fetchall()

    print(f"{len(rows)} paper(s) missing chunks for model {settings.embedding_model!r}")
    if args.dry_run:
        for row in rows:
            path = "extracted_text" if row.extracted_text else "manual (abstract+body)"
            print(f"  would re-index {row.id}  [{path}]  {row.title[:60]}")
        return

    # One paper per transaction, sequentially. index_chunks deletes the paper's
    # existing rows before inserting, so a failure mid-run leaves that ONE paper
    # unindexed and every other paper untouched — and the next run picks it up,
    # because it still has no row at the configured model. Batching papers into
    # a shared transaction would trade that property for throughput this script
    # does not need.
    failed: list[tuple[str, str]] = []
    for i, row in enumerate(rows, 1):
        try:
            n = await _reindex_one(row)
            print(f"[{i}/{len(rows)}] {n:>4} chunks  {row.title[:60]}", flush=True)
        except Exception as exc:
            failed.append((row.id, f"{type(exc).__name__}: {exc}"))
            print(
                f"[{i}/{len(rows)}] FAILED       {row.title[:60]}  ({type(exc).__name__})",
                flush=True,
            )

    # Re-query rather than trusting the loop: proves the rows are actually at
    # the configured model, which is the only thing retrieval will accept.
    async with SessionLocal() as db:
        (remaining,) = (
            await db.execute(
                text("""
                SELECT count(*) FROM papers p
                WHERE NOT EXISTS (
                    SELECT 1 FROM paper_chunk_embeddings c
                    WHERE c.paper_id = p.id AND c.model = :model
                )
            """),
                {"model": settings.embedding_model},
            )
        ).one()

    print(f"\nre-indexed {len(rows) - len(failed)} of {len(rows)} papers")
    for paper_id, err in failed:
        print(f"  failed: {paper_id}  {err}")
    if remaining:
        print(f"{remaining} paper(s) still have no chunks at {settings.embedding_model!r}")


if __name__ == "__main__":
    asyncio.run(main())
