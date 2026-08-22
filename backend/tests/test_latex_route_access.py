"""One test per direction on the routes that actually change something."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models import LatexDocumentMember, User
from app.db.seed import seed_users
from app.services.latex_compiler import CompileResult


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


# Compile, PDF, and SyncTeX moved from an editor-level guard to a
# viewer-level one in this task -- none of them writes the document, so a
# viewer reading it may use them. This pins the widening: a viewer must
# never see 403 on any of the four.
_VIEWER_OK_ROUTES = [
    ("POST", "/compile", None),
    ("GET", "/pdf", None),
    ("POST", "/synctex/forward", {"line": 1}),
    ("POST", "/synctex/reverse", {"page": 1, "x": 0.0, "y": 0.0}),
]


@pytest.mark.parametrize(
    "method,suffix,body", _VIEWER_OK_ROUTES, ids=[f"{m}{s}" for m, s, _ in _VIEWER_OK_ROUTES]
)
async def test_a_viewer_gets_a_non_403_on_every_compile_and_sync_route(
    client, shared, method: str, suffix: str, body
):
    base = f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}"
    headers = {"X-Dev-User-Id": shared["amelia"].id}

    if suffix == "/compile":
        result = CompileResult(ok=True, log="", pdf=b"%PDF-1.4", synctex_gz=b"gz", root="/tmp/rx")
        with patch("app.api.v1.latex.compile_tree", AsyncMock(return_value=result)):
            resp = await client.post(f"{base}{suffix}", headers=headers)
    elif suffix == "/pdf":
        # No build has ever been cached for this document -- a real hash
        # would require an actual compile, which is beside the point here.
        # `cache.get` misses and the route answers 404, never 403.
        resp = await client.get(f"{base}{suffix}", params={"hash": "nonexistent"}, headers=headers)
    else:
        # Nothing has been compiled either, so both SyncTeX routes take
        # their early `cache.latest_for(doc_id) is None` return -- a 200
        # with `found: false`, never a 403.
        resp = await client.post(f"{base}{suffix}", json=body, headers=headers)

    assert resp.status_code != 403
