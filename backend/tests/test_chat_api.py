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


async def test_a_mention_from_another_project_is_rejected(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """Scoping boundary, not input hygiene: papers are project-scoped and
    membership is checked per project, so an unvalidated id would pull another
    project's chunks into this answer."""
    from app.db.models import Paper

    other = Project(owner_id=you.id, title="Other project", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    foreign = Paper(project_id=other.id, title="Foreign paper", source="manual")
    db_session.add(foreign)
    conv = ChatConversation(project_id=project.id, title="Mention test", created_by=you.id)
    db_session.add(conv)
    await db_session.flush()
    db_session.add(ChatMessage(conversation_id=conv.id, role="user", content="Initial"))
    await db_session.commit()

    with patch("app.services.conversation_service._embed_message", new=AsyncMock()):
        resp = await client.post(
            f"/v1/projects/{project.id}/conversations/{conv.id}/messages",
            json={"content": "What does it say?", "mentioned_paper_ids": [foreign.id]},
            headers={"X-Dev-User-Id": you.id},
        )

    assert resp.status_code == 400


async def test_mentions_are_persisted_on_the_user_message_and_returned(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    from app.db.models import Paper

    paper = Paper(project_id=project.id, title="Mentioned paper", source="manual")
    db_session.add(paper)
    conv = ChatConversation(project_id=project.id, title="Mention test", created_by=you.id)
    db_session.add(conv)
    await db_session.flush()
    db_session.add(ChatMessage(conversation_id=conv.id, role="user", content="Initial"))
    await db_session.commit()

    async def fake_respond(conversation_id, user_content, mentioned_paper_ids):
        yield {"event": "done", "data": json.dumps({"citations": []})}

    with (
        patch("app.api.v1.chat.chat_service.respond", new=fake_respond),
        patch("app.services.conversation_service._embed_message", new=AsyncMock()),
    ):
        await client.post(
            f"/v1/projects/{project.id}/conversations/{conv.id}/messages",
            json={
                "content": "What reward does it use?",
                "mentioned_paper_ids": [paper.id, paper.id],
            },
            headers={"X-Dev-User-Id": you.id},
        )

    detail = await client.get(
        f"/v1/projects/{project.id}/conversations/{conv.id}",
        headers={"X-Dev-User-Id": you.id},
    )
    last_user = [m for m in detail.json()["messages"] if m["role"] == "user"][-1]
    # Deduped, and ids only — never titles, which go stale on rename.
    assert last_user["mentions"] == [paper.id]


async def test_renaming_a_conversation(client: AsyncClient, you: User, project: Project):
    with patch(
        "app.services.conversation_service._embed_message",
        new=AsyncMock(return_value=None),
    ):
        created = await client.post(
            f"/v1/projects/{project.id}/conversations",
            json={"content": "What are the main findings?"},
            headers={"X-Dev-User-Id": you.id},
        )

    resp = await client.patch(
        f"/v1/projects/{project.id}/conversations/{created.json()['id']}",
        json={"title": "Findings review"},
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 200
    assert resp.json()["title"] == "Findings review"
    listed = await client.get(
        f"/v1/projects/{project.id}/conversations", headers={"X-Dev-User-Id": you.id}
    )
    assert [c["title"] for c in listed.json()] == ["Findings review"]


async def test_two_conversations_may_share_a_title(
    client: AsyncClient, you: User, project: Project
):
    """Unlike a LaTeX project, a conversation is identified by what was said
    in it -- two chats about the same thing sharing a name is ordinary, not
    a mistake worth interrupting for."""
    with patch(
        "app.services.conversation_service._embed_message",
        new=AsyncMock(return_value=None),
    ):
        first = await client.post(
            f"/v1/projects/{project.id}/conversations",
            json={"content": "One"},
            headers={"X-Dev-User-Id": you.id},
        )
        second = await client.post(
            f"/v1/projects/{project.id}/conversations",
            json={"content": "Two"},
            headers={"X-Dev-User-Id": you.id},
        )

    await client.patch(
        f"/v1/projects/{project.id}/conversations/{first.json()['id']}",
        json={"title": "Same"},
        headers={"X-Dev-User-Id": you.id},
    )
    resp = await client.patch(
        f"/v1/projects/{project.id}/conversations/{second.json()['id']}",
        json={"title": "Same"},
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 200


async def test_renaming_a_conversation_in_another_project_is_a_404(
    client: AsyncClient, you: User, project: Project
):
    resp = await client.patch(
        f"/v1/projects/{project.id}/conversations/does-not-exist",
        json={"title": "Nope"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 404
