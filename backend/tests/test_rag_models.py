"""Smoke tests: all new RAG models create/read round-trip in SQLite."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import (
    ChatConversation,
    ChatMessage,
    Paper,
    PaperChunkEmbedding,
    ConversationMessageEmbedding,
    User,
    Project,
    ProjectMember,
)
from app.db.seed import seed_users


@pytest.fixture(autouse=True)
async def _seed(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


@pytest.fixture
async def you(db_session: AsyncSession):
    from sqlalchemy import select

    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="RAG Test Project", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_paper_creates_and_has_project(db_session: AsyncSession, project: Project):
    paper = Paper(project_id=project.id, title="Test Paper", abstract="Abstract text here.")
    db_session.add(paper)
    await db_session.commit()
    await db_session.refresh(paper)
    assert paper.id is not None
    assert paper.project_id == project.id


async def test_conversation_creates(db_session: AsyncSession, project: Project, you: User):
    conv = ChatConversation(project_id=project.id, title="First chat", created_by=you.id)
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)
    assert conv.id is not None


async def test_message_creates(db_session: AsyncSession, project: Project, you: User):
    conv = ChatConversation(project_id=project.id, title="Msg test", created_by=you.id)
    db_session.add(conv)
    await db_session.flush()
    msg = ChatMessage(conversation_id=conv.id, role="user", content="Hello world")
    db_session.add(msg)
    await db_session.commit()
    await db_session.refresh(msg)
    assert msg.role == "user"
    assert msg.citations == []


async def test_paper_chunk_embedding_creates(db_session: AsyncSession, project: Project):
    paper = Paper(project_id=project.id, title="Emb Paper")
    db_session.add(paper)
    await db_session.flush()
    chunk = PaperChunkEmbedding(
        paper_id=paper.id,
        chunk_index=0,
        text="sample chunk text",
        embedding="[0.1,0.2,0.3]",  # TEXT column in SQLite test DB
    )
    db_session.add(chunk)
    await db_session.commit()
    await db_session.refresh(chunk)
    assert chunk.chunk_index == 0


async def test_message_embedding_creates(db_session: AsyncSession, project: Project, you: User):
    conv = ChatConversation(project_id=project.id, title="Emb msg", created_by=you.id)
    db_session.add(conv)
    await db_session.flush()
    msg = ChatMessage(conversation_id=conv.id, role="user", content="Test")
    db_session.add(msg)
    await db_session.flush()
    emb = ConversationMessageEmbedding(message_id=msg.id, embedding="[0.1,0.2]")
    db_session.add(emb)
    await db_session.commit()
    await db_session.refresh(emb)
    assert emb.message_id == msg.id
