"""`paper_summaries`: defaults and the cascade from `papers`.

SQLite `create_all` round trips; the Postgres-only `doc_embedding vector(768)`
column is added by migration c3d4e5f6a7b9 and verified against the dev DB.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Paper, PaperSummary, Project, ProjectMember, User
from app.db.seed import seed_users
from app.services.paper_summary_prompt import SUMMARY_VER


@pytest.fixture
async def project(db_session: AsyncSession) -> Project:
    await seed_users(db_session)
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    p = Project(owner_id=you.id, title="Summary Project", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_summary_row_defaults(db_session: AsyncSession, project: Project):
    paper = Paper(project_id=project.id, title="Summarised")
    db_session.add(paper)
    await db_session.flush()
    db_session.add(PaperSummary(paper_id=paper.id))
    await db_session.commit()

    row = (
        await db_session.execute(select(PaperSummary).where(PaperSummary.paper_id == paper.id))
    ).scalar_one()
    assert row.status == "pending"
    assert row.keywords == []
    assert row.summary_ver == SUMMARY_VER
    assert row.model == ""
    assert row.summary is None
    assert row.doc_embedding is None
    assert row.summary_model is None
    assert row.source is None
    assert row.batch_id is None
    assert row.error is None
    assert row.created_at is not None and row.updated_at is not None


async def test_summary_row_cascades_with_paper(db_session: AsyncSession, project: Project):
    paper = Paper(project_id=project.id, title="Doomed")
    db_session.add(paper)
    await db_session.flush()
    paper_id = paper.id
    db_session.add(PaperSummary(paper_id=paper_id, summary="s", status="ready"))
    await db_session.commit()

    await db_session.delete(paper)
    await db_session.commit()
    db_session.expunge_all()

    gone = (
        await db_session.execute(select(PaperSummary).where(PaperSummary.paper_id == paper_id))
    ).scalar_one_or_none()
    assert gone is None
