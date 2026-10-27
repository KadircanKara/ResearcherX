"""Ingest status, source hash and the chunk's denormalised project_id.

SQLite `create_all` round trips. The Postgres-only half of migration
a1b2c3d4e5f7 (halfvec(768), the HNSW index) cannot run here; it is verified
against the dev database instead.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Paper,
    PaperChunkEmbedding,
    PaperStatus,
    Project,
    ProjectMember,
    User,
)
from app.db.seed import seed_users


@pytest.fixture
async def project(db_session: AsyncSession) -> Project:
    await seed_users(db_session)
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    p = Project(owner_id=you.id, title="Ingest Status Project", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_new_paper_defaults_to_pending(db_session: AsyncSession, project: Project):
    paper = Paper(project_id=project.id, title="Fresh")
    db_session.add(paper)
    await db_session.commit()
    await db_session.refresh(paper)
    assert paper.status == PaperStatus.PENDING
    assert paper.source_hash is None
    assert paper.ingest_error is None


async def test_chunk_requires_project_id(db_session: AsyncSession, project: Project):
    project_id = project.id  # the rollback below expires `project`
    paper = Paper(project_id=project_id, title="Chunked")
    db_session.add(paper)
    await db_session.flush()
    db_session.add(PaperChunkEmbedding(paper_id=paper.id, chunk_index=0, text="t", embedding="[0]"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    paper = Paper(project_id=project_id, title="Chunked again")
    db_session.add(paper)
    await db_session.flush()
    chunk = PaperChunkEmbedding(
        paper_id=paper.id, project_id=project_id, chunk_index=0, text="t", embedding="[0]"
    )
    db_session.add(chunk)
    await db_session.commit()
    await db_session.refresh(chunk)
    assert chunk.project_id == project_id


def test_status_enum_values():
    assert [s.value for s in PaperStatus] == ["pending", "ready", "failed"]


async def test_source_hash_is_unique_per_project_when_set(
    db_session: AsyncSession, project: Project
):
    """Two hashless papers coexist (manual entry has no bytes); two papers
    with one hash in one project do not."""
    db_session.add_all(
        [
            Paper(project_id=project.id, title="Manual A"),
            Paper(project_id=project.id, title="Manual B"),
            Paper(project_id=project.id, title="Upload", source_hash="a" * 64),
        ]
    )
    await db_session.commit()

    db_session.add(Paper(project_id=project.id, title="Upload again", source_hash="a" * 64))
    with pytest.raises(IntegrityError):
        await db_session.commit()
