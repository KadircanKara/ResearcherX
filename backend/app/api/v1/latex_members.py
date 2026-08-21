"""Per-document editor/viewer grants.

The only writer of `latex_document_members`. Three rows are refused rather
than stored: one naming someone who is not a project member, one naming the
project owner, and one naming the document's creator. The owner and creator
short-circuit ahead of the grant lookup in `services/latex_access.py`, so
such a row could never change an answer -- and a stored row with no effect is
a lie the share dialog would faithfully display. A non-member row would be an
access path the project's own share dialog never shows.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_current_user
from app.db.models import LatexDocument, LatexDocumentMember, ProjectMember, User
from app.db.session import get_session
from app.schemas.latex import LatexMemberCreate, LatexMemberOut, LatexMemberRoleUpdate
from app.schemas.user import UserOut
from app.services import latex_access

router = APIRouter(tags=["latex-members"])


async def _out(db: AsyncSession, grant: LatexDocumentMember) -> LatexMemberOut:
    user = await db.get(User, grant.user_id)
    return LatexMemberOut(user=UserOut.model_validate(user), role=str(grant.role))


async def _assert_grantable(
    db: AsyncSession, project_id: str, document_id: str, user_id: str
) -> None:
    """422 for a grant that could never take effect or could never be seen."""
    is_member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none() is not None
    if not is_member:
        raise HTTPException(status_code=422, detail="That person is not a member of this project")

    if await latex_access.owner_id_of(db, project_id) == user_id:
        raise HTTPException(status_code=422, detail="The project owner already has full access")

    document = await db.get(LatexDocument, document_id)
    if document is not None and document.created_by == user_id:
        raise HTTPException(
            status_code=422, detail="The person who created this document already has full access"
        )


@router.get(
    "/projects/{project_id}/latex/{document_id}/members",
    response_model=list[LatexMemberOut],
)
async def list_grants(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[LatexMemberOut]:
    await latex_access.require(db, project_id, document_id, user.id)
    grants = (
        (
            await db.execute(
                select(LatexDocumentMember).where(LatexDocumentMember.document_id == document_id)
            )
        )
        .scalars()
        .all()
    )
    return [await _out(db, grant) for grant in grants]


@router.post(
    "/projects/{project_id}/latex/{document_id}/members",
    response_model=LatexMemberOut,
    status_code=201,
)
async def add_grant(
    project_id: str,
    document_id: str,
    data: LatexMemberCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexMemberOut:
    await latex_access.require(db, project_id, document_id, user.id, need="editor")
    await _assert_grantable(db, project_id, document_id, data.user_id)

    existing = (
        await db.execute(
            select(LatexDocumentMember).where(
                LatexDocumentMember.document_id == document_id,
                LatexDocumentMember.user_id == data.user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Idempotent rather than a 409: re-sharing with someone who already has
        # access is the user asking for a state, not for an insert.
        existing.role = data.role
        await db.commit()
        return await _out(db, existing)

    grant = LatexDocumentMember(document_id=document_id, user_id=data.user_id, role=data.role)
    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    return await _out(db, grant)


@router.patch(
    "/projects/{project_id}/latex/{document_id}/members/{user_id}",
    response_model=LatexMemberOut,
)
async def update_grant(
    project_id: str,
    document_id: str,
    user_id: str,
    data: LatexMemberRoleUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexMemberOut:
    await latex_access.require(db, project_id, document_id, user.id, need="editor")
    grant = (
        await db.execute(
            select(LatexDocumentMember).where(
                LatexDocumentMember.document_id == document_id,
                LatexDocumentMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=404, detail="No such grant")
    grant.role = data.role
    await db.commit()
    await db.refresh(grant)
    return await _out(db, grant)


@router.delete("/projects/{project_id}/latex/{document_id}/members/{user_id}", status_code=204)
async def remove_grant(
    project_id: str,
    document_id: str,
    user_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    await latex_access.require(db, project_id, document_id, user.id, need="editor")
    await db.execute(
        sa_delete(LatexDocumentMember).where(
            LatexDocumentMember.document_id == document_id,
            LatexDocumentMember.user_id == user_id,
        )
    )
    await db.commit()
