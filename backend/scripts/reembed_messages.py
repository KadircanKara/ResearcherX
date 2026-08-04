"""Re-embed chat messages under the currently configured model.

Message content is persisted, so unlike papers these need no re-fetch. Rows
whose model already matches are skipped, making this safe to re-run.
"""

import asyncio

from sqlalchemy import text

from app.core.config import settings
from app.db.models import _now
from app.db.session import SessionLocal
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

        print(f"re-embedding {len(rows)} messages as {settings.embedding_model}")
        for row in rows:
            emb = await svc.embed(row.content, task_type="RETRIEVAL_DOCUMENT")
            vec_str = "[" + ",".join(str(x) for x in emb) + "]"
            await db.execute(
                text("""
                INSERT INTO conversation_message_embeddings
                    (id, message_id, embedding, model, created_at)
                VALUES (gen_random_uuid()::text, :message_id,
                        CAST(:emb AS vector), :model, :now)
                ON CONFLICT (message_id) DO UPDATE
                    SET embedding = EXCLUDED.embedding, model = EXCLUDED.model
            """),
                {
                    "message_id": row.id,
                    "emb": vec_str,
                    "model": settings.embedding_model,
                    "now": _now(),
                },
            )
        await db.commit()
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
