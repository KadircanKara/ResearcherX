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
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.config import settings
from app.db.models import LatexDocument, LatexFile
from app.services import latex_dedupe
from app.services.latex_paths import MANIFEST_PATH, InvalidPath, collision_key, normalize_path


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
    """The path is taken, or differs from a taken one only by case.

    Carries `suggestion` -- the `(n)` name the user can accept with one click
    -- because a refusal with no way forward is what this exception used to
    be, and a dead-end 409 is why the case-collision rule read as a bug.
    """

    def __init__(self, path: str, existing: str, suggestion: str) -> None:
        super().__init__(f"{path!r} collides with existing {existing!r}")
        self.path = path
        self.existing = existing
        self.suggestion = suggestion


class FileNotFound(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(f"{path!r} is not in this document")
        self.path = path


class InvalidEncoding(Exception):
    """`is_binary=False` was claimed for bytes that are not valid UTF-8."""

    def __init__(self, path: str) -> None:
        super().__init__(f"{path!r} is not valid UTF-8 text")
        self.path = path


def _reject_reserved(path: str) -> None:
    """`MANIFEST_PATH` is reserved for the export round trip (see its
    docstring in `latex_paths.py`). A user file at that exact path would
    make export emit a duplicate zip member and silently shadow the user's
    own content -- so every write path that can CREATE or RENAME a file
    checks this, on the already-normalized path so a denormalized spelling
    can't sneak past it."""
    if path == MANIFEST_PATH:
        raise InvalidPath(path, "reserved for the export manifest")


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
    db: AsyncSession,
    document_id: str,
    path: str,
    size: int,
    if_exists: Literal["fail", "replace"],
) -> LatexFile | None:
    """Shared precondition check for both write paths. Returns the row being
    overwritten, or None for a create.

    Locks the document row first (`FOR UPDATE` on Postgres; SQLAlchemy's
    sqlite dialect compiles `with_for_update()` away entirely -- verified,
    not assumed -- so this is a no-op there rather than an error). Without
    it, two overlapping writes can each read the tree under the cap, both
    project themselves as within it, and both commit, taking the document
    over 25MB with neither write individually at fault.

    `if_exists` governs only an EXACT-path collision. A case-only collision
    -- the incoming path differs from a taken one only by case fold -- is
    refused regardless of `if_exists`: "replace" means "replace THIS file",
    and a case-folded neighbour is a different file the caller never named.
    """
    if size > settings.latex_file_max_bytes:
        raise QuotaExceeded(size, settings.latex_file_max_bytes)

    await db.execute(
        select(LatexDocument.id).where(LatexDocument.id == document_id).with_for_update()
    )

    existing = await read_file(db, document_id, path)

    # Every taken path, once. Needed for the collision decision AND for the
    # suggestion, so the two cannot disagree about what is taken. Column-only
    # query: `list_files` ships whole rows including blobs, and answering
    # "is this path free" from that materialises every file's content on
    # every single write.
    taken = (
        (await db.execute(select(LatexFile.path).where(LatexFile.document_id == document_id)))
        .scalars()
        .all()
    )

    if existing is not None and if_exists == "fail":
        raise PathCollision(path, existing.path, latex_dedupe.suffix_path(path, taken))

    if existing is None:
        # Case-fold collision is checked only on CREATE: overwriting the
        # file at its own exact path is not a collision with itself.
        key = collision_key(path)
        for other in taken:
            if collision_key(other) == key:
                raise PathCollision(path, other, latex_dedupe.suffix_path(path, taken))
        if len(taken) >= settings.latex_max_files:
            raise TooManyFiles(len(taken) + 1, settings.latex_max_files)

    projected = await used_bytes(db, document_id, excluding_path=path) + size
    if projected > settings.latex_project_max_bytes:
        raise QuotaExceeded(projected, settings.latex_project_max_bytes)
    return existing


async def write_text(
    db: AsyncSession,
    document_id: str,
    path: str,
    content: str,
    *,
    if_exists: Literal["fail", "replace"] = "fail",
) -> LatexFile:
    """Create or overwrite a text file.

    `if_exists` defaults to FAIL, and that default is load-bearing. Autosave
    -- the one legitimate overwrite in this system -- passes "replace"
    explicitly, so a call site that forgets the parameter breaks loudly with
    a 409 instead of silently blanking a chapter. A default of "replace"
    would preserve the data-loss bug for every future caller.
    """
    path = normalize_path(path)
    _reject_reserved(path)
    size = len(content.encode("utf-8"))
    existing = await _guard_write(db, document_id, path, size, if_exists)

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


async def write_binary(
    db: AsyncSession,
    document_id: str,
    path: str,
    data: bytes,
    *,
    if_exists: Literal["fail", "replace"] = "fail",
) -> LatexFile:
    """Create or overwrite a binary file. See `write_text` for why the
    default is FAIL."""
    path = normalize_path(path)
    _reject_reserved(path)
    existing = await _guard_write(db, document_id, path, len(data), if_exists)

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


async def bulk_create(
    db: AsyncSession, document_id: str, entries: Sequence[tuple[str, bytes, bool]]
) -> int:
    """Insert every file of a FRESHLY CREATED document in one pass.

    Valid only for a document whose tree is empty: there is nothing to
    collide with and nothing to add to, so the per-write collision scan and
    per-write quota SUM that `write_text` performs are both vacuous here.
    Bumps `revision` ONCE -- an import is one change, not one per file.

    Do NOT use this to add files to an existing document; it does not check
    for collisions against rows already present.

    Still enforces the total-bytes and per-count caps in Python over
    `entries` -- defence in depth, not reliance on the archive reader having
    already done it.

    PRECONDITION callers must uphold: `is_binary=False` means `data` decodes
    as UTF-8. `latex_archive.classify_binary` is what establishes this today.
    The decode below still does not TRUST that blindly -- it is guarded, so a
    caller that violates the precondition gets a typed `InvalidEncoding`
    (translated to a 422) rather than an unhandled `UnicodeDecodeError`
    surfacing as a 500 mid-transaction. Never falls back to
    `errors="replace"`: that would silently persist corrupted source instead
    of refusing it.
    """
    total = 0
    count = 0
    for path, data, _is_binary in entries:
        total += len(data)
        count += 1
        if total > settings.latex_project_max_bytes:
            raise QuotaExceeded(total, settings.latex_project_max_bytes)
        if count > settings.latex_max_files:
            raise TooManyFiles(count, settings.latex_max_files)

    for path, data, is_binary in entries:
        normalized = normalize_path(path)
        content: str | None = None
        if not is_binary:
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise InvalidEncoding(normalized) from exc
        db.add(
            LatexFile(
                document_id=document_id,
                path=normalized,
                is_binary=is_binary,
                content=content,
                blob=data if is_binary else None,
                size_bytes=len(data),
            )
        )
    await db.flush()
    await _bump_revision(db, document_id)
    return count


async def bulk_merge(
    db: AsyncSession, document_id: str, entries: Sequence[tuple[str, bytes, bool]]
) -> int:
    """Insert many files into an EXISTING, non-empty tree in one pass.

    `bulk_create`'s sibling, and deliberately a separate function rather than
    a flag on it: `bulk_create`'s whole justification is that a freshly
    created document has nothing to collide with and nothing to add to, so
    its missing collision scan and missing quota SUM are vacuous. Neither is
    vacuous here.

    `entries` carry FINAL paths. Every collision has already been resolved by
    the caller (`latex_dedupe.plan_writes` plus the user's decisions); a path
    that is still taken when it reaches here is a caller bug and raises
    rather than overwriting.

    One lock, one taken-paths read, one cap check, one revision bump -- a
    merge is one change, and doing any of these per file costs O(n) round
    trips on an import of a real project.
    """
    await db.execute(
        select(LatexDocument.id).where(LatexDocument.id == document_id).with_for_update()
    )

    taken = (
        (await db.execute(select(LatexFile.path).where(LatexFile.document_id == document_id)))
        .scalars()
        .all()
    )
    taken_keys = {collision_key(t): t for t in taken}

    total = await used_bytes(db, document_id)
    count = len(taken)
    for path, data, _is_binary in entries:
        normalized = normalize_path(path)
        _reject_reserved(normalized)
        key = collision_key(normalized)
        if key in taken_keys:
            raise PathCollision(
                normalized,
                taken_keys[key],
                latex_dedupe.suffix_path(normalized, taken_keys.values()),
            )
        taken_keys[key] = normalized
        if len(data) > settings.latex_file_max_bytes:
            raise QuotaExceeded(len(data), settings.latex_file_max_bytes)
        total += len(data)
        count += 1
        if total > settings.latex_project_max_bytes:
            raise QuotaExceeded(total, settings.latex_project_max_bytes)
        if count > settings.latex_max_files:
            raise TooManyFiles(count, settings.latex_max_files)

    written = 0
    for path, data, is_binary in entries:
        normalized = normalize_path(path)
        content: str | None = None
        if not is_binary:
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise InvalidEncoding(normalized) from exc
        db.add(
            LatexFile(
                document_id=document_id,
                path=normalized,
                is_binary=is_binary,
                content=content,
                blob=data if is_binary else None,
                size_bytes=len(data),
            )
        )
        written += 1

    await db.flush()
    await _bump_revision(db, document_id)
    return written


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
    _reject_reserved(dst)
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
                raise PathCollision(dst, other, latex_dedupe.suffix_path(dst, taken))
    row.path = dst
    await db.flush()
    await _bump_revision(db, document_id)
    return row


async def rename_dir(db: AsyncSession, document_id: str, src: str, dst: str) -> int:
    """Move every file under the `src` prefix to sit under `dst`. Returns the
    count moved. Caller commits.

    There is no directory ROW to rename -- `latex_files.path` is a flat
    string and a directory exists only as the shared prefix of the files
    beneath it. So this is N renames, and it is one route rather than N
    client calls for the reason the archive parser refuses a partial
    import: a tree left half-moved is a project silently missing the file
    its `\\input` names, and nothing tells the user which half moved.

    All-or-nothing in one transaction: every destination is computed and
    checked BEFORE any row is written, so a collision on the last file
    leaves the first untouched.
    """
    src = normalize_path(src)
    dst = normalize_path(dst)
    _reject_reserved(dst)
    if src == dst:
        return 0

    await db.execute(
        select(LatexDocument.id).where(LatexDocument.id == document_id).with_for_update()
    )
    rows = (
        (await db.execute(select(LatexFile).where(LatexFile.document_id == document_id)))
        .scalars()
        .all()
    )

    prefix = f"{src}/"
    moving = [r for r in rows if r.path.startswith(prefix)]
    if not moving:
        raise FileNotFound(src)

    # Refused rather than silently flattened: `chapters` -> `chapters/intro`
    # would move every file INTO a subdirectory of itself, and the
    # destination prefix would keep sliding as the source is consumed.
    if dst.startswith(prefix):
        raise InvalidPath(dst, "a directory cannot be moved inside itself")

    # Only paths staying put can be collided with. A file moving out of the
    # way is not an obstacle to the file taking its place, which is what
    # makes renaming `a` -> `b` work when `b` does not exist yet.
    staying = {collision_key(r.path): r.path for r in rows if not r.path.startswith(prefix)}
    planned: list[tuple[LatexFile, str]] = []
    for row in moving:
        target = normalize_path(f"{dst}/{row.path[len(prefix) :]}")
        _reject_reserved(target)
        key = collision_key(target)
        if key in staying:
            raise PathCollision(target, staying[key], latex_dedupe.suffix_path(target, staying))
        staying[key] = target
        planned.append((row, target))

    for row, target in planned:
        row.path = target
    await db.flush()
    await _bump_revision(db, document_id)
    return len(planned)


def tree_hash(entries: Sequence[tuple[str, bytes]], engine: str, main_path: str) -> str:
    """Cache key for a compiled tree.

    Same property the retired single-file hash had: identical input cannot
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
