import pytest_asyncio
from sqlalchemy import select

from app.db.models import Paper, Project, ProjectMember, User
from app.db.seed import seed_users


@pytest_asyncio.fixture
async def project(db_session):
    await seed_users(db_session)
    await db_session.commit()
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    p = Project(owner_id=you.id, title="P", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    db_session.add(Paper(project_id=p.id, title="a", source="upload"))
    await db_session.commit()
    return p


async def test_usage_reports_limits_and_reset(client, project, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "user_max_papers", 20)
    monkeypatch.setattr(settings, "user_chat_turns_per_day", 100)
    body = (await client.get("/v1/me/usage")).json()
    assert body["papers"] == {"used": 1, "limit": 20}
    assert body["chat_turns_today"]["used"] == 0
    assert body["chat_turns_today"]["limit"] == 100
    assert body["chat_turns_today"]["resets_at"].endswith(("T00:00:00Z", "T00:00:00+00:00"))
