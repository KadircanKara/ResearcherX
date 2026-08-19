"""LaTeX document router.

Compilation runs in a separate, sandboxed container -- see
latex-compiler/app.py for why the containment is the container rather than
engine flags.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_current_user
from app.db.models import LatexDocument, User
from app.db.session import get_session
from app.schemas.latex import (
    LatexDocumentCreate,
    LatexDocumentOut,
    LatexDocumentUpdate,
)
from app.services import project_service

router = APIRouter(tags=["latex"])


async def _get_document_or_404(
    db: AsyncSession, project_id: str, document_id: str
) -> LatexDocument:
    """A document from another project is a 404, not a 403: the caller learns
    nothing about documents outside the project they asked about."""
    result = await db.execute(
        select(LatexDocument).where(
            LatexDocument.id == document_id, LatexDocument.project_id == project_id
        )
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/projects/{project_id}/latex", response_model=list[LatexDocumentOut])
async def list_documents(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[LatexDocumentOut]:
    await project_service.require_member(db, project_id, user.id, "viewer")
    rows = (
        (
            await db.execute(
                select(LatexDocument)
                .where(LatexDocument.project_id == project_id)
                .order_by(LatexDocument.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [LatexDocumentOut.model_validate(row) for row in rows]


@router.post("/projects/{project_id}/latex", response_model=LatexDocumentOut, status_code=201)
async def create_document(
    project_id: str,
    payload: LatexDocumentCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexDocumentOut:
    await project_service.require_member(db, project_id, user.id, "editor")
    document = LatexDocument(
        project_id=project_id,
        name=payload.name,
        source=payload.source,
        engine=payload.engine,
        created_by=user.id,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return LatexDocumentOut.model_validate(document)


@router.get("/projects/{project_id}/latex/{document_id}", response_model=LatexDocumentOut)
async def get_document(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexDocumentOut:
    await project_service.require_member(db, project_id, user.id, "viewer")
    return LatexDocumentOut.model_validate(await _get_document_or_404(db, project_id, document_id))


@router.patch("/projects/{project_id}/latex/{document_id}", response_model=LatexDocumentOut)
async def update_document(
    project_id: str,
    document_id: str,
    payload: LatexDocumentUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexDocumentOut:
    await project_service.require_member(db, project_id, user.id, "editor")
    document = await _get_document_or_404(db, project_id, document_id)
    if payload.name is not None:
        document.name = payload.name
    if payload.source is not None:
        document.source = payload.source
    if payload.engine is not None:
        document.engine = payload.engine
    await db.commit()
    await db.refresh(document)
    return LatexDocumentOut.model_validate(document)


@router.delete("/projects/{project_id}/latex/{document_id}", status_code=204)
async def delete_document(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await project_service.require_member(db, project_id, user.id, "editor")
    document = await _get_document_or_404(db, project_id, document_id)
    await db.delete(document)
    await db.commit()
    return Response(status_code=204)
