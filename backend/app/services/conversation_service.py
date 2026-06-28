"""ConversationService — CRUD for conversations and messages.

Message embedding is fire-and-forget (asyncio.create_task): it must not
block the SSE stream. Background tasks create their own DB sessions.
"""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sqlalchemy import text

from app.core.logging import log
from app.db.models import ChatConversation, ChatMessage, _now
from app.db.session import SessionLocal
from app.services.embedding_service import EmbeddingService


async def _embed_message(message_id: str, content: str, svc: EmbeddingService) -> None:
    """Embed one message and persist. Runs in background — never raises to caller."""
    try:
        embedding = await svc.embed(content, task_type="RETRIEVAL_DOCUMENT")
        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
        async with SessionLocal() as db:
            import uuid
            await db.execute(text("""
                INSERT INTO conversation_message_embeddings (id, message_id, embedding, created_at)
                VALUES (:id, :message_id, CAST(:emb AS vector), :now)
                ON CONFLICT (message_id) DO UPDATE SET embedding = EXCLUDED.embedding
            """), {"id": str(uuid.uuid4()), "message_id": message_id,
                   "emb": vec_str, "now": _now()})
            await db.commit()
    except Exception as exc:
        log.warning("message_embedding_failed", message_id=message_id, error=str(exc)[:200])


class ConversationService:
    def __init__(self) -> None:
        self._embedding_svc = EmbeddingService()

    async def create_conversation(
        self,
        db: AsyncSession,
        project_id: str,
        user_id: str,
        title: str,
    ) -> ChatConversation:
        """Create an empty conversation. First message is sent via save_message."""
        conv = ChatConversation(project_id=project_id, title=title[:200], created_by=user_id)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        return conv

    async def list_conversations(
        self, db: AsyncSession, project_id: str
    ) -> list[ChatConversation]:
        result = await db.execute(
            select(ChatConversation)
            .where(ChatConversation.project_id == project_id)
            .order_by(ChatConversation.updated_at.desc())
        )
        return list(result.scalars().all())

    async def get_conversation(
        self, db: AsyncSession, conversation_id: str
    ) -> ChatConversation | None:
        result = await db.execute(
            select(ChatConversation)
            .where(ChatConversation.id == conversation_id)
            .options(selectinload(ChatConversation.messages))
        )
        return result.scalar_one_or_none()

    async def save_message(
        self,
        db: AsyncSession,
        conversation_id: str,
        role: str,
        content: str,
        citations: list[dict] | None = None,
    ) -> ChatMessage:
        msg = ChatMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            citations=citations or [],
        )
        db.add(msg)
        # Touch updated_at on the conversation (drives sidebar ordering)
        conv = await db.get(ChatConversation, conversation_id)
        if conv:
            from app.db.models import _now
            conv.updated_at = _now()
        await db.commit()
        await db.refresh(msg)

        asyncio.create_task(_embed_message(msg.id, content, self._embedding_svc))
        return msg
