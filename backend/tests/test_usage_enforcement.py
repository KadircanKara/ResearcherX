"""Each limit refuses the (N+1)th action with a fixed 429, and 0 turns it off."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models import ChatConversation, ChatMessage, Paper, Project, ProjectMember, User
from app.db.seed import seed_users


@pytest_asyncio.fixture
async def you(db_session):
    await seed_users(db_session)
    await db_session.commit()
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session, you):
    p = Project(owner_id=you.id, title="P", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    return p


@pytest.fixture
def limits(monkeypatch):
    from app.core.config import settings

    def _set(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(settings, k, v)

    return _set


async def _add_papers(db, project, n):
    for i in range(n):
        db.add(Paper(project_id=project.id, title=f"p{i}", source="upload"))
    await db.commit()


async def test_the_21st_paper_is_refused(client, db_session, project, limits):
    limits(user_max_papers=20)
    await _add_papers(db_session, project, 19)
    ok = await client.post(
        f"/v1/projects/{project.id}/papers", json={"title": "20", "source": "upload"}
    )
    assert ok.status_code == 201
    r = await client.post(
        f"/v1/projects/{project.id}/papers", json={"title": "21", "source": "upload"}
    )
    assert r.status_code == 429
    assert r.json() == {"detail": "Paper limit reached (20). Delete a paper to add another."}


async def test_papers_in_every_owned_project_count(client, db_session, you, project, limits):
    limits(user_max_papers=1)
    other = Project(owner_id=you.id, title="Q", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=other.id, user_id=you.id, role="owner"))
    await _add_papers(db_session, other, 1)
    r = await client.post(
        f"/v1/projects/{project.id}/papers", json={"title": "x", "source": "upload"}
    )
    assert r.status_code == 429


async def test_zero_means_unlimited(client, db_session, project, limits):
    limits(user_max_papers=0)
    await _add_papers(db_session, project, 50)
    r = await client.post(
        f"/v1/projects/{project.id}/papers", json={"title": "x", "source": "upload"}
    )
    assert r.status_code == 201


async def test_suggest_title_is_refused_at_the_paper_cap_before_any_llm_call(
    client, db_session, project, limits
):
    limits(user_max_papers=1)
    await _add_papers(db_session, project, 1)
    llm = AsyncMock(return_value=("t", "a", "b"))
    with patch("app.services.title_extraction_service.extract_meta_from_pdf", new=llm):
        r = await client.post(f"/v1/projects/{project.id}/papers/suggest-title", content=b"%PDF")
    assert r.status_code == 429
    llm.assert_not_called()


async def test_title_assists_are_limited_per_day(client, project, limits):
    limits(user_llm_assists_per_day=2)
    llm = AsyncMock(return_value=("t", "a", "b"))
    with patch("app.services.title_extraction_service.extract_meta_from_pdf", new=llm):
        for _ in range(2):
            ok = await client.post(
                f"/v1/projects/{project.id}/papers/suggest-title", content=b"%PDF"
            )
            assert ok.status_code == 200
        r = await client.post(f"/v1/projects/{project.id}/papers/suggest-title", content=b"%PDF")
    assert r.status_code == 429
    assert r.json()["detail"] == ("Daily title-suggestion limit reached (2). Resets at 00:00 UTC.")


async def test_suggest_title_refuses_oversize_body(client, project, limits):
    limits(paper_pdf_max_bytes=10)
    r = await client.post(f"/v1/projects/{project.id}/papers/suggest-title", content=b"x" * 11)
    assert r.status_code == 413


async def _conversation_with_turns(db, project, n, when):
    conv = ChatConversation(project_id=project.id, title="c", created_by=project.owner_id)
    db.add(conv)
    await db.flush()
    for _ in range(n):
        db.add(ChatMessage(conversation_id=conv.id, role="user", content="q", created_at=when))
    await db.commit()
    return conv


async def test_the_101st_turn_today_is_refused_and_yesterday_does_not_count(
    client, db_session, project, limits
):
    limits(user_chat_turns_per_day=100)
    now = datetime.now(timezone.utc)
    conv = await _conversation_with_turns(db_session, project, 100, now)
    await _conversation_with_turns(db_session, project, 500, now - timedelta(days=1, hours=1))
    r = await client.post(
        f"/v1/projects/{project.id}/conversations/{conv.id}/messages", json={"content": "hi"}
    )
    assert r.status_code == 429
    assert r.json() == {"detail": "Daily chat limit reached (100). Resets at 00:00 UTC."}


async def test_the_global_backstop_refuses_everyone(client, db_session, project, limits):
    limits(global_chat_turns_per_day=3)
    conv = await _conversation_with_turns(db_session, project, 3, datetime.now(timezone.utc))
    r = await client.post(
        f"/v1/projects/{project.id}/conversations/{conv.id}/messages", json={"content": "hi"}
    )
    assert r.status_code == 429
    assert r.json() == {"detail": "The demo is at capacity for today. Try again after 00:00 UTC."}
