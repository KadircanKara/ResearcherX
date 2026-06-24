"""TDD tests for project_service.py — B2.

Run order: write these first (red), then implement (green).
"""

import pytest
from fastapi import HTTPException

from app.db.models import Role, User
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.services.project_service import (
    add_member,
    create_project,
    get_project,
    list_projects,
    remove_member,
    update_project,
)


# ── helpers ────────────────────────────────────────────────────────────────


async def _seed_user(db, email: str, name: str = "User") -> User:
    u = User(email=email, name=name)
    db.add(u)
    await db.flush()
    return u


# ── tests ──────────────────────────────────────────────────────────────────


async def test_create_project_makes_caller_owner(db_session):
    """create_project inserts the caller as owner member."""
    owner = await _seed_user(db_session, "owner@x.io")
    data = ProjectCreate(title="My Project", description="desc", topic_keywords=["ai"])
    project = await create_project(db_session, owner, data)

    assert project.title == "My Project"
    assert project.owner_id == owner.id

    # caller must be an owner member
    listing = await list_projects(db_session, owner)
    assert len(listing) == 1
    assert listing[0]["my_role"] == Role.OWNER


async def test_list_returns_only_my_projects(db_session):
    """list_projects only returns projects where the caller is a member."""
    alice = await _seed_user(db_session, "alice@x.io")
    bob = await _seed_user(db_session, "bob@x.io")

    await create_project(db_session, alice, ProjectCreate(title="Alice's"))
    await create_project(db_session, bob, ProjectCreate(title="Bob's"))

    alice_projects = await list_projects(db_session, alice)
    bob_projects = await list_projects(db_session, bob)

    assert len(alice_projects) == 1
    assert alice_projects[0]["project"].title == "Alice's"
    assert len(bob_projects) == 1
    assert bob_projects[0]["project"].title == "Bob's"


async def test_get_project_non_member_raises_404(db_session):
    """Non-member get → 404 (existence must not be leaked via 403)."""
    owner = await _seed_user(db_session, "owner@x.io")
    stranger = await _seed_user(db_session, "stranger@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="Secret"))

    with pytest.raises(HTTPException) as exc_info:
        await get_project(db_session, stranger, project.id)
    assert exc_info.value.status_code == 404


async def test_get_project_returns_correct_role(db_session):
    """get_project returns the caller's role."""
    owner = await _seed_user(db_session, "owner@x.io")
    editor = await _seed_user(db_session, "editor@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))
    await add_member(db_session, owner, project.id, editor.id, "editor")

    proj, members, my_role = await get_project(db_session, editor, project.id)
    assert my_role == "editor"
    assert len(members) == 2  # owner + editor


async def test_commenter_cannot_update_project(db_session):
    """Member with commenter role cannot update — 403."""
    owner = await _seed_user(db_session, "owner@x.io")
    commenter = await _seed_user(db_session, "commenter@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))
    await add_member(db_session, owner, project.id, commenter.id, "commenter")

    with pytest.raises(HTTPException) as exc_info:
        await update_project(db_session, commenter, project.id, ProjectUpdate(title="Hacked"))
    assert exc_info.value.status_code == 403


async def test_editor_can_update_project(db_session):
    """Member with editor role can update."""
    owner = await _seed_user(db_session, "owner@x.io")
    editor = await _seed_user(db_session, "editor@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))
    await add_member(db_session, owner, project.id, editor.id, "editor")

    updated = await update_project(db_session, editor, project.id, ProjectUpdate(title="Updated"))
    assert updated.title == "Updated"


async def test_only_owner_can_add_member(db_session):
    """Editor cannot add members — 403."""
    owner = await _seed_user(db_session, "owner@x.io")
    editor = await _seed_user(db_session, "editor@x.io")
    newcomer = await _seed_user(db_session, "newcomer@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))
    await add_member(db_session, owner, project.id, editor.id, "editor")

    with pytest.raises(HTTPException) as exc_info:
        await add_member(db_session, editor, project.id, newcomer.id, "viewer")
    assert exc_info.value.status_code == 403


async def test_cannot_remove_last_owner(db_session):
    """Removing the sole owner → 400."""
    owner = await _seed_user(db_session, "owner@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))

    with pytest.raises(HTTPException) as exc_info:
        await remove_member(db_session, owner, project.id, owner.id)
    assert exc_info.value.status_code == 400


async def test_counts_members_correct(db_session):
    """list_projects reports the real member count."""
    owner = await _seed_user(db_session, "owner@x.io")
    viewer = await _seed_user(db_session, "viewer@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))
    await add_member(db_session, owner, project.id, viewer.id, "viewer")

    listing = await list_projects(db_session, owner)
    assert listing[0]["counts"]["members"] == 2
    assert listing[0]["counts"]["papers"] == 0
    assert listing[0]["counts"]["chats"] == 0
