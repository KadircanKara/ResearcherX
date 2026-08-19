"""LaTeX document routes. Membership is enforced on every one of them."""

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument, Project, ProjectMember, User
from app.db.seed import seed_users
from app.services.latex_cache import CachedBuild, LatexCache, source_hash
from app.services.latex_compiler import CompileResult, PdfPosition


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
    p = Project(owner_id=you.id, title="LaTeX API Test", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_create_and_list_documents(client: AsyncClient, you: User, project: Project):
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "main.tex", "source": "\\documentclass{article}"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert created.status_code == 201

    listed = await client.get(f"/v1/projects/{project.id}/latex", headers={"X-Dev-User-Id": you.id})
    assert [d["name"] for d in listed.json()] == ["main.tex"]


async def test_patch_saves_the_source(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    doc = LatexDocument(project_id=project.id, name="main.tex", source="old")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc.id}",
        json={"source": "new"},
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 200
    assert resp.json()["source"] == "new"


async def test_a_document_from_another_project_404s(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """Resolved the same way conversations already are: a foreign id is
    indistinguishable from a missing one."""
    other = Project(owner_id=you.id, title="Other", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    foreign = LatexDocument(project_id=other.id, name="theirs.tex", source="")
    db_session.add(foreign)
    await db_session.commit()
    await db_session.refresh(foreign)

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{foreign.id}", headers={"X-Dev-User-Id": you.id}
    )

    assert resp.status_code == 404


async def test_a_non_member_cannot_read_documents(
    client: AsyncClient, project: Project, db_session: AsyncSession, seeded
):
    stranger = (
        (await db_session.execute(select(User).where(User.email != "you@researcherx.dev")))
        .scalars()
        .first()
    )

    resp = await client.get(
        f"/v1/projects/{project.id}/latex", headers={"X-Dev-User-Id": stranger.id}
    )

    assert resp.status_code in (403, 404)


async def test_delete_removes_the_document(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    doc = LatexDocument(project_id=project.id, name="gone.tex", source="")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    resp = await client.delete(
        f"/v1/projects/{project.id}/latex/{doc.id}", headers={"X-Dev-User-Id": you.id}
    )

    assert resp.status_code == 204
    remaining = (
        await db_session.execute(select(LatexDocument).where(LatexDocument.id == doc.id))
    ).scalar_one_or_none()
    assert remaining is None


async def test_compile_stores_the_pdf_and_returns_its_hash(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    doc = LatexDocument(project_id=project.id, name="main.tex", source="\\documentclass{article}")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    result = CompileResult(ok=True, log="", pdf=b"%PDF-good", synctex_gz=b"gz")
    with (
        patch("app.api.v1.latex.compile_source", AsyncMock(return_value=result)),
        patch("app.api.v1.latex.cache", LatexCache(max_entries=4, max_bytes=10_000)) as cache,
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc.id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )
        body = resp.json()
        pdf = await client.get(
            f"/v1/projects/{project.id}/latex/{doc.id}/pdf?hash={body['pdf_hash']}",
            headers={"X-Dev-User-Id": you.id},
        )

    assert body["ok"] is True
    assert body["pdf_hash"] == source_hash(doc.source, doc.engine)
    assert pdf.content == b"%PDF-good"
    assert cache.get(body["pdf_hash"]) is not None


async def test_a_failed_compile_returns_the_log_and_no_hash(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """No hash means the client keeps showing the PDF it already has -- the
    last good PDF survives a broken edit."""
    doc = LatexDocument(project_id=project.id, name="main.tex", source="\\bogus")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    result = CompileResult(ok=False, log="! Undefined control sequence.", pdf=None, synctex_gz=None)
    with patch("app.api.v1.latex.compile_source", AsyncMock(return_value=result)):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc.id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )

    body = resp.json()
    assert body["ok"] is False
    assert body["pdf_hash"] is None
    assert "Undefined control sequence" in body["log"]


async def test_fetching_a_pdf_hash_that_is_not_cached_404s(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    doc = LatexDocument(project_id=project.id, name="main.tex", source="x")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{doc.id}/pdf?hash=deadbeef",
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 404


async def test_forward_sync_maps_a_line_to_a_page_position(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    doc = LatexDocument(project_id=project.id, name="main.tex", source="src")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    prepared = LatexCache(max_entries=4, max_bytes=10_000)
    prepared.put(
        source_hash("src", "pdflatex"),
        CachedBuild(source="src", pdf=b"%PDF", synctex_gz=b"gz", log=""),
        document_id=doc.id,
    )
    position = PdfPosition(page=1, x=36.0, y=122.0, width=100.0, height=12.0)

    with (
        patch("app.api.v1.latex.cache", prepared),
        patch("app.api.v1.latex.synctex_forward", AsyncMock(return_value=position)),
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc.id}/synctex/forward",
            json={"line": 161},
            headers={"X-Dev-User-Id": you.id},
        )

    assert resp.json() == {
        "found": True,
        "page": 1,
        "x": 36.0,
        "y": 122.0,
        "width": 100.0,
        "height": 12.0,
    }


async def test_sync_before_any_compile_reports_not_found_rather_than_erroring(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """Navigation is an enhancement: with no build cached there is nothing to
    map, and the editor must keep working."""
    doc = LatexDocument(project_id=project.id, name="main.tex", source="src")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    with patch("app.api.v1.latex.cache", LatexCache(max_entries=4, max_bytes=10_000)):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc.id}/synctex/reverse",
            json={"page": 1, "x": 36.0, "y": 122.0},
            headers={"X-Dev-User-Id": you.id},
        )

    assert resp.status_code == 200
    assert resp.json() == {"found": False, "line": None}


async def test_reverse_sync_maps_a_point_to_a_line(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    doc = LatexDocument(project_id=project.id, name="main.tex", source="src")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    prepared = LatexCache(max_entries=4, max_bytes=10_000)
    prepared.put(
        source_hash("src", "pdflatex"),
        CachedBuild(source="src", pdf=b"%PDF", synctex_gz=b"gz", log=""),
        document_id=doc.id,
    )

    with (
        patch("app.api.v1.latex.cache", prepared),
        patch("app.api.v1.latex.synctex_reverse", AsyncMock(return_value=161)),
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc.id}/synctex/reverse",
            json={"page": 1, "x": 36.0, "y": 122.0},
            headers={"X-Dev-User-Id": you.id},
        )

    assert resp.json() == {"found": True, "line": 161}
