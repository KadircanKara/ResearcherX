"""ConversationService — CRUD for conversations and messages.

Message embedding is fire-and-forget (asyncio.create_task): it must not
block the SSE stream. Background tasks create their own DB sessions.
"""

import asyncio
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sqlalchemy import text

from app.core.config import settings
from app.core.logging import log
from app.db.models import ChatConversation, ChatMessage, Paper, _now
from app.db.session import SessionLocal
from app.services.embedding_service import EmbeddingService


async def _embed_message(message_id: str, content: str, svc: EmbeddingService) -> None:
    """Embed one message and persist. Runs in background — never raises to caller."""
    try:
        embedding = await svc.embed(content, task_type="RETRIEVAL_DOCUMENT")
        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
        async with SessionLocal() as db:
            import uuid

            await db.execute(
                text("""
                INSERT INTO conversation_message_embeddings
                    (id, message_id, embedding, model, created_at)
                VALUES (:id, :message_id, CAST(:emb AS vector), :model, :now)
                ON CONFLICT (message_id) DO UPDATE
                    SET embedding = EXCLUDED.embedding, model = EXCLUDED.model
            """),
                {
                    "id": str(uuid.uuid4()),
                    "message_id": message_id,
                    "emb": vec_str,
                    "model": settings.embedding_model,
                    "now": _now(),
                },
            )
            await db.commit()
    except Exception as exc:
        log.warning("message_embedding_failed", message_id=message_id, error=str(exc)[:200])


def retitle_citations(citations: list[dict], titles: Mapping[str, str]) -> list[dict]:
    """Re-label persisted citations with each paper's CURRENT title.

    A citation is written as a snapshot — n, paper_id, title, chunk_index,
    snippet — because the chip has to render without a join, and because
    chunk_index and snippet must stay pinned to the text the model was
    actually shown. The TITLE is the one field that is not evidence: it is a
    label for a paper that still exists, and renaming that paper in the Papers
    tab has to change every chip and hover card that points at it. Rewriting
    the stored rows on rename would leave every conversation one failed
    backfill away from lying again, so resolution happens on the read path
    instead, where it is self-healing by construction.

    A paper_id with no current title (the paper was deleted) keeps its stored
    title: a chip labelled with the name the answer was written against beats
    a chip labelled with nothing.

    Returns new dicts. The ORM instances are left untouched — a mutated JSON
    column would be a write on a GET.
    """
    return [
        {**c, "title": titles.get(c.get("paper_id"), c.get("title"))} if isinstance(c, dict) else c
        for c in citations
    ]


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

    async def list_conversations(self, db: AsyncSession, project_id: str) -> list[ChatConversation]:
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

    async def current_paper_titles(self, db: AsyncSession, project_id: str) -> dict[str, str]:
        """paper_id -> title for this project, as the papers table has it NOW."""
        result = await db.execute(
            select(Paper.id, Paper.title).where(Paper.project_id == project_id)
        )
        return {paper_id: title for paper_id, title in result.all()}

    async def validate_mentions(
        self, db: AsyncSession, project_id: str, paper_ids: list[str]
    ) -> list[str]:
        """Dedupe and confirm every id is a paper of THIS project.

        A scoping boundary, not input hygiene. Papers are project-scoped and
        membership is checked per project, so an id from elsewhere would read
        another project's chunks into this answer. Raises ValueError, which the
        router turns into a 400 — the caller never learns whether the id exists
        somewhere else.
        """
        deduped = list(dict.fromkeys(paper_ids))
        if not deduped:
            return []
        rows = await db.execute(
            select(Paper.id).where(Paper.project_id == project_id, Paper.id.in_(deduped))
        )
        known = {row[0] for row in rows.all()}
        if len(known) != len(deduped):
            raise ValueError("unknown paper id in mentions")
        return deduped

    async def save_message(
        self,
        db: AsyncSession,
        conversation_id: str,
        role: str,
        content: str,
        citations: list[dict] | None = None,
        mentions: list[str] | None = None,
    ) -> ChatMessage:
        msg = ChatMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            citations=citations or [],
            mentions=mentions or [],
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
