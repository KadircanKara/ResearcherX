"""File tree routes for a LaTeX document.

Paths travel as a QUERY PARAMETER, never in the URL path. A LaTeX path
contains slashes, and encoding them into the route makes every proxy in the
chain a participant in the escaping scheme.

Every service refusal maps to a status the client can act on: a bad path is a
422 naming the path, a cap is a 413 carrying the numbers, an occupied path is
a 409. These are the user's errors, not ours, so unlike compiler failures the
message is specific rather than sanitized.
"""

from __future__ import annotations

import asyncio
import io
import json
import urllib.parse
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.identity import get_current_user
from app.db.models import LatexDocument, User
from app.db.session import get_session
from app.schemas.latex import (
    LatexFileContentOut,
    LatexFileOut,
    LatexFileRename,
    LatexFileWrite,
    LatexImportOut,
    LatexTreeOut,
)
from app.services import latex_files_service as files
from app.services import latex_import_service
from app.services import project_service
from app.services.latex_archive import (
    ArchiveTooLarge,
    EncryptedArchive,
    InvalidArchive,
    read_archive,
)
from app.services.latex_detect import AmbiguousMain, NoMainFile
from app.services.latex_import_service import MANIFEST_PATH
from app.services.latex_paths import InvalidPath, normalize_path

router = APIRouter(tags=["latex"])

_BASE = "/projects/{project_id}/latex/{document_id}"

# Bounds concurrent archive PARSES only. `zipfile.ZipFile(...).infolist()`
# materialises the whole central directory before the entry-count guard can
# reject an oversized archive (measured: 8.1s / 152MB peak for a 24MB/270k
# entry archive, comfortably under the 25MB body cap). Running the parse in
# a threadpool (below) keeps it off the event loop; this semaphore keeps N
# concurrent parses from each holding ~152MB at once. Module-level and valid
# only because uvicorn runs a single worker -- see `_compile_semaphore` in
# `latex_compiler.py` for the same invariant and the same reason its tests
# patch in a fresh semaphore rather than contending this one (an
# asyncio.Semaphore binds to the event loop that first contends it, and
# pytest hands out a fresh loop per test).
_import_semaphore = asyncio.Semaphore(settings.latex_max_concurrent_imports)


async def _document_or_404(db: AsyncSession, project_id: str, document_id: str) -> LatexDocument:
    """Same contract as the document router: a document in another project is
    a 404, so the caller learns nothing about documents outside the project
    they asked about."""
    result = await db.execute(
        select(LatexDocument).where(
            LatexDocument.id == document_id, LatexDocument.project_id == project_id
        )
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, InvalidPath):
        return HTTPException(status_code=422, detail=f"{exc.path}: {exc.reason}")
    if isinstance(exc, files.QuotaExceeded):
        return HTTPException(
            status_code=413, detail=f"{exc.used} bytes exceeds the {exc.cap} byte limit"
        )
    if isinstance(exc, files.TooManyFiles):
        return HTTPException(
            status_code=413, detail=f"{exc.count} files exceeds the {exc.cap} file limit"
        )
    if isinstance(exc, files.PathCollision):
        return HTTPException(
            status_code=409, detail=f"{exc.path} collides with existing {exc.existing}"
        )
    if isinstance(exc, files.FileNotFound):
        return HTTPException(status_code=404, detail=f"{exc.path} is not in this document")
    if isinstance(exc, files.InvalidEncoding):
        return HTTPException(status_code=422, detail=f"{exc.path} is not valid UTF-8 text")
    raise exc


@router.post("/projects/{project_id}/latex/import", response_model=LatexImportOut, status_code=201)
async def import_archive_route(
    project_id: str,
    request: Request,
    name: str = Query("Imported project", min_length=1, max_length=200),
    main_path: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexImportOut:
    """Create a NEW document from an uploaded .zip. Never overwrites one.

    The body is streamed against a running counter rather than `await
    request.body()`: a client can lie in Content-Length, or send chunked with
    no Content-Length at all, and the counter is the only real bound.
    """
    await project_service.require_member(db, project_id, user.id, "editor")

    cap = settings.latex_project_max_bytes
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=413, detail=f"{total} bytes exceeds the {cap} byte limit"
            )
        chunks.append(chunk)

    blob = b"".join(chunks)
    try:
        async with _import_semaphore:
            entries = await run_in_threadpool(read_archive, blob)
    except ArchiveTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (EncryptedArchive, InvalidArchive) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if main_path is not None:
        chosen = next((e for e in entries if e.path == main_path), None)
        if chosen is None:
            raise HTTPException(status_code=422, detail=f"{main_path} is not in the archive")
        if chosen.is_binary or not chosen.path.endswith(".tex"):
            raise HTTPException(
                status_code=422,
                detail=f"{main_path} is not a .tex source file in the archive",
            )

    try:
        document, count = await latex_import_service.import_archive(
            db,
            project_id=project_id,
            user_id=user.id,
            entries=entries,
            name=name,
            main_path=main_path,
        )
    except AmbiguousMain as exc:
        await db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"error": "ambiguous_main", "candidates": exc.paths},
        ) from exc
    except NoMainFile as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        raise _translate(exc) from exc

    await db.commit()
    await db.refresh(document)
    return LatexImportOut(
        id=document.id,
        name=document.name,
        main_path=document.main_path,
        engine=document.engine,
        revision=document.revision,
        file_count=count,
    )


@router.get(f"{_BASE}/export")
async def export_archive_route(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """The document's tree as a .zip.

    Read with `viewer` rights: exporting reveals nothing a viewer cannot
    already read a file at a time.

    ZIP_DEFLATED rather than stored: a LaTeX project is mostly text, and the
    response is built in memory bounded by the same 25MB the tree is.
    """
    await project_service.require_member(db, project_id, user.id, "viewer")
    document = await _document_or_404(db, project_id, document_id)

    rows = await files.list_files(db, document_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            # list_files DEFERS content/blob -- touching them on these rows
            # raises MissingGreenlet. Re-read each file by path.
            full = await files.read_file(db, document_id, row.path)
            if full is None:
                continue
            archive.writestr(full.path, full.blob if full.is_binary else (full.content or ""))
        # Round-trip manifest: `main_path` and `engine` are decisions
        # detection cannot re-derive on its own (an ambiguity the user
        # already resolved, an engine set by PATCH with no triggering
        # package). Import consumes and discards this entry -- it never
        # lands in the re-imported tree. Its mere presence as a root-level
        # file also defeats the wrapper-stripping heuristic in
        # `latex_archive._common_prefix` (a root-level file already means
        # "don't strip", so a tree entirely under one directory, e.g.
        # `src/`, round-trips with its paths unchanged).
        archive.writestr(
            MANIFEST_PATH,
            json.dumps({"main_path": document.main_path, "engine": document.engine}),
        )
    await db.commit()

    raw = document.name or "project"
    # Control characters cannot appear in a header value at all -- uvicorn
    # rejects them AFTER the status line is sent, so the failure is a reset
    # connection rather than a clean error. Strip them, and the quote that
    # would end the quoted-string early.
    safe = "".join(c for c in raw if c.isprintable() and c != '"').strip() or "project"
    ascii_fallback = safe.encode("ascii", "ignore").decode() or "project"
    encoded = urllib.parse.quote(f"{safe}.zip", safe="")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_fallback}.zip\"; filename*=UTF-8''{encoded}"
            )
        },
    )


@router.get(f"{_BASE}/files", response_model=LatexTreeOut)
async def list_tree(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexTreeOut:
    await project_service.require_member(db, project_id, user.id, "viewer")
    await _document_or_404(db, project_id, document_id)
    rows = await files.list_files(db, document_id)
    return LatexTreeOut(
        files=[LatexFileOut.model_validate(r) for r in rows],
        used_bytes=sum(r.size_bytes for r in rows),
        max_bytes=settings.latex_project_max_bytes,
    )


@router.get(f"{_BASE}/file")
async def read_file(
    project_id: str,
    document_id: str,
    path: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await project_service.require_member(db, project_id, user.id, "viewer")
    await _document_or_404(db, project_id, document_id)
    try:
        row = await files.read_file(db, document_id, path)
    except InvalidPath as exc:
        raise _translate(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"{path} is not in this document")
    if row.is_binary:
        # Raw bytes, not base64 in JSON: the editor hands these straight to an
        # <img> or a download, and a 10MB file does not need a 4/3 inflation
        # to make that trip.
        return Response(
            content=row.blob or b"",
            media_type="application/octet-stream",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "attachment",
            },
        )
    return Response(
        content=LatexFileContentOut(path=row.path, content=row.content or "").model_dump_json(),
        media_type="application/json",
    )


@router.put(f"{_BASE}/file", response_model=LatexFileOut)
async def write_file(
    project_id: str,
    document_id: str,
    path: str,
    payload: LatexFileWrite,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexFileOut:
    await project_service.require_member(db, project_id, user.id, "editor")
    await _document_or_404(db, project_id, document_id)
    try:
        row = await files.write_text(db, document_id, path, payload.content)
    except Exception as exc:
        await db.rollback()
        raise _translate(exc) from exc
    await db.commit()
    await db.refresh(row)
    return LatexFileOut.model_validate(row)


@router.post(f"{_BASE}/file/binary", response_model=LatexFileOut)
async def write_binary_file(
    project_id: str,
    document_id: str,
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexFileOut:
    await project_service.require_member(db, project_id, user.id, "editor")
    document = await _document_or_404(db, project_id, document_id)

    try:
        normalized = normalize_path(path)
    except InvalidPath as exc:
        raise _translate(exc) from exc
    # Checked BEFORE the body is streamed, same invariant the delete route
    # already guards: overwriting the main file with binary bytes leaves the
    # document with no source at all, and no compile can recover it.
    if document.main_path == normalized:
        raise HTTPException(
            status_code=409,
            detail="That is the document's main file. Point main_path elsewhere first.",
        )

    # Streamed against a running counter rather than `await request.body()`:
    # a client that lies in Content-Length, or sends chunked (where there is
    # no Content-Length at all), must not be able to make this process hold
    # more than the per-file cap.
    cap = settings.latex_file_max_bytes
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=413, detail=f"{total} bytes exceeds the {cap} byte limit"
            )
        chunks.append(chunk)

    try:
        row = await files.write_binary(db, document_id, path, b"".join(chunks))
    except Exception as exc:
        await db.rollback()
        raise _translate(exc) from exc
    await db.commit()
    # No `db.refresh` here (unlike the text routes): `SessionLocal` sets
    # `expire_on_commit=False`, so attributes are not expired by the commit,
    # and a refresh would re-SELECT up to 10MB of `blob` just to serialize
    # four scalar fields -- doubling peak memory on exactly the path the
    # streaming byte counter exists to bound.
    return LatexFileOut.model_validate(row)


@router.delete(f"{_BASE}/file", status_code=204)
async def delete_file(
    project_id: str,
    document_id: str,
    path: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await project_service.require_member(db, project_id, user.id, "editor")
    document = await _document_or_404(db, project_id, document_id)
    try:
        normalized = normalize_path(path)
    except InvalidPath as exc:
        raise _translate(exc) from exc
    # Checked BEFORE anything is deleted. Deleting the root file would leave
    # the next compile failing with a confusing "file not found" from
    # latexmk; refusing here says so while the tree is still intact, and
    # avoids a delete-then-rollback that depends on the transaction unwinding
    # correctly to be safe.
    if document.main_path == normalized:
        raise HTTPException(
            status_code=409,
            detail="That is the document's main file. Point main_path elsewhere first.",
        )
    if not await files.delete_file(db, document_id, normalized):
        raise HTTPException(status_code=404, detail=f"{path} is not in this document")
    await db.commit()
    return Response(status_code=204)


@router.post(f"{_BASE}/file/rename", response_model=LatexFileOut)
async def rename_file(
    project_id: str,
    document_id: str,
    payload: LatexFileRename,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexFileOut:
    await project_service.require_member(db, project_id, user.id, "editor")
    document = await _document_or_404(db, project_id, document_id)
    # Normalized once, up front, and compared/passed as the normalized value:
    # `main_path` is always stored normalized, so comparing it against the
    # raw client string lets a denormalized spelling (`./main.tex`) rename
    # the row while leaving `main_path` pointing at a path that no longer
    # exists -- the same class of bug the delete route already guards
    # against for the exact same reason.
    try:
        src = normalize_path(payload.from_path)
    except InvalidPath as exc:
        raise _translate(exc) from exc
    try:
        row = await files.rename_file(db, document_id, src, payload.to_path)
        # The main file following its own rename is the only sane behaviour:
        # the alternative is a document whose main_path silently points at
        # nothing. But it must still be a .tex file afterwards -- the same
        # constraint `update_document` enforces on a direct main_path PATCH.
        if document.main_path == src:
            if not row.path.endswith(".tex"):
                raise HTTPException(status_code=422, detail="The main file must be a .tex file")
            document.main_path = row.path
    except Exception as exc:
        await db.rollback()
        raise _translate(exc) from exc
    await db.commit()
    await db.refresh(row)
    return LatexFileOut.model_validate(row)
