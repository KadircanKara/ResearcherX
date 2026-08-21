"""Who may change ONE LaTeX document.

The single answer to that question in this codebase. Project membership is
binary (`core/permissions.py`); this module is the finer layer, and it exists
only for LaTeX because that is the thing users share at document granularity.

Every LaTeX route calls `require`. Nothing re-derives an answer of its own --
including the client, which is handed the result as `my_access`.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument, LatexDocumentMember, ProjectMember
from app.services import project_service

EDITOR = "editor"
VIEWER = "viewer"


def decide(
    membership_role: str, created_by: str | None, grant_role: str | None, user_id: str
) -> str:
    """The ordering itself, and the ONLY place it is written down.

    Pure -- no I/O, no ORM types -- so `resolve` (one document) and
    `list_documents` (N documents, one query) can both call this and get
    identical behaviour instead of a second, drifting implementation of the
    ordering. `owner` and `created_by` short-circuit ahead of the grant
    deliberately: a grant row saying "viewer" must not lock the project owner
    out of their own project, nor the creator out of what they made. The
    grant routes refuse to create such a row, so this ordering never silently
    contradicts a row a user can see in the share dialog -- the row cannot
    exist.
    """
    if membership_role == "owner":
        return EDITOR
    if created_by is not None and created_by == user_id:
        return EDITOR
    if grant_role is not None:
        return grant_role
    # Every project member reads every document in the project. This is the
    # approved default, not a failure to find a grant.
    return VIEWER


async def resolve(db: AsyncSession, project_id: str, document_id: str, user_id: str) -> str:
    """Return "editor" or "viewer", or raise 404 for anything unreachable.

    404 rather than 403 for a non-member: a 403 confirms the project exists to
    someone with no business knowing that. `require_member` already draws that
    line and this preserves it.
    """
    membership = await project_service.require_member(db, project_id, user_id, "member")

    document = (
        await db.execute(
            select(LatexDocument).where(
                LatexDocument.id == document_id,
                LatexDocument.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if document is None:
        # Scoped to the project on purpose: a document id from another project
        # must not resolve here just because the caller is a member of THIS
        # one.
        raise HTTPException(status_code=404, detail="Document not found")

    grant = (
        await db.execute(
            select(LatexDocumentMember).where(
                LatexDocumentMember.document_id == document_id,
                LatexDocumentMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    grant_role = str(grant.role) if grant is not None else None

    return decide(str(membership.role), document.created_by, grant_role, user_id)


async def require(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    need: str = VIEWER,
) -> str:
    """`resolve`, plus a 403 when the caller falls short of *need*.

    `need` is developer-supplied, never database- or client-supplied, so an
    unrecognized value is our bug and must crash loudly (ValueError) rather
    than silently perform no check -- unlike a role string read back from the
    database, which must never crash a request.
    """
    if need not in (VIEWER, EDITOR):
        raise ValueError(f"unknown need: {need}")
    access = await resolve(db, project_id, document_id, user_id)
    if need == EDITOR and access != EDITOR:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return access


async def owner_id_of(db: AsyncSession, project_id: str) -> str | None:
    """The project's owner, for the grant routes' refusal check.

    Reads the same `ProjectMember` row that `resolve` uses to decide who the
    owner is -- `ProjectMember` is the single authority for access decisions,
    so this must not read `Project.owner_id` as a second, potentially
    disagreeing source of truth.

    Orders deterministically and takes the first row rather than asserting
    uniqueness with `scalar_one_or_none()`. The contract here is "an owner",
    not "proof there is exactly one" -- the API can no longer create a second
    owner row after the role collapse, but seed data or a direct database
    write still can, and this function must not 500 every grant-route POST
    if that happens.
    """
    membership = (
        (
            await db.execute(
                select(ProjectMember)
                .where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.role == "owner",
                )
                .order_by(ProjectMember.created_at, ProjectMember.id)
            )
        )
        .scalars()
        .first()
    )
    return None if membership is None else membership.user_id
