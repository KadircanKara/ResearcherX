"""Tests for GET /projects/{id}/papers/{paper_id}/chunks/{chunk_index}.

Reads the `text` column only — no pgvector needed, so these run on sqlite
like the rest of the suite.
"""

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Paper, PaperChunkEmbedding, Project, ProjectMember, User
from app.db.seed import seed_users


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


@pytest_asyncio.fixture
async def you(db_session: AsyncSession, seeded) -> User:
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Chunk API Test", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest_asyncio.fixture
async def paper(db_session: AsyncSession, project: Project) -> Paper:
    """A paper with two chunks under the configured model, plus one stale row
    written under a different model at the same chunk_index."""
    p = Paper(project_id=project.id, title="Cooperative Search", source="upload")
    db_session.add(p)
    await db_session.flush()
    db_session.add_all(
        [
            PaperChunkEmbedding(
                paper_id=p.id,
                chunk_index=0,
                text="The first chunk about connectivity.",
                embedding="[0.0]",
                model=settings.embedding_model,
            ),
            PaperChunkEmbedding(
                paper_id=p.id,
                chunk_index=1,
                text="The reward is a weighted sum.",
                embedding="[0.0]",
                model=settings.embedding_model,
            ),
            PaperChunkEmbedding(
                paper_id=p.id,
                chunk_index=7,
                text="STALE text from an old model.",
                embedding="[0.0]",
                model="some-other-model",
            ),
        ]
    )
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_returns_full_chunk_text(
    client: AsyncClient, you: User, project: Project, paper: Paper
):
    r = await client.get(
        f"/v1/projects/{project.id}/papers/{paper.id}/chunks/1",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["chunk_index"] == 1
    assert body["text"] == "The reward is a weighted sum."
    assert body["paper_title"] == "Cooperative Search"


async def test_unknown_chunk_index_is_404(
    client: AsyncClient, you: User, project: Project, paper: Paper
):
    r = await client.get(
        f"/v1/projects/{project.id}/papers/{paper.id}/chunks/999",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 404


async def test_chunk_from_a_different_embedding_model_is_not_returned(
    client: AsyncClient, you: User, project: Project, paper: Paper
):
    """Chunk indices only mean something within one model's chunking. A stale
    row must 404, not leak text from a previous vector space."""
    r = await client.get(
        f"/v1/projects/{project.id}/papers/{paper.id}/chunks/7",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 404


async def test_paper_from_another_project_is_404(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project, paper: Paper
):
    other = Project(owner_id=you.id, title="Other Project", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=other.id, user_id=you.id, role="owner"))
    await db_session.commit()

    r = await client.get(
        f"/v1/projects/{other.id}/papers/{paper.id}/chunks/1",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 404


async def test_non_member_is_refused(
    client: AsyncClient, db_session: AsyncSession, project: Project, paper: Paper, seeded
):
    outsider = (
        await db_session.execute(select(User).where(User.email == "marco@lab.io"))
    ).scalar_one()
    r = await client.get(
        f"/v1/projects/{project.id}/papers/{paper.id}/chunks/1",
        headers={"X-Dev-User-Id": outsider.id},
    )
    assert r.status_code == 404
