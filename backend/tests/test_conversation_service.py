"""ConversationService unit tests."""
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, User
from app.db.seed import seed_users


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


@pytest_asyncio.fixture
async def you(db_session: AsyncSession, seeded):
    return (await db_session.execute(
        select(User).where(User.email == "you@researcherx.dev")
    )).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Conv Test", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_create_conversation(db_session: AsyncSession, project: Project, you: User):
    from app.services.conversation_service import ConversationService
    svc = ConversationService()
    with patch(
        "app.services.conversation_service._embed_message",
        new=AsyncMock(return_value=None),
    ):
        conv = await svc.create_conversation(db_session, project.id, you.id, "What is UAV?")
    assert conv.title == "What is UAV?"
    assert conv.project_id == project.id


async def test_list_conversations(db_session: AsyncSession, project: Project, you: User):
    from app.services.conversation_service import ConversationService
    svc = ConversationService()
    with patch("app.services.conversation_service._embed_message", new=AsyncMock()):
        await svc.create_conversation(db_session, project.id, you.id, "First")
        await svc.create_conversation(db_session, project.id, you.id, "Second")
    convs = await svc.list_conversations(db_session, project.id)
    assert len(convs) == 2


async def test_save_message_and_get_conversation(
    db_session: AsyncSession, project: Project, you: User
):
    from app.services.conversation_service import ConversationService
    svc = ConversationService()
    with patch("app.services.conversation_service._embed_message", new=AsyncMock()):
        conv = await svc.create_conversation(db_session, project.id, you.id, "What is MTSP?")
        msg = await svc.save_message(
            db_session, conv.id, "assistant",
            "MTSP stands for...", citations=[{"n": 1, "paper_id": "x"}]
        )
    assert msg.role == "assistant"
    assert msg.citations[0]["n"] == 1

    fetched = await svc.get_conversation(db_session, conv.id)
    assert fetched is not None
    assert len(fetched.messages) == 1   # only the assistant message (conversation creation no longer stores first message)
