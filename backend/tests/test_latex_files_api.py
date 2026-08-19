"""File tree routes. Membership is enforced on every one of them, and every
service-level refusal maps to a status code a client can act on."""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import LatexDocument, Project, ProjectMember, User
from app.db.seed import seed_users


@pytest_asyncio.fixture
async def you(db_session: AsyncSession) -> User:
    await seed_users(db_session)
    await db_session.commit()
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Files API", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest_asyncio.fixture
async def document(db_session: AsyncSession, project: Project) -> LatexDocument:
    doc = LatexDocument(project_id=project.id, name="paper", source="")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)
    return doc


def _h(user: User) -> dict:
    return {"X-Dev-User-Id": user.id}


async def test_putting_a_text_file_then_reading_it_back(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    put = await client.put(
        f"{base}/file",
        params={"path": "chapters/intro.tex"},
        json={"content": "\\section{I}"},
        headers=_h(you),
    )
    assert put.status_code == 200

    got = await client.get(f"{base}/file", params={"path": "chapters/intro.tex"}, headers=_h(you))
    assert got.status_code == 200
    assert got.json() == {"path": "chapters/intro.tex", "content": "\\section{I}"}


async def test_the_tree_lists_paths_with_the_quota(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    await client.put(
        f"{base}/file", params={"path": "b.tex"}, json={"content": "22"}, headers=_h(you)
    )
    await client.put(
        f"{base}/file", params={"path": "a.tex"}, json={"content": "1"}, headers=_h(you)
    )

    tree = await client.get(f"{base}/files", headers=_h(you))
    body = tree.json()
    assert [f["path"] for f in body["files"]] == ["a.tex", "b.tex"]
    assert body["used_bytes"] == 3
    assert body["max_bytes"] > 0


async def test_a_binary_upload_round_trips_its_bytes(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    png = b"\x89PNG\r\n\x1a\n\x00\x01"
    up = await client.post(
        f"{base}/file/binary", params={"path": "figures/f.png"}, content=png, headers=_h(you)
    )
    assert up.status_code == 200
    assert up.json()["is_binary"] is True

    got = await client.get(f"{base}/file", params={"path": "figures/f.png"}, headers=_h(you))
    assert got.status_code == 200
    assert got.content == png
    assert got.headers["content-type"] == "application/octet-stream"


async def test_a_traversal_path_is_a_422_naming_the_path(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    resp = await client.put(
        f"{base}/file",
        params={"path": "../../etc/passwd"},
        json={"content": "x"},
        headers=_h(you),
    )
    assert resp.status_code == 422
    assert "../../etc/passwd" in str(resp.json()["detail"])


async def test_reading_a_missing_file_is_a_404(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    resp = await client.get(f"{base}/file", params={"path": "ghost.tex"}, headers=_h(you))
    assert resp.status_code == 404


async def test_deleting_a_file_removes_it_from_the_tree(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    await client.put(
        f"{base}/file", params={"path": "a.tex"}, json={"content": "a"}, headers=_h(you)
    )

    gone = await client.delete(f"{base}/file", params={"path": "a.tex"}, headers=_h(you))
    assert gone.status_code == 204

    tree = await client.get(f"{base}/files", headers=_h(you))
    assert tree.json()["files"] == []


async def test_deleting_the_main_file_is_a_409_and_leaves_the_tree_intact(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    """Refused BEFORE anything is removed -- the next compile would otherwise
    fail with an unactionable "file not found" from latexmk."""
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    await client.put(
        f"{base}/file", params={"path": "main.tex"}, json={"content": "a"}, headers=_h(you)
    )

    resp = await client.delete(f"{base}/file", params={"path": "main.tex"}, headers=_h(you))
    assert resp.status_code == 409

    tree = await client.get(f"{base}/files", headers=_h(you))
    assert [f["path"] for f in tree.json()["files"]] == ["main.tex"]


async def test_deleting_the_main_file_by_a_denormalized_path_is_still_a_409(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    """The guard compares NORMALIZED paths. Comparing raw strings would let
    `./main.tex` walk straight past it."""
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    await client.put(
        f"{base}/file", params={"path": "main.tex"}, json={"content": "a"}, headers=_h(you)
    )

    resp = await client.delete(f"{base}/file", params={"path": "./main.tex"}, headers=_h(you))
    assert resp.status_code == 409


async def test_deleting_a_missing_file_is_a_404(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    resp = await client.delete(f"{base}/file", params={"path": "ghost.tex"}, headers=_h(you))
    assert resp.status_code == 404


async def test_renaming_moves_a_file(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    await client.put(
        f"{base}/file", params={"path": "a.tex"}, json={"content": "body"}, headers=_h(you)
    )

    resp = await client.post(
        f"{base}/file/rename", json={"from": "a.tex", "to": "chapters/b.tex"}, headers=_h(you)
    )
    assert resp.status_code == 200
    assert resp.json()["path"] == "chapters/b.tex"


async def test_renaming_onto_an_occupied_path_is_a_409(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    await client.put(
        f"{base}/file", params={"path": "a.tex"}, json={"content": "a"}, headers=_h(you)
    )
    await client.put(
        f"{base}/file", params={"path": "b.tex"}, json={"content": "b"}, headers=_h(you)
    )

    resp = await client.post(
        f"{base}/file/rename", json={"from": "a.tex", "to": "b.tex"}, headers=_h(you)
    )
    assert resp.status_code == 409


async def test_a_write_past_the_project_cap_is_a_413(
    client: AsyncClient, you: User, project: Project, document: LatexDocument, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "latex_project_max_bytes", 10)
    base = f"/v1/projects/{project.id}/latex/{document.id}"

    resp = await client.put(
        f"{base}/file", params={"path": "a.tex"}, json={"content": "x" * 11}, headers=_h(you)
    )
    assert resp.status_code == 413


async def test_a_viewer_cannot_write_but_can_read(
    client: AsyncClient,
    db_session: AsyncSession,
    project: Project,
    document: LatexDocument,
    you: User,
):
    # Named explicitly rather than "any user that is not you": seed_users
    # creates three, and `.first()` without an ORDER BY is whichever row the
    # database hands back.
    viewer = (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one()
    db_session.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
    await db_session.commit()

    base = f"/v1/projects/{project.id}/latex/{document.id}"
    write = await client.put(
        f"{base}/file", params={"path": "a.tex"}, json={"content": "x"}, headers=_h(viewer)
    )
    assert write.status_code == 403

    read = await client.get(f"{base}/files", headers=_h(viewer))
    assert read.status_code == 200


async def test_a_non_member_gets_404_on_the_tree(
    client: AsyncClient,
    db_session: AsyncSession,
    project: Project,
    document: LatexDocument,
    you: User,
):
    # project_service deliberately answers a non-member with 404, not 403 --
    # "do NOT reveal existence via 403" (see its module docstring). 403 is
    # reserved for a member whose role is too low.
    stranger = (
        await db_session.execute(select(User).where(User.email == "marco@lab.io"))
    ).scalar_one()
    base = f"/v1/projects/{project.id}/latex/{document.id}"

    resp = await client.get(f"{base}/files", headers=_h(stranger))
    assert resp.status_code == 404


async def test_a_document_from_another_project_404s_on_the_tree(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project
):
    other = Project(owner_id=you.id, title="Other", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=other.id, user_id=you.id, role="owner"))
    doc = LatexDocument(project_id=other.id, name="elsewhere", source="")
    db_session.add(doc)
    await db_session.commit()

    resp = await client.get(f"/v1/projects/{project.id}/latex/{doc.id}/files", headers=_h(you))
    assert resp.status_code == 404


# ── Fix round 1 additions ────────────────────────────────────────────────
#
# Finding 1: rename must normalize `from` before comparing it against
# `main_path` (which is always stored normalized), the same way delete does.
#
# Finding 2: every route must independently enforce membership and role, and
# `_document_or_404` must be exercised on the write surface too -- not just
# on the one GET that happened to be covered.
#
# Finding 3: the streaming byte cap on the binary upload route must actually
# be exercised, including the chunked/no-Content-Length case the counter
# exists for.


async def test_renaming_the_main_file_by_a_denormalized_path_still_moves_main_path(
    client: AsyncClient, you: User, project: Project, document: LatexDocument
):
    """The guard compares NORMALIZED paths on both sides. Comparing
    `main_path` against the raw `from` string would leave `main_path`
    pointing at a file that no longer exists after a denormalized rename."""
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    await client.put(
        f"{base}/file", params={"path": "main.tex"}, json={"content": "a"}, headers=_h(you)
    )

    resp = await client.post(
        f"{base}/file/rename", json={"from": "./main.tex", "to": "paper.tex"}, headers=_h(you)
    )
    assert resp.status_code == 200
    assert resp.json()["path"] == "paper.tex"

    # LatexDocumentOut does not expose main_path, so prove it followed the
    # rename indirectly: if it did, "paper.tex" is now the main file and the
    # delete guard refuses it with 409.
    delete = await client.delete(f"{base}/file", params={"path": "paper.tex"}, headers=_h(you))
    assert delete.status_code == 409


# (method, path suffix, request body, permission level the route requires)
_ROUTES = [
    ("GET", "/files", None, "viewer"),
    ("GET", "/file", None, "viewer"),
    ("PUT", "/file", {"content": "x"}, "editor"),
    ("POST", "/file/binary", b"\x00", "editor"),
    ("DELETE", "/file", None, "editor"),
    ("POST", "/file/rename", {"from": "a.tex", "to": "b.tex"}, "editor"),
]


async def _call(client: AsyncClient, base: str, method: str, suffix: str, body, headers: dict):
    if suffix == "/files":
        return await client.request(method, f"{base}/files", headers=headers)
    if suffix == "/file":
        params = {"path": "a.tex"}
        if method == "GET":
            return await client.get(f"{base}/file", params=params, headers=headers)
        if method == "PUT":
            return await client.put(f"{base}/file", params=params, json=body, headers=headers)
        if method == "DELETE":
            return await client.delete(f"{base}/file", params=params, headers=headers)
    if suffix == "/file/binary":
        return await client.post(
            f"{base}/file/binary", params={"path": "f.png"}, content=body, headers=headers
        )
    if suffix == "/file/rename":
        return await client.post(f"{base}/file/rename", json=body, headers=headers)
    raise AssertionError(f"unhandled route {method} {suffix}")


@pytest.mark.parametrize(
    "method,suffix,body,level", _ROUTES, ids=[f"{m}{s}" for m, s, _, _ in _ROUTES]
)
async def test_a_non_member_gets_404_on_every_file_route(
    client: AsyncClient,
    db_session: AsyncSession,
    project: Project,
    document: LatexDocument,
    you: User,
    method: str,
    suffix: str,
    body,
    level: str,
):
    """Pins `require_member` on all six routes, not just the two that
    happened to have coverage before -- deleting the call from any one of
    the other four left the suite green.

    Every probed resource is made to EXIST first, as the owner: an absent
    file would 404 from `read_file`/`_document_or_404` alone, which is
    indistinguishable from a membership 404 and would leave this test green
    even with `require_member` deleted from the route. `a.tex` is
    deliberately not the document's `main_path` ("main.tex" by default), or
    the DELETE case would 409 instead of 204/404 -- a different ambiguity.
    `b.tex` is left absent so the rename case has a real destination."""
    base = f"/v1/projects/{project.id}/latex/{document.id}"
    await client.put(
        f"{base}/file", params={"path": "a.tex"}, json={"content": "x"}, headers=_h(you)
    )

    stranger = (
        await db_session.execute(select(User).where(User.email == "marco@lab.io"))
    ).scalar_one()

    resp = await _call(client, base, method, suffix, body, _h(stranger))
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "method,suffix,body,level", _ROUTES, ids=[f"{m}{s}" for m, s, _, _ in _ROUTES]
)
async def test_a_viewer_gets_403_only_on_routes_that_require_editor(
    client: AsyncClient,
    db_session: AsyncSession,
    project: Project,
    document: LatexDocument,
    you: User,
    method: str,
    suffix: str,
    body,
    level: str,
):
    """Pins the REQUIRED LEVEL of each route, not merely that some check ran:
    a viewer must be refused on every editor route and let through
    (not-403) on every viewer route."""
    viewer = (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one()
    db_session.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
    await db_session.commit()

    base = f"/v1/projects/{project.id}/latex/{document.id}"
    resp = await _call(client, base, method, suffix, body, _h(viewer))
    if level == "editor":
        assert resp.status_code == 403
    else:
        assert resp.status_code != 403


async def test_a_document_from_another_project_404s_on_the_write_routes(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project
):
    """`_document_or_404` was only exercised on the tree GET before -- the
    whole write surface (PUT, DELETE, rename) was untested against a
    document id that belongs to a different project."""
    other = Project(owner_id=you.id, title="Other", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=other.id, user_id=you.id, role="owner"))
    doc = LatexDocument(project_id=other.id, name="elsewhere", source="")
    db_session.add(doc)
    await db_session.commit()

    base = f"/v1/projects/{project.id}/latex/{doc.id}"

    put = await client.put(
        f"{base}/file", params={"path": "a.tex"}, json={"content": "x"}, headers=_h(you)
    )
    assert put.status_code == 404

    delete = await client.delete(f"{base}/file", params={"path": "a.tex"}, headers=_h(you))
    assert delete.status_code == 404

    rename = await client.post(
        f"{base}/file/rename", json={"from": "a.tex", "to": "b.tex"}, headers=_h(you)
    )
    assert rename.status_code == 404


async def test_a_binary_upload_over_the_cap_is_a_413(
    client: AsyncClient, you: User, project: Project, document: LatexDocument, monkeypatch
):
    monkeypatch.setattr(settings, "latex_file_max_bytes", 16)
    base = f"/v1/projects/{project.id}/latex/{document.id}"

    resp = await client.post(
        f"{base}/file/binary", params={"path": "f.bin"}, content=b"x" * 64, headers=_h(you)
    )
    assert resp.status_code == 413

    tree = await client.get(f"{base}/files", headers=_h(you))
    assert tree.json()["files"] == []


async def test_a_chunked_binary_upload_with_no_content_length_is_still_capped(
    client: AsyncClient, you: User, project: Project, document: LatexDocument, monkeypatch
):
    """An async-generator body makes httpx send the request without a
    Content-Length -- exactly the case the running byte counter exists for,
    since a client sending chunked encoding can otherwise claim any size (or
    none) up front."""
    monkeypatch.setattr(settings, "latex_file_max_bytes", 16)
    base = f"/v1/projects/{project.id}/latex/{document.id}"

    async def body():
        for _ in range(8):
            yield b"x" * 8

    resp = await client.post(
        f"{base}/file/binary", params={"path": "f.bin"}, content=body(), headers=_h(you)
    )
    assert resp.status_code == 413

    tree = await client.get(f"{base}/files", headers=_h(you))
    assert tree.json()["files"] == []
