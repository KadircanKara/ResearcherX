"""Chat API endpoint tests."""

import json
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ChatConversation,
    ChatMessage,
    Project,
    ProjectMember,
    User,
)
from app.db.seed import seed_users


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


@pytest_asyncio.fixture
async def you(db_session: AsyncSession, seeded):
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Chat API Test", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_create_conversation(client: AsyncClient, you: User, project: Project):
    with patch(
        "app.services.conversation_service._embed_message",
        new=AsyncMock(return_value=None),
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/conversations",
            json={"content": "What are the main findings?"},
            headers={"X-Dev-User-Id": you.id},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "What are the main findings?"
    assert data["project_id"] == project.id


async def test_list_conversations(client: AsyncClient, you: User, project: Project):
    with patch("app.services.conversation_service._embed_message", new=AsyncMock()):
        await client.post(
            f"/v1/projects/{project.id}/conversations",
            json={"content": "First question here"},
            headers={"X-Dev-User-Id": you.id},
        )
    resp = await client.get(
        f"/v1/projects/{project.id}/conversations",
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_get_conversation_detail(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    conv = ChatConversation(project_id=project.id, title="Test", created_by=you.id)
    db_session.add(conv)
    await db_session.flush()
    msg = ChatMessage(conversation_id=conv.id, role="user", content="Hello?")
    db_session.add(msg)
    await db_session.commit()

    resp = await client.get(
        f"/v1/projects/{project.id}/conversations/{conv.id}",
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "Hello?"


async def test_send_message_streams(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """POST /messages should stream SSE events and return 200."""
    conv = ChatConversation(project_id=project.id, title="Stream test", created_by=you.id)
    db_session.add(conv)
    await db_session.flush()
    db_session.add(ChatMessage(conversation_id=conv.id, role="user", content="Initial"))
    await db_session.commit()

    async def fake_respond(conversation_id, user_content):
        yield {"event": "thinking", "data": "{}"}
        yield {"event": "delta", "data": json.dumps({"text": "answer"})}
        yield {"event": "done", "data": json.dumps({"citations": []})}

    with patch("app.api.v1.chat.chat_service.respond", new=fake_respond):
        with patch(
            "app.services.conversation_service._embed_message", new=AsyncMock(return_value=None)
        ):
            resp = await client.post(
                f"/v1/projects/{project.id}/conversations/{conv.id}/messages",
                json={"content": "A question"},
                headers={"X-Dev-User-Id": you.id},
            )
    assert resp.status_code == 200
    # SSE content-type
    assert "text/event-stream" in resp.headers.get("content-type", "")


async def test_send_message_requires_auth(
    client: AsyncClient, project: Project, db_session: AsyncSession
):
    conv = ChatConversation(project_id=project.id, title="Auth test", created_by="x")
    db_session.add(conv)
    await db_session.commit()
    # No X-Dev-User-Id header → get_current_user falls back to default seed user
    # who IS a member, so this should succeed. Test non-member project instead:
    # Create a project owned by someone else
    amelia = (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one_or_none()
    assert amelia is not None
    other_proj = Project(owner_id=amelia.id, title="Other", topic_keywords=[])
    db_session.add(other_proj)
    await db_session.flush()
    other_conv = ChatConversation(project_id=other_proj.id, title="t", created_by=amelia.id)
    db_session.add(other_conv)
    await db_session.commit()
    # seed user "you" is NOT a member of other_proj → 404
    resp = await client.post(
        f"/v1/projects/{other_proj.id}/conversations/{other_conv.id}/messages",
        json={"content": "snoop"},
    )
    assert resp.status_code == 404


async def test_a_renamed_paper_retitles_its_citations_in_past_conversations(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """The whole point: a title fixed in the Papers tab reaches old chips.

    The stored citation is a snapshot taken when the answer was written. Its
    chunk_index and snippet must stay pinned to the text the model was shown,
    but the title is a label for a paper that still exists — so it is resolved
    against the papers table on every read.
    """
    from app.db.models import Paper

    paper = Paper(project_id=project.id, title="Old typo'd title", source="manual")
    db_session.add(paper)
    await db_session.flush()

    conv = ChatConversation(project_id=project.id, title="Retitle test", created_by=you.id)
    db_session.add(conv)
    await db_session.flush()
    db_session.add(
        ChatMessage(
            conversation_id=conv.id,
            role="assistant",
            content="Grounded here [1].",
            citations=[
                {
                    "n": 1,
                    "paper_id": paper.id,
                    "title": "Old typo'd title",
                    "chunk_index": 3,
                    "snippet": "the excerpt as shown to the model",
                }
            ],
        )
    )
    await db_session.commit()

    paper.title = "Corrected title"
    await db_session.commit()

    resp = await client.get(
        f"/v1/projects/{project.id}/conversations/{conv.id}",
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    citation = resp.json()["messages"][0]["citations"][0]
    assert citation["title"] == "Corrected title"
    # Evidence fields stay pinned to what the model was actually shown.
    assert citation["chunk_index"] == 3
    assert citation["snippet"] == "the excerpt as shown to the model"

    # A GET must not write. The snapshot on the row is left as it was.
    stored = (
        await db_session.execute(select(ChatMessage).where(ChatMessage.conversation_id == conv.id))
    ).scalar_one()
    await db_session.refresh(stored)
    assert stored.citations[0]["title"] == "Old typo'd title"


async def test_a_deleted_papers_citation_keeps_the_title_it_was_written_with(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """No current title means no paper. The stored label beats a blank chip."""
    conv = ChatConversation(project_id=project.id, title="Orphan test", created_by=you.id)
    db_session.add(conv)
    await db_session.flush()
    db_session.add(
        ChatMessage(
            conversation_id=conv.id,
            role="assistant",
            content="Grounded here [1].",
            citations=[
                {
                    "n": 1,
                    "paper_id": "a-paper-that-no-longer-exists",
                    "title": "Title at answer time",
                    "chunk_index": 0,
                    "snippet": "excerpt",
                }
            ],
        )
    )
    await db_session.commit()

    resp = await client.get(
        f"/v1/projects/{project.id}/conversations/{conv.id}",
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.json()["messages"][0]["citations"][0]["title"] == "Title at answer time"
