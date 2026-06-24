"""Tests for /v1/projects and /v1/projects/{id}/members endpoints."""

from sqlalchemy import select
import pytest_asyncio

from app.db.models import User
from app.db.seed import seed_users


@pytest_asyncio.fixture
async def seeded(db_session):
    await seed_users(db_session)
    await db_session.commit()


@pytest_asyncio.fixture
async def users(db_session, seeded):
    """Return (you, amelia, marco) User objects."""
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


# ── owner creates project ────────────────────────────────────────────────────


async def test_owner_creates_project(client, users):
    you, _, _ = users
    r = await client.post(
        "/v1/projects",
        json={"title": "Quantum Gravity"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "Quantum Gravity"
    assert data["owner_id"] == you.id
    assert "id" in data


# ── list: other user does not see it ────────────────────────────────────────


async def test_other_user_list_excludes_project(client, users):
    you, amelia, _ = users

    # you create a project
    await client.post(
        "/v1/projects",
        json={"title": "Private Project"},
        headers={"X-Dev-User-Id": you.id},
    )

    # amelia lists hers — should be empty
    r = await client.get("/v1/projects", headers={"X-Dev-User-Id": amelia.id})
    assert r.status_code == 200
    assert r.json() == []


# ── owner adds viewer; viewer sees it with my_role ───────────────────────────


async def test_owner_adds_viewer_sees_project(client, users):
    you, amelia, _ = users

    # you create
    create_r = await client.post(
        "/v1/projects",
        json={"title": "Shared Project"},
        headers={"X-Dev-User-Id": you.id},
    )
    project_id = create_r.json()["id"]

    # owner adds amelia as viewer
    add_r = await client.post(
        f"/v1/projects/{project_id}/members",
        json={"user_id": amelia.id, "role": "viewer"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert add_r.status_code == 201
    member_data = add_r.json()
    assert member_data["role"] == "viewer"
    assert member_data["user"]["id"] == amelia.id

    # amelia lists — sees the project
    list_r = await client.get("/v1/projects", headers={"X-Dev-User-Id": amelia.id})
    assert list_r.status_code == 200
    projects = list_r.json()
    assert len(projects) == 1
    assert projects[0]["id"] == project_id

    # amelia gets detail — my_role=viewer
    detail_r = await client.get(
        f"/v1/projects/{project_id}",
        headers={"X-Dev-User-Id": amelia.id},
    )
    assert detail_r.status_code == 200
    detail = detail_r.json()
    assert detail["my_role"] == "viewer"
    assert detail["project"]["id"] == project_id
    assert any(m["user"]["id"] == amelia.id for m in detail["members"])


# ── viewer PATCH → 403 ───────────────────────────────────────────────────────


async def test_viewer_patch_returns_403(client, users):
    you, amelia, _ = users

    create_r = await client.post(
        "/v1/projects",
        json={"title": "Locked"},
        headers={"X-Dev-User-Id": you.id},
    )
    project_id = create_r.json()["id"]

    await client.post(
        f"/v1/projects/{project_id}/members",
        json={"user_id": amelia.id, "role": "viewer"},
        headers={"X-Dev-User-Id": you.id},
    )

    r = await client.patch(
        f"/v1/projects/{project_id}",
        json={"title": "Hacked"},
        headers={"X-Dev-User-Id": amelia.id},
    )
    assert r.status_code == 403


# ── non-member GET → 404 ─────────────────────────────────────────────────────


async def test_nonmember_get_returns_404(client, users):
    you, _, marco = users

    create_r = await client.post(
        "/v1/projects",
        json={"title": "Secret"},
        headers={"X-Dev-User-Id": you.id},
    )
    project_id = create_r.json()["id"]

    r = await client.get(
        f"/v1/projects/{project_id}",
        headers={"X-Dev-User-Id": marco.id},
    )
    assert r.status_code == 404


# ── owner DELETE → 204 ───────────────────────────────────────────────────────


async def test_owner_delete_returns_204(client, users):
    you, _, _ = users

    create_r = await client.post(
        "/v1/projects",
        json={"title": "Temporary"},
        headers={"X-Dev-User-Id": you.id},
    )
    project_id = create_r.json()["id"]

    r = await client.delete(
        f"/v1/projects/{project_id}",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 204

    # gone — list is now empty
    list_r = await client.get("/v1/projects", headers={"X-Dev-User-Id": you.id})
    assert list_r.json() == []


# ── members list ─────────────────────────────────────────────────────────────


async def test_members_list(client, users):
    you, amelia, _ = users

    create_r = await client.post(
        "/v1/projects",
        json={"title": "Team"},
        headers={"X-Dev-User-Id": you.id},
    )
    project_id = create_r.json()["id"]

    await client.post(
        f"/v1/projects/{project_id}/members",
        json={"user_id": amelia.id, "role": "editor"},
        headers={"X-Dev-User-Id": you.id},
    )

    r = await client.get(
        f"/v1/projects/{project_id}/members",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 200
    members = r.json()
    emails = {m["user"]["email"] for m in members}
    assert "you@researcherx.dev" in emails
    assert "amelia@lab.io" in emails


# ── PATCH member role ────────────────────────────────────────────────────────


async def test_owner_can_update_member_role(client, users):
    you, amelia, _ = users

    create_r = await client.post(
        "/v1/projects",
        json={"title": "Roles"},
        headers={"X-Dev-User-Id": you.id},
    )
    project_id = create_r.json()["id"]

    await client.post(
        f"/v1/projects/{project_id}/members",
        json={"user_id": amelia.id, "role": "viewer"},
        headers={"X-Dev-User-Id": you.id},
    )

    r = await client.patch(
        f"/v1/projects/{project_id}/members/{amelia.id}",
        json={"role": "editor"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "editor"


# ── DELETE member ────────────────────────────────────────────────────────────


async def test_owner_can_remove_member(client, users):
    you, amelia, _ = users

    create_r = await client.post(
        "/v1/projects",
        json={"title": "Shrink"},
        headers={"X-Dev-User-Id": you.id},
    )
    project_id = create_r.json()["id"]

    await client.post(
        f"/v1/projects/{project_id}/members",
        json={"user_id": amelia.id, "role": "viewer"},
        headers={"X-Dev-User-Id": you.id},
    )

    r = await client.delete(
        f"/v1/projects/{project_id}/members/{amelia.id}",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 204
