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
    CompileOut,
    LatexDocumentCreate,
    LatexDocumentOut,
    LatexDocumentUpdate,
    SynctexForwardIn,
    SynctexForwardOut,
    SynctexReverseIn,
    SynctexReverseOut,
)
from app.services import project_service
from app.services.latex_cache import CachedBuild, cache, source_hash
from app.services.latex_compiler import compile_source, synctex_forward, synctex_reverse

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


@router.post("/projects/{project_id}/latex/{document_id}/compile", response_model=CompileOut)
async def compile_document(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> CompileOut:
    await project_service.require_member(db, project_id, user.id, "editor")
    document = await _get_document_or_404(db, project_id, document_id)
    # Read what the compile needs into plain values, then END THE TRANSACTION
    # before the external call. A compile is bounded by
    # `latex_compile_timeout` (60s) and the pool is 5 + 5 overflow, so holding
    # the checked-out connection across it means ten concurrent compiles
    # starve every other request in the app — including the ones that are not
    # about LaTeX at all. `research_service` already establishes this pattern
    # for slow LLM calls. Extract BEFORE the commit: touching an ORM attribute
    # afterwards would trigger a refresh and check a connection straight back
    # out.
    doc_id, source, engine = document.id, document.source, document.engine
    await db.commit()

    key = source_hash(source, engine)
    cached = cache.get(key)
    if cached is not None:
        # Identical source and engine cannot produce a different PDF, so a
        # repeat compile is a lookup rather than another 30s of CPU.
        return CompileOut(ok=True, log=cached.log, pdf_hash=key)

    result = await compile_source(source, engine)
    if not result.ok or result.pdf is None:
        # No hash on failure: the client keeps the PDF it already has, so a
        # broken edit never blanks the preview.
        return CompileOut(ok=False, log=result.log, pdf_hash=None)

    cache.put(
        key,
        CachedBuild(source=source, pdf=result.pdf, synctex_gz=result.synctex_gz, log=result.log),
        document_id=doc_id,
    )
    return CompileOut(ok=True, log=result.log, pdf_hash=key)


@router.get("/projects/{project_id}/latex/{document_id}/pdf")
async def get_document_pdf(
    project_id: str,
    document_id: str,
    hash: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await project_service.require_member(db, project_id, user.id, "viewer")
    await _get_document_or_404(db, project_id, document_id)
    build = cache.get(hash)
    if build is None:
        # The cache is in-process and bounded, so a hash can age out. A 404
        # tells the client to recompile rather than pretending the PDF is gone
        # forever.
        raise HTTPException(status_code=404, detail="No compiled PDF for that hash")
    return Response(content=build.pdf, media_type="application/pdf")


@router.post(
    "/projects/{project_id}/latex/{document_id}/synctex/forward",
    response_model=SynctexForwardOut,
)
async def synctex_forward_route(
    project_id: str,
    document_id: str,
    payload: SynctexForwardIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> SynctexForwardOut:
    await project_service.require_member(db, project_id, user.id, "viewer")
    document = await _get_document_or_404(db, project_id, document_id)

    # The map answers for the LAST COMPILED source, not what is on screen now.
    doc_id = document.id
    # Same reason as the compile route: no pooled connection is held across
    # the external synctex call.
    await db.commit()

    build = cache.latest_for(doc_id)
    if build is None or build.synctex_gz is None:
        return SynctexForwardOut(found=False)

    position = await synctex_forward(build.source, build.pdf, build.synctex_gz, payload.line)
    if position is None:
        return SynctexForwardOut(found=False)
    return SynctexForwardOut(
        found=True,
        page=position.page,
        x=position.x,
        y=position.y,
        width=position.width,
        height=position.height,
    )


@router.post(
    "/projects/{project_id}/latex/{document_id}/synctex/reverse",
    response_model=SynctexReverseOut,
)
async def synctex_reverse_route(
    project_id: str,
    document_id: str,
    payload: SynctexReverseIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> SynctexReverseOut:
    await project_service.require_member(db, project_id, user.id, "viewer")
    document = await _get_document_or_404(db, project_id, document_id)

    doc_id = document.id
    # Same reason as the compile route: no pooled connection is held across
    # the external synctex call.
    await db.commit()

    build = cache.latest_for(doc_id)
    if build is None or build.synctex_gz is None:
        return SynctexReverseOut(found=False)

    line = await synctex_reverse(
        build.source, build.pdf, build.synctex_gz, payload.page, payload.x, payload.y
    )
    if line is None:
        return SynctexReverseOut(found=False)
    return SynctexReverseOut(found=True, line=line)
