"""The file tree behind a LaTeX document.

Every mutation bumps `latex_documents.revision`. That integer is what tells
the editor its PDF is stale, and it must move for a delete or a binary upload
exactly as it does for an edit -- a content comparison over the open buffers
would miss both.

Nothing here commits. Callers own the transaction, because the compile route
has to read the tree and END the transaction before its external call, and a
service that committed underneath it would break that.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.config import settings
from app.db.models import LatexDocument, LatexFile
from app.services.latex_paths import collision_key, normalize_path


class QuotaExceeded(Exception):
    """A write would take the document past a byte cap."""

    def __init__(self, used: int, cap: int) -> None:
        super().__init__(f"{used} bytes exceeds the {cap} byte cap")
        self.used = used
        self.cap = cap


class TooManyFiles(Exception):
    def __init__(self, count: int, cap: int) -> None:
        super().__init__(f"{count} files exceeds the {cap} file cap")
        self.count = count
        self.cap = cap


class PathCollision(Exception):
    """The path is taken, or differs from a taken one only by case."""

    def __init__(self, path: str, existing: str) -> None:
        super().__init__(f"{path!r} collides with existing {existing!r}")
        self.path = path
        self.existing = existing


class FileNotFound(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(f"{path!r} is not in this document")
        self.path = path


async def list_files(db: AsyncSession, document_id: str) -> list[LatexFile]:
    """Every file in the tree, sorted by path. Content columns deferred.

    Sorted in SQL rather than in the caller: the sidebar renders in this
    order, and two callers sorting differently is two different trees.

    `content`/`blob` are deferred -- a caller that needs a file's bytes must
    go through `read_file`, which loads a single row. Without this, listing
    a 2000-file / 25MB tree ships and ORM-hydrates every blob just to render
    a sidebar.
    """
    rows = await db.execute(
        select(LatexFile)
        .where(LatexFile.document_id == document_id)
        .order_by(LatexFile.path)
        .options(defer(LatexFile.content), defer(LatexFile.blob))
    )
    return list(rows.scalars().all())


async def read_file(db: AsyncSession, document_id: str, path: str) -> LatexFile | None:
    row = await db.execute(
        select(LatexFile).where(
            LatexFile.document_id == document_id, LatexFile.path == normalize_path(path)
        )
    )
    return row.scalar_one_or_none()


async def used_bytes(
    db: AsyncSession, document_id: str, *, excluding_path: str | None = None
) -> int:
    """Bytes the tree currently occupies.

    `excluding_path` is what makes overwriting possible at the cap: without
    it, a project sitting exactly on 25MB could never be edited again, only
    deleted, because the new content is counted on top of the old.
    """
    stmt = select(func.coalesce(func.sum(LatexFile.size_bytes), 0)).where(
        LatexFile.document_id == document_id
    )
    if excluding_path is not None:
        stmt = stmt.where(LatexFile.path != excluding_path)
    return int((await db.execute(stmt)).scalar_one())


async def bump_revision(db: AsyncSession, document_id: str) -> None:
    """Move `latex_documents.revision`. Every mutation that changes what a
    compile would produce -- a file write, a delete, a rename, or (in
    `app.api.v1.latex.update_document`) repointing `main_path` or switching
    `engine` -- must call this. `revision` is the designated staleness
    signal a client compares against without recomputing any hash itself, so
    a change that forgets to bump it makes a stale PDF read as fresh.

    Core UPDATE: `onupdate=_now` on the model still fires, so updated_at
    moves too, and no ORM instance has to be loaded to do it.
    """
    await db.execute(
        update(LatexDocument)
        .where(LatexDocument.id == document_id)
        .values(revision=LatexDocument.revision + 1)
    )


# Old private name, kept as an alias in case anything still imports it.
_bump_revision = bump_revision


async def _guard_write(
    db: AsyncSession, document_id: str, path: str, size: int
) -> LatexFile | None:
    """Shared precondition check for both write paths. Returns the row being
    overwritten, or None for a create.

    Locks the document row first (`FOR UPDATE` on Postgres; SQLAlchemy's
    sqlite dialect compiles `with_for_update()` away entirely -- verified,
    not assumed -- so this is a no-op there rather than an error). Without
    it, two overlapping writes can each read the tree under the cap, both
    project themselves as within it, and both commit, taking the document
    over 25MB with neither write individually at fault.
    """
    if size > settings.latex_file_max_bytes:
        raise QuotaExceeded(size, settings.latex_file_max_bytes)

    await db.execute(
        select(LatexDocument.id).where(LatexDocument.id == document_id).with_for_update()
    )

    existing = await read_file(db, document_id, path)

    if existing is None:
        # Case-fold collision is checked only on CREATE: overwriting the
        # file at its own exact path is not a collision with itself.
        # Column-only query: `list_files` selects (and, pre-deferral, used
        # to ship) whole rows including blobs -- answering "does this path
        # collide" and "how many files" from that materialised every file's
        # content/blob on every single write.
        key = collision_key(path)
        taken = (
            (await db.execute(select(LatexFile.path).where(LatexFile.document_id == document_id)))
            .scalars()
            .all()
        )
        for other in taken:
            if collision_key(other) == key:
                raise PathCollision(path, other)
        if len(taken) >= settings.latex_max_files:
            raise TooManyFiles(len(taken) + 1, settings.latex_max_files)

    projected = await used_bytes(db, document_id, excluding_path=path) + size
    if projected > settings.latex_project_max_bytes:
        raise QuotaExceeded(projected, settings.latex_project_max_bytes)
    return existing


async def write_text(db: AsyncSession, document_id: str, path: str, content: str) -> LatexFile:
    path = normalize_path(path)
    size = len(content.encode("utf-8"))
    existing = await _guard_write(db, document_id, path, size)

    # Built complete on construction, not assigned field-by-field after
    # `db.add` -- a row with is_binary's column default (False) and both
    # content and blob still None satisfies neither arm of the DB's
    # content-xor-blob CHECK. That half-built state used to survive only
    # because nothing queried the session between `add` and these
    # assignments to trigger autoflush; `_guard_write`'s own lock/collision
    # queries now sit in exactly that window.
    if existing is None:
        existing = LatexFile(
            document_id=document_id,
            path=path,
            is_binary=False,
            content=content,
            blob=None,
            size_bytes=size,
        )
        db.add(existing)
    else:
        existing.is_binary = False
        existing.content = content
        existing.blob = None
        existing.size_bytes = size
    await db.flush()
    await _bump_revision(db, document_id)
    return existing


async def write_binary(db: AsyncSession, document_id: str, path: str, data: bytes) -> LatexFile:
    path = normalize_path(path)
    existing = await _guard_write(db, document_id, path, len(data))

    if existing is None:
        existing = LatexFile(
            document_id=document_id,
            path=path,
            is_binary=True,
            content=None,
            blob=data,
            size_bytes=len(data),
        )
        db.add(existing)
    else:
        existing.is_binary = True
        existing.content = None
        existing.blob = data
        existing.size_bytes = len(data)
    await db.flush()
    await _bump_revision(db, document_id)
    return existing


async def delete_file(db: AsyncSession, document_id: str, path: str) -> bool:
    """True if a file was removed. A miss is NOT an error -- the caller turns
    it into a 404 with the context it has."""
    row = await read_file(db, document_id, path)
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    await _bump_revision(db, document_id)
    return True


async def rename_file(db: AsyncSession, document_id: str, src: str, dst: str) -> LatexFile:
    src = normalize_path(src)
    dst = normalize_path(dst)
    row = await read_file(db, document_id, src)
    if row is None:
        raise FileNotFound(src)
    if src != dst:
        key = collision_key(dst)
        taken = (
            (await db.execute(select(LatexFile.path).where(LatexFile.document_id == document_id)))
            .scalars()
            .all()
        )
        for other in taken:
            if other != src and collision_key(other) == key:
                raise PathCollision(dst, other)
    row.path = dst
    await db.flush()
    await _bump_revision(db, document_id)
    return row


def tree_hash(entries: Sequence[tuple[str, bytes]], engine: str, main_path: str) -> str:
    """Cache key for a compiled tree.

    Same property the single-file `source_hash` had: identical input cannot
    produce a different PDF. The engine and the main file are part of it
    because the same bytes compiled by xelatex, or with a different root
    file, are a different document.

    Hashes per-file DIGESTS rather than concatenated content, so the key is
    computable without holding 25MB in memory twice. Every field is NUL
    separated: without a separator, ('ab', '') and ('a', 'b') hash the same.
    """
    digest = hashlib.sha256()
    digest.update(engine.encode("utf-8"))
    digest.update(b"\0")
    digest.update(main_path.encode("utf-8"))
    digest.update(b"\0")
    for path, data in sorted(entries):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
        digest.update(b"\0")
    return digest.hexdigest()
