"""Tests for POST /projects/{id}/papers/{paper_id}/ingest-from-url."""

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, User
from app.db.seed import seed_users
from app.services.paper_fetch_service import PaywallError

_FAKE_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 72 720 Td (Hello World) Tj ET\n"
    b"endstream\nendobj\nxref\n0 5\n"
    b"0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n"
    b"0000000115 00000 n \n0000000206 00000 n \n"
    b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n300\n%%EOF"
)


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
    p = Project(owner_id=you.id, title="URL Ingest Test", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest_asyncio.fixture
async def paper(client: AsyncClient, you: User, project: Project) -> dict:
    r = await client.post(
        f"/v1/projects/{project.id}/papers",
        json={"title": "URL Paper", "abstract": None, "pdf_url": None},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 201
    return r.json()


async def test_ingest_from_url_success(
    client: AsyncClient, you: User, project: Project, paper: dict
):
    with (
        patch(
            "app.services.paper_fetch_service.fetch_pdf",
            new=AsyncMock(return_value=_FAKE_PDF),
        ),
        patch(
            "app.services.paper_ingest_service.EmbeddingService.embed_batch",
            new=AsyncMock(return_value=[[0.0] * 768]),
        ),
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/papers/{paper['id']}/ingest-from-url",
            json={"url": "https://example.com/paper.pdf"},
            headers={"X-Dev-User-Id": you.id},
        )
    assert resp.status_code == 200
    assert resp.json()["chunks_stored"] >= 1


async def test_ingest_from_url_paywalled(
    client: AsyncClient, you: User, project: Project, paper: dict
):
    with patch(
        "app.services.paper_fetch_service.fetch_pdf",
        new=AsyncMock(side_effect=PaywallError("blocked")),
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/papers/{paper['id']}/ingest-from-url",
            json={"url": "https://paywalled.com/paper"},
            headers={"X-Dev-User-Id": you.id},
        )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "paywalled"
    assert "Upload the PDF directly" in detail["message"]


async def test_ingest_from_url_requires_membership(
    client: AsyncClient, project: Project, db_session: AsyncSession, seeded
):
    viewer = (
        await db_session.execute(select(User).where(User.email == "marco@lab.io"))
    ).scalar_one()
    # marco is not a member — expect 404 (require_member raises 404 for non-members)
    resp = await client.post(
        f"/v1/projects/{project.id}/papers/fake-id/ingest-from-url",
        json={"url": "https://example.com/paper.pdf"},
        headers={"X-Dev-User-Id": viewer.id},
    )
    assert resp.status_code == 404
