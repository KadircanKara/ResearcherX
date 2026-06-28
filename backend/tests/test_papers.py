"""Paper CRUD + ingest endpoint tests."""

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, User
from app.db.seed import seed_users


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
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Paper Test Project", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_create_paper(client: AsyncClient, you: User, project: Project):
    resp = await client.post(
        f"/v1/projects/{project.id}/papers",
        json={"title": "Test Paper", "abstract": "An abstract.", "pdf_url": None},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test Paper"
    assert data["project_id"] == project.id


async def test_list_papers(client: AsyncClient, you: User, project: Project):
    await client.post(
        f"/v1/projects/{project.id}/papers",
        json={"title": "Paper A", "abstract": None, "pdf_url": None},
        headers={"X-Dev-User-Id": you.id},
    )
    resp = await client.get(
        f"/v1/projects/{project.id}/papers",
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_ingest_paper(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    # Create the paper first
    r = await client.post(
        f"/v1/projects/{project.id}/papers",
        json={"title": "Ingest Me", "abstract": "x", "pdf_url": None},
        headers={"X-Dev-User-Id": you.id},
    )
    paper_id = r.json()["id"]

    # Minimal valid PDF bytes (just enough for fitz to parse + get text)
    # We mock embed_batch so no real HTTP call is made
    fake_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R>>endobj\n4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 72 720 Td (Hello World) Tj ET\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000206 00000 n \ntrailer<</Size 5/Root 1 0 R>>\nstartxref\n300\n%%EOF"

    with patch(
        "app.services.paper_ingest_service.EmbeddingService.embed_batch",
        new=AsyncMock(return_value=[[0.0] * 768]),
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/papers/{paper_id}/ingest",
            content=fake_pdf,
            headers={
                "X-Dev-User-Id": you.id,
                "Content-Type": "application/octet-stream",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["chunks_stored"] >= 1

    # Verify chunks stored in DB
    from app.db.models import PaperChunkEmbedding

    chunks = (
        (
            await db_session.execute(
                select(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == paper_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(chunks) == data["chunks_stored"]


@pytest_asyncio.fixture
async def non_member(db_session: AsyncSession, seeded) -> User:
    """A seeded user who is NOT added to the project."""
    return (await db_session.execute(select(User).where(User.email == "marco@lab.io"))).scalar_one()


async def test_paper_requires_member(client: AsyncClient, project: Project, non_member: User):
    # A user who is not a member of the project should get 404
    resp = await client.post(
        f"/v1/projects/{project.id}/papers",
        json={"title": "Blocked", "abstract": None, "pdf_url": None},
        headers={"X-Dev-User-Id": non_member.id},
    )
    assert resp.status_code == 404
