"""LaTeX document routes. Membership is enforced on every one of them."""

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument, Project, ProjectMember, User
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
