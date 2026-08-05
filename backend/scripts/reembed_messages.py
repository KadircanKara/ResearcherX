"""Re-embed chat messages under the currently configured model.

Message content is persisted, so unlike papers these need no re-fetch. Rows
whose model already matches are skipped, making this safe to re-run.
"""

import asyncio

from sqlalchemy import bindparam, text

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.conversation_service import _embed_message
from app.services.embedding_service import EmbeddingService


async def main() -> None:
    svc = EmbeddingService()
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                text("""
                SELECT cm.id, cm.content
                FROM chat_messages cm
                LEFT JOIN conversation_message_embeddings cme ON cme.message_id = cm.id
                WHERE cme.model IS DISTINCT FROM :model
            """),
                {"model": settings.embedding_model},
            )
        ).fetchall()

    total = len(rows)
    print(f"re-embedding {total} messages as {settings.embedding_model}")

    # One row at a time via _embed_message, not embed_batch(): this is a
    # recovery script, run precisely when something has already gone wrong
    # (rate limits, quota exhaustion, a flaky provider). _embed_message opens
    # its own session and commits per message, so a failure partway through
    # loses at most one row instead of rolling back every embedding computed
    # so far in one shared transaction. Don't "optimise" this back into a
    # batched call — isolation matters more than throughput here.
    for row in rows:
        await _embed_message(row.id, row.content, svc)

    # _embed_message swallows errors by design (it's written for a
    # fire-and-forget background task, so it never raises to the caller) —
    # the loop above can't tell us what failed. Re-query instead of trusting
    # it: count how many of the rows we just attempted now sit at the
    # configured model.
    done = 0
    ids = [row.id for row in rows]
    if ids:
        async with SessionLocal() as db:
            query = text("""
                SELECT count(*) FROM conversation_message_embeddings
                WHERE message_id IN :ids AND model = :model
            """).bindparams(bindparam("ids", expanding=True))
            (done,) = (
                await db.execute(query, {"ids": ids, "model": settings.embedding_model})
            ).one()
    print(f"re-embedded {done} of {total} messages")
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
