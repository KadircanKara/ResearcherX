"""Tests for research runs ↔ project binding (L1)."""

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, ResearchRun, User
from app.db.seed import seed_users


# ── shared fixtures ──────────────────────────────────────────────────────────


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
async def amelia(db_session: AsyncSession, seeded):
    return (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Test Project", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


# ── Task 1: model has project_id ─────────────────────────────────────────────


async def test_run_model_has_project_id(db_session: AsyncSession, project: Project):
    run = ResearchRun(question="test question here", project_id=project.id)
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    assert run.project_id == project.id


async def test_run_project_id_nullable(db_session: AsyncSession):
    run = ResearchRun(question="anonymous question ok")
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    assert run.project_id is None


# ── Task 2: schemas + service ────────────────────────────────────────────────


async def test_run_out_includes_project_id(db_session: AsyncSession, project: Project):
    from app.schemas.research import RunOut

    run = ResearchRun(question="schema test question", project_id=project.id)
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    out = RunOut(
        id=run.id,
        question=run.question,
        status=str(run.status),
        report=run.report,
        error=run.error,
        project_id=run.project_id,
        created_at=run.created_at,
        steps=[],
    )
    assert out.project_id == project.id


async def test_service_create_with_project_id(db_session: AsyncSession, project: Project):
    from app.services.research_service import ResearchService

    svc = ResearchService()
    run = await svc.create(db_session, "what is machine learning here", project.id)
    assert run.project_id == project.id


async def test_service_create_without_project_id(db_session: AsyncSession):
    from app.services.research_service import ResearchService

    svc = ResearchService()
    run = await svc.create(db_session, "what is machine learning here")
    assert run.project_id is None


# ── Task 3: POST /research project guard ─────────────────────────────────────


async def test_create_run_with_project_id_as_member(client, you: User, project: Project):
    r = await client.post(
        "/v1/research",
        json={"question": "what is quantum computing", "project_id": project.id},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["project_id"] == project.id


async def test_create_run_without_project_id_is_anonymous(client):
    r = await client.post(
        "/v1/research",
        json={"question": "what is quantum computing"},
    )
    assert r.status_code == 201
    assert r.json()["project_id"] is None


async def test_create_run_with_project_id_no_identity(client, project: Project):
    # No X-Dev-User-Id header → get_current_user_optional returns None → 401
    r = await client.post(
        "/v1/research",
        json={"question": "what is quantum computing", "project_id": project.id},
    )
    assert r.status_code == 401


async def test_create_run_with_project_id_non_member(client, amelia: User, project: Project):
    # amelia is not in the project
    r = await client.post(
        "/v1/research",
        json={"question": "what is quantum computing", "project_id": project.id},
        headers={"X-Dev-User-Id": amelia.id},
    )
    assert r.status_code == 404
