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
import time
import urllib.parse
import zipfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.identity import get_current_user
from app.db.models import LatexDocument, LatexFile, User
from app.db.session import get_session
from app.schemas.latex import (
    LatexCollisionOut,
    LatexFileContentOut,
    LatexFileOut,
    LatexFileRename,
    LatexFileWrite,
    LatexImportCommit,
    LatexImportOut,
    LatexImportPlanOut,
    LatexMutationOut,
    LatexNameCollisionOut,
    LatexTreeOut,
)
from app.services import latex_access
from app.services import latex_dedupe
from app.services import latex_files_service as files
from app.services import latex_import_service
from app.services import project_service
from app.services.latex_archive import (
    ArchiveEntry,
    ArchiveTooLarge,
    EncryptedArchive,
    InvalidArchive,
    read_archive,
)
from app.services.latex_detect import AmbiguousMain, NoMainFile
from app.services.latex_paths import MANIFEST_PATH, InvalidPath, normalize_path
from app.services.latex_staging import (
    StagedImport,
    StagingExpired,
    StagingNotFound,
    staging,
)

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
        # A structured detail, following the `ambiguous_main` precedent in
        # this same module: the client needs the suggestion to offer "Keep
        # both", and a sentence cannot carry it.
        return HTTPException(
            status_code=409,
            detail={
                "error": "path_collision",
                "collisions": [
                    {
                        "path": exc.path,
                        "existing": exc.existing,
                        "suggestion": exc.suggestion,
                    }
                ],
            },
        )
    if isinstance(exc, files.FileNotFound):
        return HTTPException(status_code=404, detail=f"{exc.path} is not in this document")
    if isinstance(exc, files.InvalidEncoding):
        return HTTPException(status_code=422, detail=f"{exc.path} is not valid UTF-8 text")
    raise exc


async def _read_archive_body(request: Request) -> list[ArchiveEntry]:
    """The streamed, bounded, validated read.

    Unchanged from the single-shot route it was lifted out of: the body is
    streamed against a running counter rather than `await request.body()`
    because a client can lie in Content-Length, or send chunked with no
    Content-Length at all, and the counter is the only real bound. Every
    traversal, symlink, encryption and decompression-bomb guard lives behind
    `read_archive`, which runs in a threadpool under `_import_semaphore`.
    """
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
            return await run_in_threadpool(read_archive, blob)
    except ArchiveTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (EncryptedArchive, InvalidArchive) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _require_main_in_archive(entries: list[ArchiveEntry], main_path: str) -> None:
    """The same guard the manifest's own `main_path` gets in the service: it
    must name an entry in THIS archive, and that entry must be usable as
    source. Applied wherever a caller names a main file -- the plan step and
    the commit step both -- so neither can hand the service a path it will
    fail to find."""
    chosen = next((e for e in entries if e.path == main_path), None)
    if chosen is None:
        raise HTTPException(status_code=422, detail=f"{main_path} is not in the archive")
    if chosen.is_binary or not chosen.path.endswith(".tex"):
        raise HTTPException(
            status_code=422,
            detail=f"{main_path} is not a .tex source file in the archive",
        )


@router.post("/projects/{project_id}/latex/import/plan", response_model=LatexImportPlanOut)
async def import_plan_route(
    project_id: str,
    request: Request,
    document_id: str | None = None,
    name: str = Query("Imported project", min_length=1, max_length=200),
    main_path: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexImportPlanOut:
    """Upload the archive ONCE, and answer with everything the client must
    ask the user about: colliding files, a duplicate document name, and an
    undecidable main file.

    The parsed entries are parked in `latex_staging` so the commit step
    carries a token instead of the archive again. A merge collides by
    definition -- it is the common path, not the rare one -- so making the
    client re-upload on collision would make every merge cost two uploads.
    """
    await project_service.require_member(db, project_id, user.id, "member")
    entries = await _read_archive_body(request)
    if main_path is not None:
        _require_main_in_archive(entries, main_path)

    collisions: list[latex_dedupe.Collision] = []
    name_collision: LatexNameCollisionOut | None = None
    ambiguous: list[str] | None = None

    if document_id is not None:
        mode = "merge"
        await latex_access.require(db, project_id, document_id, user.id, need="editor")
        await _document_or_404(db, project_id, document_id)
        taken = [f.path for f in await files.list_files(db, document_id)]
        collisions = latex_import_service.plan_merge(taken, entries)
    else:
        mode = "create"
        existing_names = (
            (
                await db.execute(
                    select(LatexDocument.name).where(LatexDocument.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        suggestion = latex_dedupe.suffix_name(name, existing_names)
        if suggestion != name:
            name_collision = LatexNameCollisionOut(name=name, suggestion=suggestion)
        try:
            latex_import_service.detect_main_for(entries, override=main_path)
        except AmbiguousMain as exc:
            # Reported as a FIELD, not a 422: the plan step is where the
            # client learns everything it must ask the user about, and
            # asking twice in two different shapes is worse. An archive with
            # NO main file at all is different -- there is nothing to ask.
            ambiguous = exc.paths
        except NoMainFile as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    token = staging.put(
        StagedImport(project_id=project_id, user_id=user.id, entries=entries),
        now=time.monotonic(),
    )
    return LatexImportPlanOut(
        staging_id=token,
        mode=mode,
        file_count=len(entries),
        collisions=[
            LatexCollisionOut(path=c.path, existing=c.existing, suggestion=c.suggestion)
            for c in collisions
        ],
        name_collision=name_collision,
        ambiguous_main=ambiguous,
    )


@router.post(
    "/projects/{project_id}/latex/import/commit",
    response_model=LatexImportOut,
    status_code=201,
)
async def import_commit_route(
    project_id: str,
    payload: LatexImportCommit,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexImportOut:
    """Redeem a staging token and write the tree.

    Every `new_path` a decision names goes through `normalize_path` and the
    reserved-manifest check exactly like any other write: a decision is user
    input from the same untrusted surface as the archive itself.
    """
    await project_service.require_member(db, project_id, user.id, "member")
    try:
        staged = staging.take(payload.staging_id, project_id, user.id, now=time.monotonic())
    except StagingExpired as exc:
        raise HTTPException(
            status_code=410,
            detail="That upload expired. Please choose the .zip again.",
        ) from exc
    except StagingNotFound as exc:
        raise HTTPException(status_code=404, detail="Unknown upload.") from exc

    archive_paths = {e.path for e in staged.entries}
    renames: dict[str, str] = {}
    for decision in payload.decisions:
        if decision.path not in archive_paths:
            raise HTTPException(status_code=422, detail=f"{decision.path} is not in the archive")
        try:
            target = normalize_path(decision.new_path)
        except InvalidPath as exc:
            raise _translate(exc) from exc
        if target == MANIFEST_PATH:
            raise HTTPException(
                status_code=422, detail=f"{target} is reserved for the export manifest"
            )
        renames[decision.path] = target

    if payload.document_id is not None:
        await latex_access.require(db, project_id, payload.document_id, user.id, need="editor")
        document = await _document_or_404(db, project_id, payload.document_id)
        try:
            count = await latex_import_service.merge_archive(
                db,
                document_id=document.id,
                entries=staged.entries,
                renames=renames,
            )
        except Exception as exc:
            await db.rollback()
            raise _translate(exc) from exc
    else:
        # A merge never touches the document's main file, so this guard
        # belongs to the create path only -- refusing a `main_path` a merge
        # ignores would reject a request that is otherwise fine.
        if payload.main_path is not None:
            _require_main_in_archive(staged.entries, payload.main_path)
        try:
            document, count = await latex_import_service.import_archive(
                db,
                project_id=project_id,
                user_id=user.id,
                entries=staged.entries,
                name=payload.name or "Imported project",
                main_path=payload.main_path,
                renames=renames,
            )
        except AmbiguousMain as exc:
            # Defensive: `plan` already told the client the main file was
            # undecidable, in a shape it could act on. Reaching here means
            # the client committed without answering.
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
    # Export is a READ of the whole tree, so a viewer may take a copy. The
    # grant governs changing the document, not whether a member who can
    # already open every file may download them together.
    await latex_access.require(db, project_id, document_id, user.id)
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


async def _mutation_out(
    db: AsyncSession, document_id: str, row: LatexFile | None
) -> LatexMutationOut:
    """Read back the revision the mutation just produced.

    `bump_revision` is a core UPDATE, so the new value is not on any loaded
    ORM instance -- it has to be re-SELECTed. Called BEFORE `db.commit()` in
    every route, inside the same transaction that did the mutation, so the
    number returned is the one that mutation produced and not a later
    writer's.
    """
    revision = (
        await db.execute(select(LatexDocument.revision).where(LatexDocument.id == document_id))
    ).scalar_one()
    return LatexMutationOut(
        file=LatexFileOut.model_validate(row) if row is not None else None,
        revision=int(revision),
        used_bytes=await files.used_bytes(db, document_id),
    )


@router.get(f"{_BASE}/files", response_model=LatexTreeOut)
async def list_tree(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexTreeOut:
    await latex_access.require(db, project_id, document_id, user.id)
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
    await latex_access.require(db, project_id, document_id, user.id)
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


@router.put(f"{_BASE}/file", response_model=LatexMutationOut)
async def write_file(
    project_id: str,
    document_id: str,
    path: str,
    payload: LatexFileWrite,
    if_exists: Literal["fail", "replace"] = "fail",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexMutationOut:
    await latex_access.require(db, project_id, document_id, user.id, need="editor")
    await _document_or_404(db, project_id, document_id)
    try:
        row = await files.write_text(db, document_id, path, payload.content, if_exists=if_exists)
    except Exception as exc:
        await db.rollback()
        raise _translate(exc) from exc
    out = await _mutation_out(db, document_id, row)
    await db.commit()
    return out


@router.post(f"{_BASE}/file/binary", response_model=LatexMutationOut)
async def write_binary_file(
    project_id: str,
    document_id: str,
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexMutationOut:
    await latex_access.require(db, project_id, document_id, user.id, need="editor")
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
    # Also checked before streaming, not just relied on inside
    # `files.write_binary`: a user file at this exact path would make
    # export emit a duplicate zip member and silently shadow the user's own
    # content on both sides of a round trip.
    if normalized == MANIFEST_PATH:
        raise HTTPException(
            status_code=422, detail=f"{normalized} is reserved for the export manifest"
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
    # No `db.refresh` here (unlike the text routes): `SessionLocal` sets
    # `expire_on_commit=False`, so attributes are not expired by the commit,
    # and a refresh would re-SELECT up to 10MB of `blob` just to serialize
    # four scalar fields -- doubling peak memory on exactly the path the
    # streaming byte counter exists to bound. `_mutation_out` deliberately
    # does not refresh either, for the same reason.
    out = await _mutation_out(db, document_id, row)
    await db.commit()
    return out


@router.delete(f"{_BASE}/file", status_code=200, response_model=LatexMutationOut)
async def delete_file(
    project_id: str,
    document_id: str,
    path: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexMutationOut:
    await latex_access.require(db, project_id, document_id, user.id, need="editor")
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
    out = await _mutation_out(db, document_id, None)
    await db.commit()
    return out


@router.post(f"{_BASE}/file/rename", response_model=LatexMutationOut)
async def rename_file(
    project_id: str,
    document_id: str,
    payload: LatexFileRename,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LatexMutationOut:
    await latex_access.require(db, project_id, document_id, user.id, need="editor")
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
    out = await _mutation_out(db, document_id, row)
    await db.commit()
    return out
