"""Paper metadata columns: defaults and round-trip."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Paper, Project, ProjectMember, User
from app.db.seed import seed_users


@pytest.fixture(autouse=True)
async def _seed(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


@pytest.fixture
async def project(db_session: AsyncSession) -> Project:
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    p = Project(owner_id=you.id, title="Metadata Test Project", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_metadata_columns_default_to_absent(db_session: AsyncSession, project: Project):
    """A paper created without metadata records absence, not a guess."""
    paper = Paper(project_id=project.id, title="Untouched Paper")
    db_session.add(paper)
    await db_session.commit()
    await db_session.refresh(paper)

    assert paper.authors == []
    assert paper.year is None
    assert paper.venue is None
    assert paper.metadata_source == "none"


async def test_metadata_columns_round_trip(db_session: AsyncSession, project: Project):
    paper = Paper(
        project_id=project.id,
        title="Filled Paper",
        authors=["Kadircan Kara", "Evşen Yanmaz"],
        year=2024,
        venue="IEEE ICRA",
        metadata_source="crossref",
    )
    db_session.add(paper)
    await db_session.commit()
    await db_session.refresh(paper)

    assert paper.authors == ["Kadircan Kara", "Evşen Yanmaz"]
    assert paper.year == 2024
    assert paper.venue == "IEEE ICRA"
    assert paper.metadata_source == "crossref"
