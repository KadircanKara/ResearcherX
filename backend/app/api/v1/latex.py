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
from app.services.latex_cache import CachedBuild, cache
from app.services.latex_compiler import compile_tree, synctex_forward, synctex_reverse
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
    # `LatexDocumentCreate.source` turns into the real main file the
    # compiler will read, so create and compile cannot disagree about what
    # the document contains.
    try:
        await files.write_text(db, document.id, document.main_path, payload.source)
    except Exception as exc:
        await db.rollback()
        raise _translate(exc) from exc
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
    document = await _get_document_or_404(db, project_id, document_id)
    return LatexDocumentOut.model_validate(document)


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
        if document.main_path != normalized_main:
            document.main_path = normalized_main
            await files.bump_revision(db, document.id)
    if payload.engine is not None and payload.engine != document.engine:
        document.engine = payload.engine
        await files.bump_revision(db, document.id)
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
    doc_id, engine, main_path = document.id, document.engine, document.main_path
    revision = document.revision
    # `list_files` DEFERS `content`/`blob` -- touching either on a row it
    # returned raises MissingGreenlet under async SQLAlchemy. `read_file` per
    # path is the only correct way to get bytes, which is why this loop looks
    # redundant and is not.
    rows = await files.list_files(db, doc_id)
    entries: list[tuple[str, bytes]] = []
    for row in rows:
        full = await files.read_file(db, doc_id, row.path)
        if full is None:
            continue
        # `full.blob or b""` guards a NULL blob on a row the DB CHECK
        # constraint makes unreachable in practice (`is_binary` implies a
        # non-null `blob`) -- but `len(data)` in `_build_tar` would raise
        # TypeError on None and turn one such row into a 500 for the whole
        # compile instead of just that file. The guard costs nothing.
        entries.append(
            (
                full.path,
                (full.blob or b"") if full.is_binary else (full.content or "").encode("utf-8"),
            )
        )
    await db.commit()

    # tree_hash, over the WHOLE tree -- a chapter edit must invalidate the
    # cached build. A hash over the main file's bytes alone would return a
    # stale PDF forever after editing anything but the main file.
    key = files.tree_hash(entries, engine, main_path)
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

    result = await compile_tree(entries, engine, main_path)
    if not result.ok or result.pdf is None:
        # No hash on failure: the client keeps the PDF it already has, so a
        # broken edit never blanks the preview.
        return CompileOut(ok=False, log=result.log, pdf_hash=None)

    cache.put(
        key,
        CachedBuild(
            pdf=result.pdf,
            synctex_gz=result.synctex_gz,
            log=result.log,
            root=result.root,
            main_path=main_path,
        ),
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

    # `file` defaults to the LAST COMPILED build's main_path, not the
    # document's current one: the map answers for what was actually built,
    # and a repointed main_path since then would name a file this build
    # never compiled. This is also what keeps the existing single-file
    # frontend (which sends no `file` at all) working unchanged.
    #
    # Normalized the same way `point.file` is guarded on the reverse side:
    # it is the only path-shaped string in this subsystem that reaches the
    # compiler without a length bound or a normalize_path pass otherwise.
    # Not exploitable -- `synctex view -i` never opens the named file, so an
    # absolute or traversal-shaped string just fails to match a box -- but
    # this is defence in depth and consistency with the reverse side, not a
    # response to a real hole.
    try:
        file = normalize_path(payload.file) if payload.file is not None else build.main_path
    except InvalidPath:
        return SynctexForwardOut(found=False)
    position = await synctex_forward(
        build.pdf,
        build.synctex_gz,
        root=build.root,
        main_path=build.main_path,
        file=file,
        line=payload.line,
    )
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

    point = await synctex_reverse(
        build.pdf,
        build.synctex_gz,
        root=build.root,
        main_path=build.main_path,
        page=payload.page,
        x=payload.x,
        y=payload.y,
    )
    if point is None:
        return SynctexReverseOut(found=False)
    # The compiler cannot know the current tree -- it answers from the PDF
    # and the map alone -- but it can hand back a build artifact (main.toc,
    # main.aux) as if it were a source file, or a source file the editor has
    # since deleted. A file the editor cannot open is worse than no answer,
    # so the backend is where this gets filtered: only report a hit for a
    # path that actually exists in the document's tree right now.
    try:
        existing = await files.read_file(db, doc_id, point.file)
    except InvalidPath:
        # point.file is untyped input from a separately deployed image; a
        # shape read_file's own normalize_path rejects is the same "cannot
        # open it" case as a file that is simply gone.
        return SynctexReverseOut(found=False)
    if existing is None:
        return SynctexReverseOut(found=False)
    return SynctexReverseOut(found=True, line=point.line, file=point.file)
