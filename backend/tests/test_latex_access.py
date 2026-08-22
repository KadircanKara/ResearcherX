"""The resolution table from the spec, one test per row, plus the two rows the
grant table must never be able to express."""

import pytest
from fastapi import HTTPException

from app.db.models import LatexDocument, LatexDocumentMember, Project, ProjectMember, User
from app.services import latex_access


@pytest.fixture
async def scene(db_session):
    """owner + member + outsider, one project, one document created by owner."""
    owner = User(name="Owner", email="owner@lab.io")
    member = User(name="Member", email="member@lab.io")
    outsider = User(name="Outsider", email="outsider@lab.io")
    db_session.add_all([owner, member, outsider])
    await db_session.flush()

    project = Project(owner_id=owner.id, title="P")
    db_session.add(project)
    await db_session.flush()
    db_session.add_all(
        [
            ProjectMember(project_id=project.id, user_id=owner.id, role="owner"),
            ProjectMember(project_id=project.id, user_id=member.id, role="member"),
        ]
    )
    doc = LatexDocument(project_id=project.id, name="paper", created_by=owner.id)
    db_session.add(doc)
    await db_session.flush()
    await db_session.commit()
    return {"project": project, "doc": doc, "owner": owner, "member": member, "outsider": outsider}


async def test_a_non_member_gets_404(db_session, scene):
    with pytest.raises(HTTPException) as exc:
        await latex_access.resolve(
            db_session, scene["project"].id, scene["doc"].id, scene["outsider"].id
        )
    assert exc.value.status_code == 404


async def test_a_document_in_another_project_gets_404(db_session, scene):
    """The caller IS a member of `other`, so the 404 can only come from the
    project-scoped document lookup, not from `require_member`."""
    other = Project(owner_id=scene["owner"].id, title="Other")
    db_session.add(other)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=other.id, user_id=scene["owner"].id, role="owner"))
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await latex_access.resolve(db_session, other.id, scene["doc"].id, scene["owner"].id)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Document not found"


async def test_the_project_owner_edits(db_session, scene):
    """Uses a document the owner did NOT create, so this pins the owner
    short-circuit rather than the creator one."""
    doc = LatexDocument(
        project_id=scene["project"].id, name="theirs", created_by=scene["member"].id
    )
    db_session.add(doc)
    await db_session.commit()
    assert (
        await latex_access.resolve(db_session, scene["project"].id, doc.id, scene["owner"].id)
        == "editor"
    )


async def test_a_viewer_grant_cannot_demote_the_owner(db_session, scene):
    """The owner short-circuit comes BEFORE the grant lookup on purpose --
    isolated from the creator branch by using a document the owner didn't
    create."""
    doc = LatexDocument(
        project_id=scene["project"].id, name="theirs", created_by=scene["member"].id
    )
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        LatexDocumentMember(document_id=doc.id, user_id=scene["owner"].id, role="viewer")
    )
    await db_session.commit()
    assert (
        await latex_access.resolve(db_session, scene["project"].id, doc.id, scene["owner"].id)
        == "editor"
    )


async def test_a_viewer_grant_cannot_demote_the_creator(db_session, scene):
    """The other unexpressible row: a creator with an explicit viewer grant
    on their own document still edits."""
    doc = LatexDocument(
        project_id=scene["project"].id, name="theirs", created_by=scene["member"].id
    )
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        LatexDocumentMember(document_id=doc.id, user_id=scene["member"].id, role="viewer")
    )
    await db_session.commit()
    assert (
        await latex_access.resolve(db_session, scene["project"].id, doc.id, scene["member"].id)
        == "editor"
    )


async def test_the_creator_edits_without_a_grant(db_session, scene):
    doc = LatexDocument(
        project_id=scene["project"].id, name="theirs", created_by=scene["member"].id
    )
    db_session.add(doc)
    await db_session.commit()
    assert (
        await latex_access.resolve(db_session, scene["project"].id, doc.id, scene["member"].id)
        == "editor"
    )


async def test_a_member_defaults_to_viewer(db_session, scene):
    """Every project member reads every document in it -- viewer is the
    fallback, not an error."""
    assert (
        await latex_access.resolve(
            db_session, scene["project"].id, scene["doc"].id, scene["member"].id
        )
        == "viewer"
    )


async def test_a_viewer_grant_is_honoured_on_a_non_creator(db_session, scene):
    """An explicit viewer grant on someone who is neither owner nor creator
    resolves to viewer -- distinct from the default-viewer fallback above."""
    doc = LatexDocument(project_id=scene["project"].id, name="theirs", created_by=scene["owner"].id)
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        LatexDocumentMember(document_id=doc.id, user_id=scene["member"].id, role="viewer")
    )
    await db_session.commit()
    assert (
        await latex_access.resolve(db_session, scene["project"].id, doc.id, scene["member"].id)
        == "viewer"
    )


async def test_an_editor_grant_is_honoured(db_session, scene):
    db_session.add(
        LatexDocumentMember(document_id=scene["doc"].id, user_id=scene["member"].id, role="editor")
    )
    await db_session.commit()
    assert (
        await latex_access.resolve(
            db_session, scene["project"].id, scene["doc"].id, scene["member"].id
        )
        == "editor"
    )


async def test_require_editor_refuses_a_viewer(db_session, scene):
    with pytest.raises(HTTPException) as exc:
        await latex_access.require(
            db_session,
            scene["project"].id,
            scene["doc"].id,
            scene["member"].id,
            need="editor",
        )
    assert exc.value.status_code == 403


async def test_require_viewer_accepts_a_viewer(db_session, scene):
    assert (
        await latex_access.require(
            db_session, scene["project"].id, scene["doc"].id, scene["member"].id
        )
        == "viewer"
    )


async def test_require_rejects_an_unknown_need(db_session, scene):
    """`need` is developer-supplied, so a typo must crash loudly rather than
    silently perform no check."""
    with pytest.raises(ValueError):
        await latex_access.require(
            db_session,
            scene["project"].id,
            scene["doc"].id,
            scene["member"].id,
            need="admin",
        )
