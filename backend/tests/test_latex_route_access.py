"""One test per direction on the routes that actually change something."""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models import LatexDocumentMember, User
from app.db.seed import seed_users


@pytest_asyncio.fixture
async def users(db_session):
    """Return (you, amelia, marco) User objects."""
    await seed_users(db_session)
    await db_session.commit()
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    amelia = (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one()
    marco = (
        await db_session.execute(select(User).where(User.email == "marco@lab.io"))
    ).scalar_one()
    return you, amelia, marco


@pytest.fixture
async def shared(client, users, db_session):
    """A project the owner shared with amelia, holding one document."""
    you, amelia, _ = users
    created = await client.post(
        "/v1/projects", json={"title": "Shared"}, headers={"X-Dev-User-Id": you.id}
    )
    project_id = created.json()["id"]
    await client.post(
        f"/v1/projects/{project_id}/members",
        json={"user_id": amelia.id, "role": "member"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc = await client.post(
        f"/v1/projects/{project_id}/latex",
        json={
            "name": "paper",
            "source": "\\documentclass{article}\\begin{document}x\\end{document}",
        },
        headers={"X-Dev-User-Id": you.id},
    )
    return {"project_id": project_id, "document_id": doc.json()["id"], "you": you, "amelia": amelia}


async def test_a_member_may_read_a_document_they_have_no_grant_on(client, shared):
    r = await client.get(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}",
        headers={"X-Dev-User-Id": shared["amelia"].id},
    )
    assert r.status_code == 200
    assert r.json()["my_access"] == "viewer"


async def test_a_viewer_may_not_rename_the_document(client, shared):
    r = await client.patch(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}",
        json={"name": "renamed"},
        headers={"X-Dev-User-Id": shared["amelia"].id},
    )
    assert r.status_code == 403


async def test_a_viewer_may_not_write_a_file(client, shared):
    # Paths travel as a QUERY PARAMETER (see latex_files.py's module
    # docstring), not embedded in the URL -- `.../file?path=main.tex`, not
    # `.../files/main.tex`.
    r = await client.put(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/file",
        params={"path": "main.tex"},
        json={"content": "hacked"},
        headers={"X-Dev-User-Id": shared["amelia"].id},
    )
    assert r.status_code == 403


async def test_a_viewer_may_not_delete_the_document(client, shared):
    r = await client.delete(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}",
        headers={"X-Dev-User-Id": shared["amelia"].id},
    )
    assert r.status_code == 403


async def test_an_editor_grant_unlocks_the_same_routes(client, shared, db_session):
    db_session.add(
        LatexDocumentMember(
            document_id=shared["document_id"], user_id=shared["amelia"].id, role="editor"
        )
    )
    await db_session.commit()

    r = await client.patch(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}",
        json={"name": "renamed"},
        headers={"X-Dev-User-Id": shared["amelia"].id},
    )
    assert r.status_code == 200
    assert r.json()["my_access"] == "editor"


async def test_the_creator_sees_editor(client, shared):
    r = await client.get(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}",
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert r.json()["my_access"] == "editor"


async def test_any_member_may_create_a_document(client, shared):
    """Creation is project-scoped: a member may start their own LaTeX project,
    and `created_by` then makes them its editor."""
    r = await client.post(
        f"/v1/projects/{shared['project_id']}/latex",
        json={"name": "amelia's paper"},
        headers={"X-Dev-User-Id": shared["amelia"].id},
    )
    assert r.status_code == 201
    assert r.json()["my_access"] == "editor"
