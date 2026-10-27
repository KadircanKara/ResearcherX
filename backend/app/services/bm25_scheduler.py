"""Background rebuilds of the per-project bm25s indexes.

A lifespan task (`run_forever`) calls `tick` every `bm25_tick_s`. Each tick
marks projects whose index is missing or was built by an older tokenizer,
then rebuilds every project that has been quiet for `bm25_quiet_period_s`,
one at a time. Per project:

1. Take `pg_try_advisory_lock` on a DEDICATED connection held for the whole
   build; another process (a dev reload, a script) already building it means
   skip, not wait.
2. Under the lock, read `current_version` and snapshot `last_change_at`.
3. Run `python -m app.services.bm25_build` as a subprocess, so tokenising a
   large corpus never holds the API's event loop or memory.
4. Only after it exits 0 AND the version directory holds `DONE`, publish with
   `bm25_state.swap_version`. The swap clears the dirty flag only if nothing
   changed since the snapshot; a paper ingested during the build leaves the
   project dirty and the next quiet tick builds again.
5. Garbage-collect versions other than the current and the previous one.

Every tick also sweeps ORPHANS: a project deleted while idle loses its state
row (it cascades) but not its directory, which nothing would ever visit again.
`sweep_orphans` removes a UUID-named directory whose project no longer exists
once it is older than `bm25_gc_grace_s`, under that project's advisory lock.

A failed, killed or timed-out build publishes nothing and leaves the project
dirty, and its half-written `.tmp` is removed. Correctness never depends on
the single uvicorn worker: cross-process state is the `bm25_index_state` row
and the advisory lock.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text

from app.core import observability
from app.core.config import settings
from app.core.logging import log
from app.db.models import Bm25IndexState, Project, _now
from app.db.session import SessionLocal, engine
from app.services import bm25_state, bm25_store
from app.services.bm25_build import DONE
from app.services.bm25_tokenizer import TOKENIZER_CONFIG

# A failed or timed-out build is not retried until its backoff expires, so a
# project that always fails cannot pay a full rebuild every tick or hold the
# serial queue for `bm25_build_timeout_s` each time. In-process on purpose: a
# second process only repeats an attempt, and the advisory lock serialises it.
BACKOFF_BASE_S = 60
BACKOFF_MAX_S = 3600
_backoff: dict[str, tuple[int, float]] = {}  # project_id -> (failures, next attempt)
_clock = time.monotonic

_VERSION_TMP = re.compile(r"^\d+\.tmp(\..*)?$")

# The directory `-m app.services.bm25_build` must run from.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_STDERR_TAIL = 2000

_LOCK_SQL = text("SELECT pg_try_advisory_lock(hashtext('bm25:' || :project_id))")
_UNLOCK_SQL = text("SELECT pg_advisory_unlock(hashtext('bm25:' || :project_id))")

# Projects whose index is missing or stale and that nothing has marked yet.
# Already-dirty rows are left alone: `mark_dirty` moves `last_change_at`, so
# re-marking on every tick would restart the debounce forever.
#
# An `EMBEDDING_MODEL` change is deliberately NOT a stale trigger here: the
# state row records no model. After a switch the published index holds only
# old-model chunk ids, every one of which serving filters out, so the sparse
# arm is empty (turns are dense-only) until `scripts/reindex_papers` re-embeds
# the papers -- its `index_chunks` marks each project dirty, and the next quiet
# tick rebuilds. The switch needs that re-index anyway, so nothing is lost.
_STALE_SQL = text("""
    SELECT pr.id AS project_id
    FROM projects pr
    LEFT JOIN bm25_index_state s ON s.project_id = pr.id
    WHERE (s.project_id IS NULL OR s.dirty_since IS NULL)
      AND (
        (
          (s.project_id IS NULL OR s.current_version = 0)
          AND EXISTS (
            SELECT 1
            FROM paper_chunk_embeddings c
            JOIN papers p ON p.id = c.paper_id
            WHERE c.project_id = pr.id AND c.model = :model AND p.status = 'ready'
          )
        )
        OR (s.current_version > 0 AND s.tokenizer_ver <> :tokenizer_ver)
      )
""")


async def _mark_stale() -> int:
    async with SessionLocal() as db:
        rows = await db.execute(
            _STALE_SQL,
            {"model": settings.embedding_model, "tokenizer_ver": TOKENIZER_CONFIG["version"]},
        )
        ids = [r.project_id for r in rows.all()]
        # A published version whose files are gone (a lost volume, a build
        # written inside a container that was then recreated) would leave the
        # project dense-only until its next chunk change.
        published = await db.execute(
            select(Bm25IndexState.project_id, Bm25IndexState.current_version).where(
                Bm25IndexState.current_version > 0, Bm25IndexState.dirty_since.is_(None)
            )
        )
        root = Path(settings.bm25_index_dir)
        ids += [
            pid
            for pid, version in published.all()
            if pid not in ids and not (root / pid / str(version) / DONE).is_file()
        ]
        for project_id in ids:
            await bm25_state.mark_dirty(db, project_id)
        await db.commit()
    if ids:
        log.info("bm25_projects_marked_stale", count=len(ids))
    return len(ids)


async def mark_unindexed_projects() -> int:
    """Mark every project with ready chunks but no built index, an index from
    an older tokenizer, or a published version missing on disk. Run once at
    startup (every tick repeats it); returns how many."""
    count = await _mark_stale()
    log.info("bm25_mark_unindexed_projects", count=count)
    return count


@asynccontextmanager
async def advisory_lock(project_id: str) -> AsyncIterator[bool]:
    """Yield whether this process holds the project's build lock.

    Session-level, so the transaction that took it is committed at once and
    the connection does not sit idle in a transaction for the whole build.
    Unlocked on the SAME connection; if that fails, the connection is thrown
    away, which releases the lock with it.
    """
    async with engine.connect() as conn:
        try:
            got = bool((await conn.execute(_LOCK_SQL, {"project_id": project_id})).scalar())
            await conn.commit()
        except BaseException:
            # The lock may be held with nothing left to release it: drop the
            # connection rather than return a locked session to the pool.
            await conn.invalidate()
            raise
        if not got:
            yield False
            return
        try:
            yield True
        finally:
            try:
                await conn.execute(_UNLOCK_SQL, {"project_id": project_id})
                await conn.commit()
            except BaseException as exc:
                log.warning("bm25_unlock_failed", project_id=project_id, exc_info=True)
                await conn.invalidate()
                if not isinstance(exc, Exception):
                    raise


def build_argv(project_id: str, version: int, root: Path) -> list[str]:
    return [sys.executable, "-m", "app.services.bm25_build", project_id, str(version), str(root)]


def backoff_delay(failures: int) -> float:
    return min(BACKOFF_BASE_S * 2 ** (failures - 1), BACKOFF_MAX_S)


def _record_failure(project_id: str) -> None:
    failures = _backoff.get(project_id, (0, 0.0))[0] + 1
    delay = backoff_delay(failures)
    _backoff[project_id] = (failures, _clock() + delay)
    log.warning("bm25_build_backoff", project_id=project_id, failures=failures, delay_s=delay)


def _backing_off(project_id: str) -> bool:
    entry = _backoff.get(project_id)
    return entry is not None and entry[1] > _clock()


def _remove_child_tmp(root: Path, version: int, pid: int) -> None:
    """Remove the `.tmp` of THIS child only; another build's is not ours."""
    for d in root.glob(f"{version}.tmp.{pid}.*"):
        shutil.rmtree(d, ignore_errors=True)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    await proc.wait()


async def run_build(project_id: str, version: int, root: Path) -> int | None:
    """Run the build subprocess; the chunk count on success, None on failure.

    stderr is captured and logged only on failure: bm25s writes progress noise
    there, so its presence means nothing.
    """
    proc = await asyncio.create_subprocess_exec(
        *build_argv(project_id, version, root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=_BACKEND_ROOT,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=settings.bm25_build_timeout_s)
    except TimeoutError:
        await _kill(proc)
        _remove_child_tmp(root, version, proc.pid)
        log.warning(
            "bm25_build_timeout",
            project_id=project_id,
            timeout_s=settings.bm25_build_timeout_s,
        )
        return None
    except BaseException:
        # Cancelled (shutdown): the build must not outlive the app.
        await _kill(proc)
        _remove_child_tmp(root, version, proc.pid)
        raise
    if proc.returncode != 0:
        _remove_child_tmp(root, version, proc.pid)
        log.warning(
            "bm25_build_failed",
            project_id=project_id,
            returncode=proc.returncode,
            stderr=err.decode(errors="replace")[-_STDERR_TAIL:],
        )
        return None
    try:
        return int(out.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        log.warning("bm25_build_bad_output", project_id=project_id, stdout=out[-200:])
        return None


def gc_old_versions(project_dir: Path, keep: set[int], now: float, grace_s: int) -> list[Path]:
    """Delete version directories not in `keep`, and leftover `<v>.tmp.*`
    directories of dead or killed builds, once their mtime is older than
    `grace_s`. Anything else is left alone."""
    if not project_dir.is_dir():
        return []
    removed = []
    for d in sorted(project_dir.iterdir(), key=lambda p: p.name):
        if not d.is_dir():
            continue
        if d.name.isdigit():
            if int(d.name) in keep:
                continue
        elif not _VERSION_TMP.match(d.name):
            continue
        if now - d.stat().st_mtime < grace_s:
            continue
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d)
    return removed


async def _build_locked(project_id: str) -> bool:
    async with SessionLocal() as db:
        state = await db.get(Bm25IndexState, project_id)
    if state is None or state.dirty_since is None:
        return False  # another process built it between due_projects and the lock
    version = (state.current_version or 0) + 1
    snapshot = state.last_change_at  # passed to the swap exactly as read
    root = Path(settings.bm25_index_dir) / project_id
    # A version this number may exist unpublished: a build whose swap never
    # landed. Nothing can be reading it, and the build refuses to overwrite.
    # Other builds' `.tmp.*` directories are not touched; GC ages them out.
    shutil.rmtree(root / str(version), ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    ok = False
    cleared = False
    gone = False
    count: int | None = None
    with observability.span("bm25.build", attributes={"project_id": project_id}) as s:
        try:
            count = await run_build(project_id, version, root)
            built = count is not None and (root / str(version) / DONE).is_file()
            if built:
                async with SessionLocal() as db:
                    cleared = await bm25_state.swap_version(
                        db,
                        project_id,
                        new_version=version,
                        snapshot_change_at=snapshot,
                        chunk_count=count,
                        tokenizer_ver=TOKENIZER_CONFIG["version"],
                    )
                    if not cleared:
                        # No row matched: the project was deleted mid-build
                        # (the state row cascades with it).
                        gone = await db.get(Bm25IndexState, project_id) is None
                    await db.commit()
                ok = not gone
            if not ok:
                shutil.rmtree(root / str(version), ignore_errors=True)
        finally:
            duration = round(time.monotonic() - started, 3)
            observability.set_attributes(
                s, {"chunk_count": count, "duration_s": duration, "ok": ok}
            )
    if gone:
        shutil.rmtree(root, ignore_errors=True)
        _backoff.pop(project_id, None)
        log.info("bm25_project_gone", project_id=project_id, version=version)
        return False
    log.info(
        "bm25_build_done" if ok else "bm25_build_not_published",
        project_id=project_id,
        version=version,
        chunk_count=count,
        duration_s=duration,
        still_dirty=ok and not cleared,
    )
    if not ok:
        _record_failure(project_id)
        return False
    _backoff.pop(project_id, None)
    removed = gc_old_versions(
        root, {version, version - 1}, now=time.time(), grace_s=settings.bm25_gc_grace_s
    )
    if removed:
        log.info("bm25_versions_removed", project_id=project_id, count=len(removed))
    return True


async def build_project(project_id: str) -> bool:
    """Build and publish one project if its lock is free. True when published."""
    async with advisory_lock(project_id) as got:
        if not got:
            log.info("bm25_build_skipped_locked", project_id=project_id)
            return False
        return await _build_locked(project_id)


def _is_project_dir_name(name: str) -> bool:
    """Only a canonical UUID can be a project's directory; anything else under
    the index root is not ours to remove."""
    try:
        return str(uuid.UUID(name)) == name
    except ValueError:
        return False


async def _project_ids() -> set[str]:
    async with SessionLocal() as db:
        return set((await db.execute(select(Project.id))).scalars())


async def _project_exists(project_id: str) -> bool:
    async with SessionLocal() as db:
        return await db.get(Project, project_id) is not None


async def sweep_orphans(now: float) -> list[str]:
    """Remove index directories of projects that no longer exist.

    One query for the live ids and one listing of the root; only directories
    named by a canonical UUID, older than `bm25_gc_grace_s`, are candidates.
    Each is removed under its project's advisory lock, after re-checking the
    project still does not exist, so a build that just started for that id is
    never raced. A locked directory is left for a later tick.
    """
    root = Path(settings.bm25_index_dir)
    try:
        candidates = [d for d in root.iterdir() if _is_project_dir_name(d.name) and d.is_dir()]
    except FileNotFoundError:
        return []
    if not candidates:
        return []
    live = await _project_ids()
    removed = []
    for d in sorted(candidates, key=lambda p: p.name):
        project_id = d.name
        if project_id in live:
            continue
        try:
            if now - d.stat().st_mtime < settings.bm25_gc_grace_s:
                continue
        except FileNotFoundError:
            continue
        async with advisory_lock(project_id) as got:
            if not got or await _project_exists(project_id):
                continue
            shutil.rmtree(d, ignore_errors=True)
        bm25_store.store.evict(project_id)
        _backoff.pop(project_id, None)
        removed.append(project_id)
    if removed:
        log.info("bm25_orphans_removed", count=len(removed))
    return removed


async def tick(now: datetime) -> list[str]:
    """One scheduler pass; returns the projects published this tick."""
    await _mark_stale()
    async with SessionLocal() as db:
        due = await bm25_state.due_projects(db, quiet_s=settings.bm25_quiet_period_s, now=now)
    built = []
    for project_id in due:  # one at a time, deliberately
        if _backing_off(project_id):
            continue
        try:
            if await build_project(project_id):
                built.append(project_id)
        except Exception:
            log.error("bm25_build_error", project_id=project_id, exc_info=True)
            _record_failure(project_id)
    try:
        await sweep_orphans(time.time())
    except Exception:  # housekeeping only: a sweep error never costs a tick
        log.warning("bm25_orphan_sweep_failed", exc_info=True)
    return built


async def run_forever() -> None:
    """Tick every `bm25_tick_s` until cancelled; a failing tick is logged and
    the loop carries on."""
    log.info("bm25_scheduler_started", tick_s=settings.bm25_tick_s)
    while True:
        try:
            await tick(_now())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("bm25_tick_failed", exc_info=True)
        await asyncio.sleep(settings.bm25_tick_s)
