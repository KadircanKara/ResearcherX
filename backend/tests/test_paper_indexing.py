"""Service-level tests for chunk building and indexing."""

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Paper, PaperChunkEmbedding, Project, ProjectMember, User
from app.db.seed import seed_users
from app.services import paper_ingest_service as svc


@pytest_asyncio.fixture
async def paper(db_session: AsyncSession) -> Paper:
    await seed_users(db_session)
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    p = Project(owner_id=you.id, title="Indexing Project", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    paper = Paper(project_id=p.id, title="T", source="manual")
    db_session.add(paper)
    await db_session.commit()
    await db_session.refresh(paper)
    return paper


async def _chunks(db_session: AsyncSession, paper_id: str) -> list[str]:
    rows = await db_session.execute(
        select(PaperChunkEmbedding)
        .where(PaperChunkEmbedding.paper_id == paper_id)
        .order_by(PaperChunkEmbedding.chunk_index)
    )
    return [r.text for r in rows.scalars().all()]


def _fake_embed(n_dims: int = 768):
    async def _embed(texts, task_type="RETRIEVAL_DOCUMENT"):
        return [[0.0] * n_dims for _ in texts]

    return AsyncMock(side_effect=_embed)


async def test_index_manual_abstract_only(db_session: AsyncSession, paper: Paper):
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        n = await svc.index_manual(db_session, paper.id, "A standalone abstract.", None)
    assert n == 1
    assert await _chunks(db_session, paper.id) == ["A standalone abstract."]


async def test_index_manual_skips_abstract_already_in_body(db_session: AsyncSession, paper: Paper):
    abstract = "We study swarms."
    body = f"{abstract} Then we go into detail about the method."
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_manual(db_session, paper.id, abstract, body)
    chunks = await _chunks(db_session, paper.id)
    assert len(chunks) == 1
    assert chunks[0] == body


async def test_index_manual_dedupe_ignores_whitespace_differences(
    db_session: AsyncSession, paper: Paper
):
    abstract = "We  study\n swarms."
    body = "We study swarms. Then detail."
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_manual(db_session, paper.id, abstract, body)
    assert len(await _chunks(db_session, paper.id)) == 1


async def test_index_manual_keeps_unique_abstract_at_index_zero(
    db_session: AsyncSession, paper: Paper
):
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_manual(db_session, paper.id, "Unique summary.", "Totally different body.")
    chunks = await _chunks(db_session, paper.id)
    assert chunks[0] == "Unique summary."
    assert len(chunks) == 2


async def test_index_chunks_is_idempotent(db_session: AsyncSession, paper: Paper):
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_chunks(db_session, paper.id, ["one", "two"])
        await svc.index_chunks(db_session, paper.id, ["one", "two"])
    assert await _chunks(db_session, paper.id) == ["one", "two"]


async def test_index_chunks_empty_clears_existing(db_session: AsyncSession, paper: Paper):
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_chunks(db_session, paper.id, ["one"])
        n = await svc.index_chunks(db_session, paper.id, [])
    assert n == 0
    assert await _chunks(db_session, paper.id) == []
