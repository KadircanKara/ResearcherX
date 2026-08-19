"""LaTeX document router.

Compilation runs in a separate, sandboxed container -- see
latex-compiler/app.py for why the containment is the container rather than
engine flags.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.latex_files import _translate
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
from app.services import latex_files_service as files
from app.services import project_service
from app.services.latex_cache import CachedBuild, cache, source_hash
from app.services.latex_compiler import compile_source, synctex_forward, synctex_reverse
from app.services.latex_paths import InvalidPath, normalize_path

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


async def _as_out(db: AsyncSession, document: LatexDocument) -> LatexDocumentOut:
    """Assemble the response, resolving the `source` shim from the tree.

    `source` is not a column any more. Reading the main file here keeps the
    single-file editor working unchanged while the tree lands; plan 4 deletes
    the field and this helper with it. A document whose main file is missing
    reports an empty source rather than failing -- the editor shows an empty
    buffer, which is recoverable, where a 500 is not.
    """
    row = await files.read_file(db, document.id, document.main_path)
    return LatexDocumentOut(
        id=document.id,
        project_id=document.project_id,
        name=document.name,
        source=(row.content or "") if row is not None and not row.is_binary else "",
        main_path=document.main_path,
        revision=document.revision,
        engine=document.engine,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


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
    out = []
    for row in rows:
        out.append(await _as_out(db, row))
    return out


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
        engine=payload.engine,
        created_by=user.id,
    )
    db.add(document)
    # Flushed, not committed: `document.id` (a Python-side default) must be
    # assigned before `write_text` can target it, but the row and the tree
    # write land in the SAME transaction. Committing between them -- the
    # earlier version of this route did -- leaves an orphan document with an
    # empty tree if the write fails its quota/collision checks.
    await db.flush()
    # The `source` field is a shim: turn it into the real file the compiler
    # will read, so create and compile cannot disagree about what the
    # document contains.
    try:
        await files.write_text(db, document.id, document.main_path, payload.source)
    except Exception as exc:
        await db.rollback()
        raise _translate(exc) from exc
    await db.commit()
    await db.refresh(document)
    return await _as_out(db, document)


@router.get("/projects/{project_id}/latex/{document_id}", response_model=LatexDocumentOut)
async def get_document(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexDocumentOut:
    await project_service.require_member(db, project_id, user.id, "viewer")
    return await _as_out(db, await _get_document_or_404(db, project_id, document_id))


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
    # Order matters: main_path is applied BEFORE source, so a single PATCH
    # carrying both writes the NEW main file rather than the old one.
    if payload.main_path is not None:
        # Normalized ONCE, then that one value is what gets looked up AND
        # stored. `read_file` normalizes only for its own lookup; storing
        # the raw string here would leave `document.main_path` spelled
        # differently from the row's actual `path`, defeating the delete
        # route's "is this the main file" guard and the rename route's
        # "does the main file need to follow" guard -- both compare against
        # `document.main_path` expecting it to already be canonical.
        try:
            normalized_main = normalize_path(payload.main_path)
        except InvalidPath as exc:
            raise _translate(exc) from exc
        target = await files.read_file(db, document.id, normalized_main)
        if target is None or target.is_binary:
            raise HTTPException(
                status_code=422, detail=f"{normalized_main} is not a text file in this document"
            )
        if not normalized_main.endswith(".tex"):
            raise HTTPException(status_code=422, detail="The main file must be a .tex file")
        document.main_path = normalized_main
    if payload.source is not None:
        try:
            await files.write_text(db, document.id, document.main_path, payload.source)
        except Exception as exc:
            await db.rollback()
            raise _translate(exc) from exc
    if payload.engine is not None:
        document.engine = payload.engine
    await db.commit()
    await db.refresh(document)
    return await _as_out(db, document)


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
    doc_id, engine, main_path = document.id, document.engine, document.main_path
    revision = document.revision
    row = await files.read_file(db, doc_id, main_path)
    source = (row.content or "") if row is not None and not row.is_binary else ""
    await db.commit()

    key = source_hash(source, engine)
    cached = cache.get(key)
    if cached is not None:
        # Identical source and engine cannot produce a different PDF, so a
        # repeat compile is a lookup rather than another 30s of CPU. Still
        # record THIS document's latest key: `_latest` is written only by
        # `put`, and SyncTeX answers from `cache.latest_for(doc_id)`, not
        # from this response. Without this, a cache hit never updates
        # `_latest` -- a second document sharing this key gets no entry at
        # all (sync answers `found: False` forever), and a hit that follows
        # an intervening edit leaves `_latest` pointing at that edit's build
        # (sync answers confidently from the WRONG source). Re-`put`-ting
        # the build we just got back is safe and cheap: same key, so
        # `_entries` is a same-key re-assignment (no double count in
        # `_total_bytes`), and `move_to_end` is the LRU promotion a hit
        # should get anyway.
        cache.put(key, cached, document_id=doc_id)
        return CompileOut(ok=True, log=cached.log, pdf_hash=key, revision=revision)

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
    return CompileOut(ok=True, log=result.log, pdf_hash=key, revision=revision)


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
