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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.identity import get_current_user
from app.db.models import LatexDocument, User
from app.db.session import get_session
from app.schemas.latex import (
    LatexFileContentOut,
    LatexFileOut,
    LatexFileRename,
    LatexFileWrite,
    LatexTreeOut,
)
from app.services import latex_files_service as files
from app.services import project_service
from app.services.latex_paths import InvalidPath, normalize_path

router = APIRouter(tags=["latex"])

_BASE = "/projects/{project_id}/latex/{document_id}"


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
    raise exc


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
