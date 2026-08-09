"""Paper CRUD + ingest endpoint tests."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, User
from app.db.seed import seed_users


@pytest.fixture(autouse=True)
def no_metadata_extraction():
    """ingest() now makes an LLM call for metadata. It fails open, so tests
    pass either way — but the SDK retries the unroutable endpoint with
    backoff first, adding seconds per test for nothing."""
    with patch(
        "app.services.paper_ingest_service.apply_metadata",
        new=AsyncMock(return_value="none"),
    ):
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
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Paper Test Project", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_create_paper(client: AsyncClient, you: User, project: Project):
    # Manual source with a non-empty abstract triggers indexing on create; mock
    # embed_batch so this stays a network-free unit test.
    with patch(
        "app.services.paper_ingest_service.EmbeddingService.embed_batch",
        new=AsyncMock(return_value=[[0.0] * 768]),
    ):
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
    # Minimal valid PDF bytes (just enough for fitz to parse + get text)
    # We mock embed_batch so no real HTTP call is made
    fake_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R>>endobj\n4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 72 720 Td (Hello World) Tj ET\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000206 00000 n \ntrailer<</Size 5/Root 1 0 R>>\nstartxref\n300\n%%EOF"

    with patch(
        "app.services.paper_ingest_service.EmbeddingService.embed_batch",
        new=AsyncMock(return_value=[[0.0] * 768]),
    ):
        # Create the paper first. Manual source + non-empty abstract also
        # triggers indexing on create, so this call needs the mock too.
        r = await client.post(
            f"/v1/projects/{project.id}/papers",
            json={"title": "Ingest Me", "abstract": "x", "pdf_url": None},
            headers={"X-Dev-User-Id": you.id},
        )
        paper_id = r.json()["id"]

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


async def test_create_paper_defaults_source_to_manual(
    client: AsyncClient, you: User, project: Project
):
    resp = await client.post(
        f"/v1/projects/{project.id}/papers",
        json={"title": "No Source Given", "abstract": None, "pdf_url": None},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 201
    assert resp.json()["source"] == "manual"


async def test_create_paper_stores_explicit_source(
    client: AsyncClient, you: User, project: Project
):
    for source in ("upload", "link", "manual"):
        resp = await client.post(
            f"/v1/projects/{project.id}/papers",
            json={"title": f"Paper {source}", "source": source},
            headers={"X-Dev-User-Id": you.id},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["source"] == source


async def test_create_paper_rejects_unknown_source(
    client: AsyncClient, you: User, project: Project
):
    resp = await client.post(
        f"/v1/projects/{project.id}/papers",
        json={"title": "Bad Source", "source": "carrier-pigeon"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 422


async def test_create_manual_paper_indexes_abstract(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    from app.db.models import PaperChunkEmbedding

    with patch(
        "app.services.paper_ingest_service.EmbeddingService.embed_batch",
        new=AsyncMock(return_value=[[0.0] * 768]),
    ):
        r = await client.post(
            f"/v1/projects/{project.id}/papers",
            json={"title": "Manual", "abstract": "Only an abstract.", "source": "manual"},
            headers={"X-Dev-User-Id": you.id},
        )
    assert r.status_code == 201
    rows = await db_session.execute(
        select(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == r.json()["id"])
    )
    chunks = rows.scalars().all()
    assert len(chunks) == 1
    assert chunks[0].text == "Only an abstract."


async def test_create_upload_paper_does_not_index(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    from app.db.models import PaperChunkEmbedding

    r = await client.post(
        f"/v1/projects/{project.id}/papers",
        json={"title": "Upload", "abstract": "Extracted.", "body": "Body.", "source": "upload"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 201
    rows = await db_session.execute(
        select(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == r.json()["id"])
    )
    assert rows.scalars().all() == []


async def _make_paper(client: AsyncClient, you: User, project: Project, **fields) -> str:
    payload = {"title": "Original", **fields}
    r = await client.post(
        f"/v1/projects/{project.id}/papers",
        json=payload,
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_patch_title_on_upload_paper_succeeds(
    client: AsyncClient, you: User, project: Project
):
    pid = await _make_paper(client, you, project, source="upload", abstract="Extracted.")
    r = await client.patch(
        f"/v1/projects/{project.id}/papers/{pid}",
        json={"title": "Corrected Title"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Corrected Title"


async def test_patch_abstract_on_upload_paper_is_rejected(
    client: AsyncClient, you: User, project: Project
):
    pid = await _make_paper(client, you, project, source="upload", abstract="Extracted.")
    r = await client.patch(
        f"/v1/projects/{project.id}/papers/{pid}",
        json={"abstract": "Tampered."},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 422
    check = await client.get(f"/v1/projects/{project.id}/papers", headers={"X-Dev-User-Id": you.id})
    assert [p for p in check.json() if p["id"] == pid][0]["abstract"] == "Extracted."


async def test_patch_body_on_link_paper_is_rejected(
    client: AsyncClient, you: User, project: Project
):
    pid = await _make_paper(client, you, project, source="link", body="Fetched body.")
    r = await client.patch(
        f"/v1/projects/{project.id}/papers/{pid}",
        json={"body": "Tampered."},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 422


async def test_patch_body_on_manual_paper_reindexes(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    from app.db.models import PaperChunkEmbedding

    with patch(
        "app.services.paper_ingest_service.EmbeddingService.embed_batch",
        new=AsyncMock(
            side_effect=lambda texts, task_type="RETRIEVAL_DOCUMENT": [[0.0] * 768 for _ in texts]
        ),
    ):
        pid = await _make_paper(client, you, project, source="manual", body="First body.")
        r = await client.patch(
            f"/v1/projects/{project.id}/papers/{pid}",
            json={"body": "Replacement body text."},
            headers={"X-Dev-User-Id": you.id},
        )
    assert r.status_code == 200
    rows = await db_session.execute(
        select(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == pid)
    )
    texts = [c.text for c in rows.scalars().all()]
    assert texts == ["Replacement body text."]


async def test_patch_unchanged_body_skips_reindex(client: AsyncClient, you: User, project: Project):
    with patch(
        "app.services.paper_ingest_service.EmbeddingService.embed_batch",
        new=AsyncMock(
            side_effect=lambda texts, task_type="RETRIEVAL_DOCUMENT": [[0.0] * 768 for _ in texts]
        ),
    ) as spy:
        pid = await _make_paper(client, you, project, source="manual", body="Same body.")
        calls_after_create = spy.call_count
        r = await client.patch(
            f"/v1/projects/{project.id}/papers/{pid}",
            json={"title": "New Title", "body": "Same body."},
            headers={"X-Dev-User-Id": you.id},
        )
        assert r.status_code == 200
        assert spy.call_count == calls_after_create, "unchanged body must not re-embed"


async def test_patch_requires_editor(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    pid = await _make_paper(client, you, project, source="manual")
    viewer = (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one()
    db_session.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
    await db_session.commit()

    r = await client.patch(
        f"/v1/projects/{project.id}/papers/{pid}",
        json={"title": "Nope"},
        headers={"X-Dev-User-Id": viewer.id},
    )
    assert r.status_code == 403


async def test_patch_body_rolls_back_when_embedding_fails(
    client: AsyncClient, you: User, project: Project
):
    with patch(
        "app.services.paper_ingest_service.EmbeddingService.embed_batch",
        new=AsyncMock(
            side_effect=lambda texts, task_type="RETRIEVAL_DOCUMENT": [[0.0] * 768 for _ in texts]
        ),
    ):
        pid = await _make_paper(client, you, project, source="manual", body="ORIGINAL BODY")

    with patch(
        "app.services.paper_ingest_service.EmbeddingService.embed_batch",
        new=AsyncMock(side_effect=RuntimeError("embedding provider down")),
    ):
        resp = await client.patch(
            f"/v1/projects/{project.id}/papers/{pid}",
            json={"body": "REPLACEMENT BODY"},
            headers={"X-Dev-User-Id": you.id},
        )
    assert resp.status_code >= 500

    listing = await client.get(
        f"/v1/projects/{project.id}/papers", headers={"X-Dev-User-Id": you.id}
    )
    stored = [p for p in listing.json() if p["id"] == pid][0]
    assert stored["body"] == "ORIGINAL BODY", "text must not persist when indexing failed"


async def test_patch_null_title_is_rejected(client: AsyncClient, you: User, project: Project):
    pid = await _make_paper(client, you, project, source="manual")
    r = await client.patch(
        f"/v1/projects/{project.id}/papers/{pid}",
        json={"title": None},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 422
    check = await client.get(f"/v1/projects/{project.id}/papers", headers={"X-Dev-User-Id": you.id})
    assert [p for p in check.json() if p["id"] == pid][0]["title"] == "Original"


async def test_patch_paper_from_another_project_404s(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    pid = await _make_paper(client, you, project, source="manual")
    other = Project(owner_id=you.id, title="Other", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=other.id, user_id=you.id, role="owner"))
    await db_session.commit()

    r = await client.patch(
        f"/v1/projects/{other.id}/papers/{pid}",
        json={"title": "Nope"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 404
