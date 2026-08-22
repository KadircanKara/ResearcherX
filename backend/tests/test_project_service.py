"""TDD tests for project_service.py — B2.

Run order: write these first (red), then implement (green).
"""

import pytest
from fastapi import HTTPException

from app.db.models import LatexDocument, LatexDocumentMember, Role, User
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.services.project_service import (
    add_member,
    create_project,
    get_project,
    list_projects,
    remove_member,
    update_member_role,
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
    """get_project returns the caller's role. Was `editor`; the finer roles
    collapsed to a single `member`."""
    owner = await _seed_user(db_session, "owner@x.io")
    member = await _seed_user(db_session, "member@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))
    await add_member(db_session, owner, project.id, member.id, "member")

    proj, members, my_role = await get_project(db_session, member, project.id)
    assert my_role == "member"
    assert len(members) == 2  # owner + member


async def test_non_member_cannot_update_project(db_session):
    """Was test_commenter_cannot_update_project: the commenter role no longer
    exists (add_member rejects it — see test_role_string_is_validated). The
    guard it was protecting still exists, just at project membership rather
    than at a sub-rank: a non-member cannot update — 404, not 403, so
    existence isn't leaked."""
    owner = await _seed_user(db_session, "owner@x.io")
    stranger = await _seed_user(db_session, "stranger@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))

    with pytest.raises(HTTPException) as exc_info:
        await update_project(db_session, stranger, project.id, ProjectUpdate(title="Hacked"))
    assert exc_info.value.status_code == 404


async def test_member_can_update_project(db_session):
    """Was test_editor_can_update_project: any member can update now."""
    owner = await _seed_user(db_session, "owner@x.io")
    member = await _seed_user(db_session, "member@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))
    await add_member(db_session, owner, project.id, member.id, "member")

    updated = await update_project(db_session, member, project.id, ProjectUpdate(title="Updated"))
    assert updated.title == "Updated"


async def test_only_owner_can_add_member(db_session):
    """A plain member cannot add members — 403."""
    owner = await _seed_user(db_session, "owner@x.io")
    member = await _seed_user(db_session, "member@x.io")
    newcomer = await _seed_user(db_session, "newcomer@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))
    await add_member(db_session, owner, project.id, member.id, "member")

    with pytest.raises(HTTPException) as exc_info:
        await add_member(db_session, member, project.id, newcomer.id, "member")
    assert exc_info.value.status_code == 403


async def test_role_string_is_validated(db_session):
    """A retired role (editor/commenter/viewer) is refused on add — 422, not
    silently accepted or silently downgraded."""
    owner = await _seed_user(db_session, "owner@x.io")
    newcomer = await _seed_user(db_session, "newcomer@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))

    with pytest.raises(HTTPException) as exc_info:
        await add_member(db_session, owner, project.id, newcomer.id, "commenter")
    assert exc_info.value.status_code == 422


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
    member = await _seed_user(db_session, "member@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))
    await add_member(db_session, owner, project.id, member.id, "member")

    listing = await list_projects(db_session, owner)
    assert listing[0]["counts"]["members"] == 2
    assert listing[0]["counts"]["papers"] == 0
    assert listing[0]["counts"]["chats"] == 0


async def test_sole_owner_cannot_demote_themselves(db_session):
    """update_member_role → 400 when the target is the last remaining owner."""
    owner = await _seed_user(db_session, "owner@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))

    with pytest.raises(HTTPException) as exc_info:
        await update_member_role(db_session, owner, project.id, owner.id, "member")
    assert exc_info.value.status_code == 400


async def test_demotion_succeeds_with_two_owners(db_session):
    """With a second owner present, demoting one of them is allowed."""
    owner = await _seed_user(db_session, "owner@x.io")
    other = await _seed_user(db_session, "other@x.io")

    project = await create_project(db_session, owner, ProjectCreate(title="P"))
    # add_member only ever assigns "member" -- seed a second owner directly,
    # the same way create_project seeds the first.
    from app.db.models import ProjectMember

    db_session.add(ProjectMember(project_id=project.id, user_id=other.id, role="owner"))
    await db_session.commit()

    membership = await update_member_role(db_session, owner, project.id, other.id, "member")
    assert membership.role == "member"


async def test_remove_member_only_deletes_grants_in_that_project(db_session):
    """remove_member's grant cleanup subquery is scoped by project, not global."""
    owner = await _seed_user(db_session, "owner@x.io")
    member = await _seed_user(db_session, "member@x.io")

    project_a = await create_project(db_session, owner, ProjectCreate(title="A"))
    project_b = await create_project(db_session, owner, ProjectCreate(title="B"))
    await add_member(db_session, owner, project_a.id, member.id, "member")
    await add_member(db_session, owner, project_b.id, member.id, "member")

    doc_a = LatexDocument(project_id=project_a.id, name="doc-a", created_by=owner.id)
    doc_b = LatexDocument(project_id=project_b.id, name="doc-b", created_by=owner.id)
    db_session.add_all([doc_a, doc_b])
    await db_session.flush()

    grant_a = LatexDocumentMember(document_id=doc_a.id, user_id=member.id, role="editor")
    grant_b = LatexDocumentMember(document_id=doc_b.id, user_id=member.id, role="editor")
    db_session.add_all([grant_a, grant_b])
    await db_session.commit()

    await remove_member(db_session, owner, project_a.id, member.id)

    from sqlalchemy import select

    remaining = (
        await db_session.execute(
            select(LatexDocumentMember).where(LatexDocumentMember.document_id == doc_b.id)
        )
    ).scalar_one_or_none()
    assert remaining is not None
    assert remaining.user_id == member.id

    removed = (
        await db_session.execute(
            select(LatexDocumentMember).where(LatexDocumentMember.document_id == doc_a.id)
        )
    ).scalar_one_or_none()
    assert removed is None
