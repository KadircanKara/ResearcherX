"""Metadata source precedence and persistence."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Paper, Project, ProjectMember, User
from app.db.seed import seed_users
from app.services.paper_metadata_service import (
    SOURCE_CROSSREF,
    SOURCE_LLM,
    SOURCE_NONE,
    apply_metadata,
    resolve_metadata,
)
from app.services.title_extraction_service import PaperMeta

CROSSREF_META = PaperMeta(
    title="A Published Paper", authors=["Ada Lovelace"], year=2019, venue="ICRA"
)
LLM_META = PaperMeta(title="A Preprint", authors=["Kadircan Kara", "Evşen Yanmaz"])
DOI_URL = "https://doi.org/10.1109/TRO.2024.1234"


@pytest.fixture(autouse=True)
async def _seed(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


@pytest.fixture
async def paper(db_session: AsyncSession) -> Paper:
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    project = Project(owner_id=you.id, title="P", topic_keywords=[])
    db_session.add(project)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=you.id, role="owner"))
    p = Paper(project_id=project.id, title="A Paper")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_crossref_wins_when_it_names_authors():
    with (
        patch(
            "app.services.paper_metadata_service.fetch_crossref_meta",
            new=AsyncMock(return_value=CROSSREF_META),
        ),
        patch(
            "app.services.paper_metadata_service.extract_metadata_from_text",
            new=AsyncMock(side_effect=AssertionError("LLM must not run")),
        ),
    ):
        meta, source = await resolve_metadata("some text", DOI_URL)
    assert source == SOURCE_CROSSREF
    assert meta.authors == ["Ada Lovelace"]
    assert meta.year == 2019


async def test_crossref_without_authors_falls_through_to_the_llm():
    """An authoritative-looking label over an empty answer is worse than the LLM's."""
    with (
        patch(
            "app.services.paper_metadata_service.fetch_crossref_meta",
            new=AsyncMock(return_value=PaperMeta(title="Only A Title")),
        ),
        patch(
            "app.services.paper_metadata_service.extract_metadata_from_text",
            new=AsyncMock(return_value=LLM_META),
        ),
    ):
        meta, source = await resolve_metadata("some text", DOI_URL)
    assert source == SOURCE_LLM
    assert meta.authors == ["Kadircan Kara", "Evşen Yanmaz"]


async def test_no_url_means_no_crossref_call():
    with (
        patch(
            "app.services.paper_metadata_service.fetch_crossref_meta",
            new=AsyncMock(side_effect=AssertionError("Crossref must not run")),
        ),
        patch(
            "app.services.paper_metadata_service.extract_metadata_from_text",
            new=AsyncMock(return_value=LLM_META),
        ),
    ):
        _, source = await resolve_metadata("some text", None)
    assert source == SOURCE_LLM


async def test_url_without_a_doi_means_no_crossref_call():
    with (
        patch(
            "app.services.paper_metadata_service.fetch_crossref_meta",
            new=AsyncMock(side_effect=AssertionError("Crossref must not run")),
        ),
        patch(
            "app.services.paper_metadata_service.extract_metadata_from_text",
            new=AsyncMock(return_value=LLM_META),
        ),
    ):
        _, source = await resolve_metadata("some text", "https://arxiv.org/pdf/2409.03245")
    assert source == SOURCE_LLM


async def test_empty_extraction_records_none_not_llm():
    """`llm` must mean 'the LLM produced something', so absence stays distinguishable."""
    with patch(
        "app.services.paper_metadata_service.extract_metadata_from_text",
        new=AsyncMock(return_value=PaperMeta(title="Title only")),
    ):
        _, source = await resolve_metadata("some text", None)
    assert source == SOURCE_NONE


async def test_apply_metadata_persists_onto_the_paper(db_session: AsyncSession, paper: Paper):
    with patch(
        "app.services.paper_metadata_service.extract_metadata_from_text",
        new=AsyncMock(return_value=LLM_META),
    ):
        source = await apply_metadata(db_session, paper.id, "some text")
    assert source == SOURCE_LLM

    await db_session.refresh(paper)
    assert paper.authors == ["Kadircan Kara", "Evşen Yanmaz"]
    assert paper.year is None
    assert paper.venue is None
    assert paper.metadata_source == SOURCE_LLM


async def test_apply_metadata_never_raises(db_session: AsyncSession, paper: Paper):
    """Metadata is an enhancement — a failure must not fail the ingest around it."""
    with patch(
        "app.services.paper_metadata_service.resolve_metadata",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        source = await apply_metadata(db_session, paper.id, "some text")
    assert source == SOURCE_NONE

    await db_session.refresh(paper)
    assert paper.metadata_source == SOURCE_NONE


async def test_apply_metadata_on_a_missing_paper_is_a_no_op(db_session: AsyncSession):
    with patch(
        "app.services.paper_metadata_service.extract_metadata_from_text",
        new=AsyncMock(return_value=LLM_META),
    ):
        assert await apply_metadata(db_session, "no-such-paper", "some text") == SOURCE_NONE
