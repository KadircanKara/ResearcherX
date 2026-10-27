"""Per-project bm25 index state: what changed, when, and which version is live.

Every path that changes a project's chunks calls `mark_dirty` inside the same
transaction, before its commit, so the dirty flag and the change cannot
disagree. The background builder asks `due_projects` which projects have been
quiet long enough, snapshots `last_change_at`, builds, then `swap_version`
publishes the result and clears the flag only if nothing changed meanwhile.

Dialect-neutral on purpose (sqlite in tests, Postgres in prod): UPDATE first,
INSERT in a savepoint when no row exists, and on a lost insert race fall back
to the UPDATE.
"""

from datetime import datetime, timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bm25IndexState, _now


async def _touch(db: AsyncSession, project_id: str, now: datetime) -> int:
    """Stamp an existing row; returns how many rows it matched."""
    result = await db.execute(
        update(Bm25IndexState)
        .where(Bm25IndexState.project_id == project_id)
        .values(
            dirty_since=func.coalesce(Bm25IndexState.dirty_since, now),
            last_change_at=now,
        )
    )
    return result.rowcount


async def mark_dirty(db: AsyncSession, project_id: str) -> None:
    """Record that `project_id`'s chunks changed. Does NOT commit.

    `dirty_since` keeps the FIRST unindexed change; `last_change_at` always
    moves to now. Never raises on a concurrent first insert.
    """
    now = _now()
    if await _touch(db, project_id, now):
        return
    try:
        async with db.begin_nested():
            db.add(Bm25IndexState(project_id=project_id, dirty_since=now, last_change_at=now))
            await db.flush()
    except IntegrityError:
        # Someone else created the row since our UPDATE; the savepoint rolled
        # back only the INSERT, so the caller's chunk work is intact.
        await _touch(db, project_id, now)


async def due_projects(db: AsyncSession, *, quiet_s: int, now: datetime) -> list[str]:
    """Projects with unindexed changes and no change for `quiet_s` seconds."""
    cutoff = now - timedelta(seconds=quiet_s)
    rows = await db.execute(
        select(Bm25IndexState.project_id)
        .where(Bm25IndexState.dirty_since.is_not(None), Bm25IndexState.last_change_at <= cutoff)
        .order_by(Bm25IndexState.last_change_at)
    )
    return [r[0] for r in rows.all()]


async def swap_version(
    db: AsyncSession,
    project_id: str,
    *,
    new_version: int,
    snapshot_change_at: datetime,
    chunk_count: int,
    tokenizer_ver: int,
) -> bool:
    """Publish a built version. Does NOT commit.

    One UPDATE: the new version always lands, but `dirty_since` is cleared only
    when `last_change_at` still equals the snapshot the build started from.
    Returns True when it cleared; False when a change arrived during the build
    (a second build is due) or the project has no state row.
    """
    row = (
        await db.execute(
            update(Bm25IndexState)
            .where(Bm25IndexState.project_id == project_id)
            .values(
                current_version=new_version,
                built_at=_now(),
                chunk_count=chunk_count,
                tokenizer_ver=tokenizer_ver,
                dirty_since=case(
                    (Bm25IndexState.last_change_at == snapshot_change_at, None),
                    else_=Bm25IndexState.dirty_since,
                ),
            )
            .returning(Bm25IndexState.dirty_since)
        )
    ).first()
    return row is not None and row[0] is None
