"""Tests for research runs ↔ project binding (L1)."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, ResearchRun, User
from app.db.seed import seed_users


# ── shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def no_background_pipeline():
    """Stop POST /v1/research from spawning a real research pipeline.

    These tests assert on the HTTP contract only — 201, and that the run is
    bound to the right project. They never inspect pipeline output. But the
    endpoint spawns `service.run_async(run.id)` as a background task, which
    then grinds through planner/searcher calls against the deliberately
    unroutable LLM_BASE_URL, retrying with backoff while holding a DB
    connection.

    That connection kept the sqlite file locked past the end of the test, so
    the next test's `drop_all` waited out the full `?timeout=30` busy timeout
    and errored with "database is locked" — 31s in teardown plus 31s in the
    following setup, and one erroring test per run. It looked like a random
    flake because which test got hit depended on timing; it failed on CI on
    both a run and a re-run, and was reproducible here.

    Patching the spawn removes the contention at its source and takes the file
    from ~63s to under a second.
    """
    with patch("app.api.v1.research.service.run_async", new=AsyncMock(return_value=None)):
        yield


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


# ── Task 4: GET /v1/projects/{id}/runs ───────────────────────────────────────


async def test_list_project_runs(client, you: User, project: Project, db_session: AsyncSession):
    from datetime import timedelta, timezone
    from datetime import datetime as dt

    base = dt.now(timezone.utc)
    db_session.add(
        ResearchRun(
            question="older question here",
            project_id=project.id,
            created_at=base - timedelta(hours=1),
        )
    )
    db_session.add(
        ResearchRun(
            question="newer question here",
            project_id=project.id,
            created_at=base,
        )
    )
    await db_session.commit()

    r = await client.get(
        f"/v1/projects/{project.id}/runs",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["question"] == "newer question here"
    assert data[1]["question"] == "older question here"
    assert data[0]["project_id"] == project.id


async def test_list_project_runs_excludes_other_projects(
    client, you: User, project: Project, db_session: AsyncSession
):
    other = Project(owner_id=you.id, title="Other", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=other.id, user_id=you.id, role="owner"))
    db_session.add(ResearchRun(question="belongs to other project", project_id=other.id))
    db_session.add(ResearchRun(question="belongs to this project", project_id=project.id))
    await db_session.commit()

    r = await client.get(
        f"/v1/projects/{project.id}/runs",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["question"] == "belongs to this project"


async def test_list_project_runs_non_member(client, amelia: User, project: Project):
    r = await client.get(
        f"/v1/projects/{project.id}/runs",
        headers={"X-Dev-User-Id": amelia.id},
    )
    assert r.status_code == 404


async def test_member_can_list_runs(
    client, amelia: User, project: Project, db_session: AsyncSession
):
    """Was test_viewer_can_list_runs: `viewer` is a retired role and no
    longer ranks (see test_permissions.py) — a project member does now."""
    db_session.add(ProjectMember(project_id=project.id, user_id=amelia.id, role="member"))
    db_session.add(ResearchRun(question="member can see this run", project_id=project.id))
    await db_session.commit()

    r = await client.get(
        f"/v1/projects/{project.id}/runs",
        headers={"X-Dev-User-Id": amelia.id},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1
