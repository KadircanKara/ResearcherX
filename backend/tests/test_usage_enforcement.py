"""Each limit refuses the (N+1)th action with a fixed 429, and 0 turns it off."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.db.models import (
    ChatConversation,
    ChatMessage,
    Paper,
    Project,
    ProjectMember,
    UsageEvent,
    User,
)
from app.services.usage_service import CHAT_TURN
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


async def _conversation(db, project):
    conv = ChatConversation(project_id=project.id, title="c", created_by=project.owner_id)
    db.add(conv)
    await db.commit()
    return conv


async def _seed_turns(db, user, n, when):
    for _ in range(n):
        db.add(UsageEvent(user_id=user.id, kind=CHAT_TURN, created_at=when))
    await db.commit()


async def _count(db, model):
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def test_the_101st_turn_today_is_refused_and_yesterday_does_not_count(
    client, db_session, you, project, limits
):
    limits(user_chat_turns_per_day=100)
    now = datetime.now(timezone.utc)
    conv = await _conversation(db_session, project)
    await _seed_turns(db_session, you, 100, now)
    await _seed_turns(db_session, you, 500, now - timedelta(days=1, hours=1))
    msgs, events = await _count(db_session, ChatMessage), await _count(db_session, UsageEvent)
    r = await client.post(
        f"/v1/projects/{project.id}/conversations/{conv.id}/messages", json={"content": "hi"}
    )
    assert r.status_code == 429
    assert r.json() == {"detail": "Daily chat limit reached (100). Resets at 00:00 UTC."}
    # A refused turn leaves no message and no metering event behind.
    assert await _count(db_session, ChatMessage) == msgs
    assert await _count(db_session, UsageEvent) == events


async def test_deleting_the_conversation_does_not_refund_turns(
    client, db_session, you, project, limits
):
    limits(user_chat_turns_per_day=2)
    conv = await _conversation(db_session, project)
    await _seed_turns(db_session, you, 2, datetime.now(timezone.utc))
    d = await client.delete(f"/v1/projects/{project.id}/conversations/{conv.id}")
    assert d.status_code in (200, 204)
    fresh = await _conversation(db_session, project)
    r = await client.post(
        f"/v1/projects/{project.id}/conversations/{fresh.id}/messages", json={"content": "hi"}
    )
    assert r.status_code == 429


async def test_the_global_backstop_refuses_everyone_and_survives_deletes(
    client, db_session, you, project, limits
):
    limits(global_chat_turns_per_day=3)
    conv = await _conversation(db_session, project)
    await _seed_turns(db_session, you, 3, datetime.now(timezone.utc))
    await client.delete(f"/v1/projects/{project.id}/conversations/{conv.id}")
    fresh = await _conversation(db_session, project)
    r = await client.post(
        f"/v1/projects/{project.id}/conversations/{fresh.id}/messages", json={"content": "hi"}
    )
    assert r.status_code == 429
    assert r.json() == {"detail": "The demo is at capacity for today. Try again after 00:00 UTC."}


async def test_suggest_title_refuses_chunked_body_without_content_length(client, project, limits):
    limits(paper_pdf_max_bytes=10)

    async def body():
        for _ in range(3):
            yield b"xxxx"

    r = await client.post(f"/v1/projects/{project.id}/papers/suggest-title", content=body())
    assert r.status_code == 413


async def _paper(db, project):
    p = Paper(project_id=project.id, title="p", source="upload")
    db.add(p)
    await db.commit()
    return p


async def test_the_3rd_ingest_today_is_refused_before_any_paid_call(
    client, db_session, you, project, limits
):
    from app.services.usage_service import INGEST

    limits(user_ingests_per_day=2)
    paper = await _paper(db_session, project)
    now = datetime.now(timezone.utc)
    for _ in range(2):
        db_session.add(UsageEvent(user_id=you.id, kind=INGEST, created_at=now))
    # Yesterday's ingests never count against today.
    for _ in range(50):
        db_session.add(
            UsageEvent(user_id=you.id, kind=INGEST, created_at=now - timedelta(days=1, hours=1))
        )
    await db_session.commit()
    events = await _count(db_session, UsageEvent)
    paid = AsyncMock(return_value=1)
    with patch("app.services.paper_ingest_service.ingest", new=paid):
        r = await client.post(
            f"/v1/projects/{project.id}/papers/{paper.id}/ingest", content=b"%PDF"
        )
    assert r.status_code == 429
    assert r.json() == {"detail": "Daily upload limit reached (2). Resets at 00:00 UTC."}
    paid.assert_not_called()
    assert await _count(db_session, UsageEvent) == events


async def test_allowed_ingests_are_metered_and_yesterday_frees_the_slot(
    client, db_session, you, project, limits
):
    from app.services.usage_service import INGEST

    limits(user_ingests_per_day=1)
    paper = await _paper(db_session, project)
    db_session.add(
        UsageEvent(
            user_id=you.id, kind=INGEST, created_at=datetime.now(timezone.utc) - timedelta(days=2)
        )
    )
    await db_session.commit()
    paid = AsyncMock(return_value=3)
    with patch("app.services.paper_ingest_service.ingest", new=paid):
        ok = await client.post(
            f"/v1/projects/{project.id}/papers/{paper.id}/ingest", content=b"%PDF"
        )
        again = await client.post(
            f"/v1/projects/{project.id}/papers/{paper.id}/ingest", content=b"%PDF"
        )
    assert ok.status_code == 200
    assert again.status_code == 429
    assert paid.call_count == 1


def test_a_negative_ingest_limit_is_refused_by_settings():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(user_ingests_per_day=-1)


async def test_an_allowed_chat_turn_writes_exactly_one_chat_turn_event(
    client, db_session, project, limits
):
    from app.api.v1 import chat as chat_api

    limits(user_chat_turns_per_day=2)
    conv = await _conversation(db_session, project)

    async def fake_respond(*_a, **_k):
        yield {"event": "done", "data": "{}"}

    async def turn_events():
        return int(
            await db_session.scalar(
                select(func.count()).select_from(UsageEvent).where(UsageEvent.kind == CHAT_TURN)
            )
            or 0
        )

    url = f"/v1/projects/{project.id}/conversations/{conv.id}/messages"
    with patch.object(chat_api.chat_service, "respond", new=fake_respond):
        for expected in (1, 2):
            r = await client.post(url, json={"content": "hi"})
            assert r.status_code == 200
            assert await turn_events() == expected
        third = await client.post(url, json={"content": "hi"})
    assert third.status_code == 429
    assert await turn_events() == 2
