"""Tests for research runs ↔ project binding (L1)."""

import pytest_asyncio
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
    from sqlalchemy import select
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def amelia(db_session: AsyncSession, seeded):
    from sqlalchemy import select
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
