import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models import User
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
async def shared(client, users):
    you, amelia, marco = users
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
        json={"name": "paper"},
        headers={"X-Dev-User-Id": you.id},
    )
    return {
        "project_id": project_id,
        "document_id": doc.json()["id"],
        "you": you,
        "amelia": amelia,
        "marco": marco,
    }


async def test_an_editor_grant_is_created_and_listed(client, shared):
    r = await client.post(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        json={"user_id": shared["amelia"].id, "role": "editor"},
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert r.status_code == 201

    listed = await client.get(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert [m["role"] for m in listed.json()] == ["editor"]


async def test_a_grant_for_a_non_member_is_refused(client, shared):
    """Document access that outlives project access is an access path nobody
    can see."""
    r = await client.post(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        json={"user_id": shared["marco"].id, "role": "editor"},
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert r.status_code == 422
    assert "not a member" in r.json()["detail"]


async def test_a_grant_for_the_project_owner_is_refused(client, shared):
    """The owner short-circuits ahead of the grant lookup, so such a row could
    never take effect. Refusing it beats storing a lie the dialog displays.

    "you" is both the project owner AND the document's creator in this
    fixture -- the owner check runs first in `_assert_grantable`, so
    asserting on the exact detail text (rather than just the 422 status)
    proves this is the owner branch firing, not the creator branch that sits
    behind it.
    """
    r = await client.post(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        json={"user_id": shared["you"].id, "role": "viewer"},
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "The project owner already has full access"


async def test_a_grant_for_the_documents_creator_is_refused(client, shared):
    """A member who created the document (but is not the project owner) hits
    the creator refusal specifically, not the owner one."""
    you, amelia, _ = shared["you"], shared["amelia"], shared["marco"]
    doc = await client.post(
        f"/v1/projects/{shared['project_id']}/latex",
        json={"name": "amelias-doc"},
        headers={"X-Dev-User-Id": amelia.id},
    )
    document_id = doc.json()["id"]

    r = await client.post(
        f"/v1/projects/{shared['project_id']}/latex/{document_id}/members",
        json={"user_id": amelia.id, "role": "viewer"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "The person who created this document already has full access"


async def test_a_viewer_may_not_grant_access(client, shared):
    r = await client.post(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        json={"user_id": shared["amelia"].id, "role": "editor"},
        headers={"X-Dev-User-Id": shared["amelia"].id},
    )
    assert r.status_code == 403


async def test_a_grant_is_updated_and_revoked(client, shared):
    await client.post(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        json={"user_id": shared["amelia"].id, "role": "editor"},
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    patched = await client.patch(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members/{shared['amelia'].id}",
        json={"role": "viewer"},
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert patched.status_code == 200
    assert patched.json()["role"] == "viewer"

    removed = await client.delete(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members/{shared['amelia'].id}",
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert removed.status_code == 204


async def test_removing_a_project_member_revokes_their_document_grants(client, shared):
    granted = await client.post(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        json={"user_id": shared["amelia"].id, "role": "editor"},
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert granted.status_code == 201

    before = await client.get(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    # Proves the assertion below is not vacuously true: if grant creation ever
    # started returning a non-201, this would already be empty and the final
    # `== []` would prove nothing.
    assert before.json() != []

    await client.delete(
        f"/v1/projects/{shared['project_id']}/members/{shared['amelia'].id}",
        headers={"X-Dev-User-Id": shared["you"].id},
    )

    listed = await client.get(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert listed.json() == []


async def test_reposting_a_grant_updates_the_existing_row_instead_of_adding_one(client, shared):
    """The upsert branch: re-sharing with someone who already has access
    updates their role rather than 409ing or creating a second row. The row
    COUNT after the second POST is what proves an update happened, not just
    the returned role."""
    first = await client.post(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        json={"user_id": shared["amelia"].id, "role": "editor"},
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert first.status_code == 201

    second = await client.post(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        json={"user_id": shared["amelia"].id, "role": "viewer"},
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    assert second.status_code == 201
    assert second.json()["role"] == "viewer"

    listed = await client.get(
        f"/v1/projects/{shared['project_id']}/latex/{shared['document_id']}/members",
        headers={"X-Dev-User-Id": shared["you"].id},
    )
    rows = [m for m in listed.json() if m["user"]["id"] == shared["amelia"].id]
    assert len(rows) == 1
    assert rows[0]["role"] == "viewer"
