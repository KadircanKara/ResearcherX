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

from app.db.models import LatexDocument, LatexDocumentMember, Project
from app.services import project_service

EDITOR = "editor"
VIEWER = "viewer"


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

    # Both short-circuits sit ahead of the grant lookup deliberately. A grant
    # row saying "viewer" must not lock the project owner out of their own
    # project, nor the creator out of what they made. The grant routes refuse
    # to create such a row, so this ordering never silently contradicts a row
    # a user can see in the share dialog -- the row cannot exist.
    if str(membership.role) == "owner":
        return EDITOR
    if document.created_by is not None and document.created_by == user_id:
        return EDITOR

    grant = (
        await db.execute(
            select(LatexDocumentMember).where(
                LatexDocumentMember.document_id == document_id,
                LatexDocumentMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if grant is not None:
        return str(grant.role)

    # Every project member reads every document in the project. This is the
    # approved default, not a failure to find a grant.
    return VIEWER


async def require(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    need: str = VIEWER,
) -> str:
    """`resolve`, plus a 403 when the caller falls short of *need*."""
    access = await resolve(db, project_id, document_id, user_id)
    if need == EDITOR and access != EDITOR:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return access


async def owner_id_of(db: AsyncSession, project_id: str) -> str | None:
    """The project's owner, for the grant routes' refusal check."""
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    return None if project is None else project.owner_id
