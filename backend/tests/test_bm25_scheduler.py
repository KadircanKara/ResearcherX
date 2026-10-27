"""Background bm25 rebuilds: debounce, lock, atomic swap, GC.

sqlite has no advisory locks, so the lock is faked; the build subprocess is
faked except where the subprocess handling itself is under test.
"""

import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Bm25IndexState,
    Paper,
    PaperChunkEmbedding,
    PaperStatus,
    Project,
    ProjectMember,
    User,
)
from app.db.seed import seed_users
from app.db.session import SessionLocal
from app.services import bm25_scheduler, bm25_state
from app.services.bm25_tokenizer import TOKENIZER_CONFIG

LATER = timedelta(hours=1)


def _later() -> datetime:
    return datetime.now(timezone.utc) + LATER


async def _state(project_id: str) -> Bm25IndexState | None:
    async with SessionLocal() as db:
        return (
            await db.execute(select(Bm25IndexState).where(Bm25IndexState.project_id == project_id))
        ).scalar_one_or_none()


async def _new_project(db: AsyncSession, title: str, *, ready_chunk: bool = False) -> str:
    you = (await db.execute(select(User).where(User.email == "you@researcherx.dev"))).scalar_one()
    p = Project(owner_id=you.id, title=title, topic_keywords=[])
    db.add(p)
    await db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    if ready_chunk:
        paper = Paper(project_id=p.id, title=f"{title} paper", status=PaperStatus.READY)
        db.add(paper)
        await db.flush()
        db.add(
            PaperChunkEmbedding(
                paper_id=paper.id,
                project_id=p.id,
                chunk_index=0,
                text="swarm search",
                section_text="2 Method",
                embedding="[]",
                model=settings.embedding_model,
            )
        )
    await db.commit()
    return p.id


async def _dirty(project_id: str) -> None:
    async with SessionLocal() as db:
        await bm25_state.mark_dirty(db, project_id)
        await db.commit()


@pytest.fixture
async def users(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


@pytest.fixture(autouse=True)
def no_backoff_leak():
    bm25_scheduler._backoff.clear()
    yield
    bm25_scheduler._backoff.clear()


@pytest.fixture
def index_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "bm25_index_dir", str(tmp_path))
    return tmp_path


@pytest.fixture
def lock(monkeypatch):
    """Fake advisory lock: projects in `held` are locked by someone else."""
    held: set[str] = set()
    taken: list[str] = []

    @asynccontextmanager
    async def fake(project_id: str):
        if project_id in held:
            yield False
            return
        taken.append(project_id)
        yield True

    monkeypatch.setattr(bm25_scheduler, "advisory_lock", fake)
    return held, taken


@pytest.fixture
def builds(monkeypatch, index_dir):
    """Fake build subprocess: writes a complete version dir, returns a count."""
    calls: list[tuple[str, int]] = []
    behaviour: dict = {"count": 3, "during": None}

    async def fake(project_id: str, version: int, root: Path) -> int | None:
        calls.append((project_id, version))
        if behaviour["during"] is not None:
            await behaviour["during"](project_id)
        if behaviour["count"] is None:
            return None
        out = root / str(version)
        out.mkdir(parents=True)
        (out / "DONE").write_text("1\n")
        return behaviour["count"]

    monkeypatch.setattr(bm25_scheduler, "run_build", fake)
    return calls, behaviour


async def test_tick_builds_only_due_projects(users, db_session, lock, builds):
    calls, _ = builds
    due = await _new_project(db_session, "due")
    fresh = await _new_project(db_session, "fresh")
    await _dirty(due)
    # `fresh` changed just now; with `now` only 10s ahead it is still inside
    # the quiet period, while `due` is pushed back an hour.
    async with SessionLocal() as db:
        old = datetime.now(timezone.utc) - LATER
        await db.execute(
            update(Bm25IndexState)
            .where(Bm25IndexState.project_id == due)
            .values(dirty_since=old, last_change_at=old)
        )
        await db.commit()
    await _dirty(fresh)

    built = await bm25_scheduler.tick(datetime.now(timezone.utc) + timedelta(seconds=10))

    assert built == [due]
    assert calls == [(due, 1)]
    s = await _state(due)
    assert (s.current_version, s.dirty_since, s.chunk_count) == (1, None, 3)
    assert s.tokenizer_ver == TOKENIZER_CONFIG["version"]
    assert (await _state(fresh)).dirty_since is not None


async def test_tick_skips_when_lock_is_held(users, db_session, lock, builds):
    held, _ = lock
    calls, _ = builds
    pid = await _new_project(db_session, "locked")
    await _dirty(pid)
    held.add(pid)

    assert await bm25_scheduler.tick(_later()) == []
    assert calls == []
    s = await _state(pid)
    assert s.dirty_since is not None and s.current_version == 0


async def test_failed_build_leaves_project_dirty(users, db_session, lock, builds, index_dir):
    calls, behaviour = builds
    behaviour["count"] = None
    pid = await _new_project(db_session, "fails")
    await _dirty(pid)

    assert await bm25_scheduler.tick(_later()) == []
    assert calls == [(pid, 1)]
    s = await _state(pid)
    assert s.dirty_since is not None and s.current_version == 0

    # The retry (after the backoff window) reuses the version number:
    # nothing was published.
    bm25_scheduler._backoff.clear()
    behaviour["count"] = 2
    assert await bm25_scheduler.tick(_later()) == [pid]
    assert calls[-1] == (pid, 1)


async def test_a_version_dir_without_done_is_never_published(
    users, db_session, lock, monkeypatch, index_dir
):
    async def no_done(project_id, version, root):
        (root / str(version)).mkdir(parents=True)
        return 4

    monkeypatch.setattr(bm25_scheduler, "run_build", no_done)
    pid = await _new_project(db_session, "half")
    await _dirty(pid)

    assert await bm25_scheduler.tick(_later()) == []
    s = await _state(pid)
    assert s.current_version == 0 and s.dirty_since is not None
    assert not (index_dir / pid / "1").exists()


async def test_change_during_build_triggers_second_build(users, db_session, lock, builds):
    calls, behaviour = builds
    pid = await _new_project(db_session, "busy")
    await _dirty(pid)

    async def ingest_meanwhile(project_id):
        behaviour["during"] = None  # only the first build sees a change
        await _dirty(project_id)

    behaviour["during"] = ingest_meanwhile
    assert await bm25_scheduler.tick(_later()) == [pid]
    s = await _state(pid)
    assert s.current_version == 1
    assert s.dirty_since is not None  # the change after the snapshot is unindexed

    assert await bm25_scheduler.tick(_later()) == [pid]
    assert calls == [(pid, 1), (pid, 2)]
    s = await _state(pid)
    assert (s.current_version, s.dirty_since) == (2, None)


async def test_a_successful_swap_garbage_collects_old_versions(
    users, db_session, lock, builds, index_dir, monkeypatch
):
    monkeypatch.setattr(settings, "bm25_gc_grace_s", 0)
    pid = await _new_project(db_session, "gc")
    for _ in range(3):
        await _dirty(pid)
        assert await bm25_scheduler.tick(_later()) == [pid]
    names = sorted(p.name for p in (index_dir / pid).iterdir())
    assert names == ["2", "3"]


def _version(root: Path, n: int, age_s: float, now: float) -> Path:
    d = root / str(n)
    d.mkdir()
    (d / "DONE").write_text("1\n")
    os.utime(d, (now - age_s, now - age_s))
    return d


def test_gc_keeps_current_and_previous(tmp_path: Path):
    now = time.time()
    for n in (1, 2, 3, 4):
        _version(tmp_path, n, 3600, now)
    (tmp_path / "notes").mkdir()  # not a version: never touched

    removed = bm25_scheduler.gc_old_versions(tmp_path, {3, 4}, now=now, grace_s=600)

    assert sorted(p.name for p in removed) == ["1", "2"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["3", "4", "notes"]


def test_gc_waits_for_grace(tmp_path: Path):
    now = time.time()
    _version(tmp_path, 1, 3600, now)
    _version(tmp_path, 2, 60, now)
    _version(tmp_path, 3, 0, now)
    _version(tmp_path, 4, 0, now)

    removed = bm25_scheduler.gc_old_versions(tmp_path, {3, 4}, now=now, grace_s=600)

    assert [p.name for p in removed] == ["1"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2", "3", "4"]


def test_gc_of_a_missing_dir_is_a_no_op(tmp_path: Path):
    assert bm25_scheduler.gc_old_versions(tmp_path / "nope", {1}, now=time.time(), grace_s=0) == []


async def test_mark_unindexed_projects_marks_ready_projects_with_no_version(users, db_session):
    with_chunks = await _new_project(db_session, "chunks", ready_chunk=True)
    empty = await _new_project(db_session, "empty")
    # A row that exists but was never built counts as unindexed too.
    zero = await _new_project(db_session, "zero", ready_chunk=True)
    async with SessionLocal() as db:
        db.add(Bm25IndexState(project_id=zero, current_version=0))
        await db.commit()

    assert await bm25_scheduler.mark_unindexed_projects() == 2

    assert (await _state(with_chunks)).dirty_since is not None
    assert (await _state(zero)).dirty_since is not None
    assert await _state(empty) is None
    # Idempotent: an already-dirty row is not re-stamped (that would reset
    # its debounce on every call).
    before = (await _state(with_chunks)).last_change_at
    assert await bm25_scheduler.mark_unindexed_projects() == 0
    assert (await _state(with_chunks)).last_change_at == before


async def test_tokenizer_version_mismatch_marks_dirty(users, db_session, lock, builds):
    calls, _ = builds
    pid = await _new_project(db_session, "tok")
    current = await _new_project(db_session, "current")
    async with SessionLocal() as db:
        db.add_all(
            [
                Bm25IndexState(
                    project_id=pid,
                    current_version=4,
                    tokenizer_ver=TOKENIZER_CONFIG["version"] - 1,
                ),
                Bm25IndexState(
                    project_id=current,
                    current_version=4,
                    tokenizer_ver=TOKENIZER_CONFIG["version"],
                ),
            ]
        )
        await db.commit()
    for project_id in (pid, current):
        (Path(settings.bm25_index_dir) / project_id / "4").mkdir(parents=True)
        (Path(settings.bm25_index_dir) / project_id / "4" / "DONE").write_text("1\n")

    assert await bm25_scheduler.mark_unindexed_projects() == 1
    assert (await _state(pid)).dirty_since is not None
    assert (await _state(current)).dirty_since is None

    # A tick marks a mismatch too (a tokenizer bump while the app runs on an
    # older row), and the rebuild then lands under the current version.
    async with SessionLocal() as db:
        await db.execute(
            update(Bm25IndexState).where(Bm25IndexState.project_id == pid).values(dirty_since=None)
        )
        await db.commit()
    await bm25_scheduler.tick(datetime.now(timezone.utc))  # marks; not yet quiet
    assert (await _state(pid)).dirty_since is not None
    assert await bm25_scheduler.tick(_later()) == [pid]
    s = await _state(pid)
    assert (s.current_version, s.tokenizer_ver) == (5, TOKENIZER_CONFIG["version"])
    assert calls == [(pid, 5)]


async def test_run_build_kills_a_build_that_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "bm25_build_timeout_s", 1.0)
    other = tmp_path / "1.tmp.1.someone-else"  # another build's: not ours to remove
    other.mkdir()
    script = (
        "import os, pathlib, sys, time;"
        "pathlib.Path(sys.argv[1], f'1.tmp.{os.getpid()}.x').mkdir();"
        "time.sleep(30)"
    )
    monkeypatch.setattr(
        bm25_scheduler,
        "build_argv",
        lambda pid, version, root: [sys.executable, "-c", script, str(root)],
    )
    started = time.monotonic()
    assert await bm25_scheduler.run_build("p", 1, tmp_path) is None
    assert time.monotonic() - started < 10
    assert [p.name for p in tmp_path.iterdir()] == [other.name]


async def test_run_build_parses_the_count_and_ignores_stderr(tmp_path, monkeypatch):
    script = "import sys; sys.stderr.write('DEBUG noise\\n'); print(42)"
    monkeypatch.setattr(
        bm25_scheduler, "build_argv", lambda pid, version, root: [sys.executable, "-c", script]
    )
    assert await bm25_scheduler.run_build("p", 1, tmp_path) == 42


async def test_run_build_treats_a_nonzero_exit_as_failure(tmp_path, monkeypatch):
    script = "import sys; print(7); sys.exit(1)"
    monkeypatch.setattr(
        bm25_scheduler, "build_argv", lambda pid, version, root: [sys.executable, "-c", script]
    )
    assert await bm25_scheduler.run_build("p", 1, tmp_path) is None


def test_build_argv_runs_the_build_module(tmp_path):
    argv = bm25_scheduler.build_argv("p", 3, tmp_path)
    assert argv == [sys.executable, "-m", "app.services.bm25_build", "p", "3", str(tmp_path)]


async def test_run_forever_survives_a_failing_tick(monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "bm25_tick_s", 0)
    seen = []

    async def flaky(now):
        seen.append(now)
        if len(seen) == 1:
            raise RuntimeError("db down")
        if len(seen) == 3:
            raise asyncio.CancelledError
        return []

    monkeypatch.setattr(bm25_scheduler, "tick", flaky)
    with pytest.raises(asyncio.CancelledError):
        await bm25_scheduler.run_forever()
    assert len(seen) == 3


async def test_each_build_is_one_root_span(users, db_session, lock, builds):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.core import observability

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observability.install_provider(provider)
    try:
        pid = await _new_project(db_session, "traced")
        await _dirty(pid)
        await bm25_scheduler.tick(_later())
    finally:
        observability.install_provider(None)

    (s,) = [s for s in exporter.get_finished_spans() if s.name == "bm25.build"]
    assert s.parent is None
    assert s.attributes["project_id"] == pid
    assert s.attributes["chunk_count"] == 3
    assert s.attributes["ok"] is True
    assert s.attributes["duration_s"] >= 0


def test_the_scheduler_needs_postgres_and_the_setting(monkeypatch):
    from app import main
    from app.services.task_registry import registry

    assert settings.bm25_scheduler_enabled is False  # conftest
    monkeypatch.setattr(settings, "bm25_scheduler_enabled", True)
    assert main.bm25_scheduler_wanted() is False  # sqlite: no advisory locks
    main._start_bm25_scheduler()
    assert registry.get("bm25-scheduler") is None


async def test_a_published_version_missing_on_disk_is_marked_dirty(users, db_session, index_dir):
    # The row says version 3 is live, but the files are gone (a lost volume,
    # a build written inside a container that was then recreated).
    lost = await _new_project(db_session, "lost")
    kept = await _new_project(db_session, "kept")
    tv = TOKENIZER_CONFIG["version"]
    async with SessionLocal() as db:
        db.add_all(
            [
                Bm25IndexState(project_id=lost, current_version=3, tokenizer_ver=tv),
                Bm25IndexState(project_id=kept, current_version=3, tokenizer_ver=tv),
            ]
        )
        await db.commit()
    (index_dir / kept / "3").mkdir(parents=True)
    (index_dir / kept / "3" / "DONE").write_text("1\n")

    assert await bm25_scheduler.mark_unindexed_projects() == 1
    assert (await _state(lost)).dirty_since is not None
    assert (await _state(kept)).dirty_since is None


# ── fix round 1 ──────────────────────────────────────────────────────────


async def test_a_failing_project_backs_off_without_blocking_others(
    users, db_session, lock, monkeypatch, index_dir
):
    clock = {"t": 1000.0}
    monkeypatch.setattr(bm25_scheduler, "_clock", lambda: clock["t"])
    calls = []
    bad = await _new_project(db_session, "bad")
    good = await _new_project(db_session, "good")

    async def fake(project_id, version, root):
        calls.append(project_id)
        if project_id == bad:
            return None
        out = root / str(version)
        out.mkdir(parents=True)
        (out / "DONE").write_text("1\n")
        return 1

    monkeypatch.setattr(bm25_scheduler, "run_build", fake)
    await _dirty(bad)  # older change: sorts first
    await _dirty(good)

    assert await bm25_scheduler.tick(_later()) == [good]
    assert calls == [bad, good]

    # Inside the first backoff window (~60s) the failing project is skipped.
    await _dirty(good)
    clock["t"] += 30
    assert await bm25_scheduler.tick(_later()) == [good]
    assert calls == [bad, good, good]

    # After it, one more attempt; the next window doubles.
    clock["t"] += 31
    assert await bm25_scheduler.tick(_later()) == []
    assert calls[-1] == bad
    n = len(calls)
    clock["t"] += 119  # the second window is 120s
    assert await bm25_scheduler.tick(_later()) == []
    assert len(calls) == n
    clock["t"] += 2
    await bm25_scheduler.tick(_later())
    assert len(calls) == n + 1


def test_backoff_doubles_and_caps():
    assert [bm25_scheduler.backoff_delay(n) for n in (1, 2, 3, 7, 30)] == [
        60,
        120,
        240,
        3600,
        3600,
    ]


async def test_a_successful_build_resets_the_backoff(users, db_session, lock, builds, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(bm25_scheduler, "_clock", lambda: clock["t"])
    calls, behaviour = builds
    pid = await _new_project(db_session, "flaky")
    await _dirty(pid)
    behaviour["count"] = None
    await bm25_scheduler.tick(_later())
    await bm25_scheduler.tick(_later())  # backing off
    assert len(calls) == 1
    clock["t"] += 61
    behaviour["count"] = 2
    assert await bm25_scheduler.tick(_later()) == [pid]
    assert pid not in bm25_scheduler._backoff
    await _dirty(pid)
    assert await bm25_scheduler.tick(_later()) == [pid]  # no window left over


async def test_a_swap_that_raises_is_reported_not_ok(users, db_session, lock, builds, monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.core import observability

    async def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(bm25_state, "swap_version", boom)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observability.install_provider(provider)
    try:
        pid = await _new_project(db_session, "swapfail")
        await _dirty(pid)
        assert await bm25_scheduler.tick(_later()) == []
    finally:
        observability.install_provider(None)
    (s,) = [s for s in exporter.get_finished_spans() if s.name == "bm25.build"]
    assert s.attributes["ok"] is False
    assert (await _state(pid)).current_version == 0


async def test_a_project_deleted_mid_build_drops_its_index_dir(
    users, db_session, lock, builds, index_dir
):
    from sqlalchemy import delete

    calls, behaviour = builds
    pid = await _new_project(db_session, "doomed")
    await _dirty(pid)

    async def delete_project(project_id):
        async with SessionLocal() as db:
            await db.execute(delete(Bm25IndexState).where(Bm25IndexState.project_id == project_id))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.commit()

    behaviour["during"] = delete_project
    assert await bm25_scheduler.tick(_later()) == []
    assert calls == [(pid, 1)]
    assert not (index_dir / pid).exists()
    assert pid not in bm25_scheduler._backoff


def test_gc_removes_stale_tmp_dirs_after_grace(tmp_path: Path):
    now = time.time()
    old_tmp = tmp_path / "5.tmp.123.abcd"
    fresh_tmp = tmp_path / "6.tmp.456.ef01"
    for d, age in ((old_tmp, 3600), (fresh_tmp, 10)):
        d.mkdir()
        os.utime(d, (now - age, now - age))
    _version(tmp_path, 4, 3600, now)

    removed = bm25_scheduler.gc_old_versions(tmp_path, {4}, now=now, grace_s=600)

    assert [p.name for p in removed] == [old_tmp.name]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["4", fresh_tmp.name]


class _FakeConn:
    def __init__(self, *, lock_result=True, fail_commit_at=None, fail_unlock=False):
        self.lock_result = lock_result
        self.fail_commit_at = fail_commit_at
        self.fail_unlock = fail_unlock
        self.statements: list[str] = []
        self.commits = 0
        self.invalidated = False

    async def execute(self, stmt, params):
        sql = str(stmt)
        self.statements.append("unlock" if "unlock" in sql else "lock")
        if "unlock" in sql and self.fail_unlock:
            raise RuntimeError("connection lost")

        class R:
            def scalar(_):
                return self.lock_result

        return R()

    async def commit(self):
        self.commits += 1
        if self.fail_commit_at == self.commits:
            raise RuntimeError("commit failed")

    async def invalidate(self):
        self.invalidated = True


class _FakeEngine:
    def __init__(self, conn):
        self.conn = conn

    def connect(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(_):
                return conn

            async def __aexit__(_, *exc):
                return False

        return Ctx()


async def test_advisory_lock_unlocks_when_the_body_raises(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(bm25_scheduler, "engine", _FakeEngine(conn))
    with pytest.raises(ValueError):
        async with bm25_scheduler.advisory_lock("p") as got:
            assert got is True
            raise ValueError
    assert conn.statements == ["lock", "unlock"]
    assert not conn.invalidated


async def test_advisory_lock_invalidates_when_the_commit_after_acquire_raises(monkeypatch):
    conn = _FakeConn(fail_commit_at=1)
    monkeypatch.setattr(bm25_scheduler, "engine", _FakeEngine(conn))
    with pytest.raises(RuntimeError):
        async with bm25_scheduler.advisory_lock("p"):
            pytest.fail("body must not run")
    assert conn.invalidated


async def test_advisory_lock_invalidates_when_unlock_fails(monkeypatch):
    conn = _FakeConn(fail_unlock=True)
    monkeypatch.setattr(bm25_scheduler, "engine", _FakeEngine(conn))
    async with bm25_scheduler.advisory_lock("p") as got:
        assert got
    assert conn.invalidated


async def test_advisory_lock_not_taken_yields_false(monkeypatch):
    conn = _FakeConn(lock_result=False)
    monkeypatch.setattr(bm25_scheduler, "engine", _FakeEngine(conn))
    async with bm25_scheduler.advisory_lock("p") as got:
        assert got is False
    assert conn.statements == ["lock"]


async def test_run_build_cancelled_kills_the_child_and_its_tmp(tmp_path, monkeypatch):
    import asyncio

    pids = []
    script = (
        "import os, pathlib, sys, time;"
        "pathlib.Path(sys.argv[1], f'1.tmp.{os.getpid()}.x').mkdir();"
        "print(os.getpid(), flush=True); time.sleep(30)"
    )
    monkeypatch.setattr(
        bm25_scheduler,
        "build_argv",
        lambda pid, version, root: [sys.executable, "-c", script, str(root)],
    )
    real = asyncio.create_subprocess_exec

    async def spy(*a, **k):
        proc = await real(*a, **k)
        pids.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    task = asyncio.create_task(bm25_scheduler.run_build("p", 1, tmp_path))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if any(tmp_path.glob("1.tmp.*")):
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pids[0].returncode is not None
    assert not list(tmp_path.glob("1.tmp.*"))


# ── final review: orphan sweep (I2) ──────────────────────────────────────


def _aged_dir(path: Path, age_s: float) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    then = time.time() - age_s
    os.utime(path, (then, then))
    return path


@pytest.fixture
def fresh_store(monkeypatch):
    from app.services import bm25_store

    s = bm25_store.Bm25Store()
    monkeypatch.setattr(bm25_store, "store", s)
    return s


async def test_an_idle_deleted_projects_dir_is_swept(
    users, db_session, lock, index_dir, fresh_store
):
    import uuid

    live = await _new_project(db_session, "live")
    orphan = str(uuid.uuid4())
    young_orphan = str(uuid.uuid4())
    _aged_dir(index_dir / live / "1", 3600)
    _aged_dir(index_dir / live, 3600)
    _aged_dir(index_dir / orphan / "1", 3600)
    _aged_dir(index_dir / orphan, 3600)
    _aged_dir(index_dir / young_orphan, 10)
    _aged_dir(index_dir / "not-a-uuid", 3600)
    (index_dir / str(uuid.uuid4())).write_text("a file, not a project dir")
    fresh_store._cache[orphan] = object()
    fresh_store._versions[orphan] = (0.0, 1)

    removed = await bm25_scheduler.sweep_orphans(now=time.time())

    assert removed == [orphan]
    assert not (index_dir / orphan).exists()
    assert (index_dir / live / "1").is_dir()
    assert (index_dir / young_orphan).is_dir()
    assert (index_dir / "not-a-uuid").is_dir()
    assert len([p for p in index_dir.iterdir() if p.is_file()]) == 1
    assert orphan not in fresh_store._cache and orphan not in fresh_store._versions
    _held, taken = lock
    assert taken == [orphan]


async def test_a_locked_orphan_is_left_for_a_later_tick(users, db_session, lock, index_dir):
    import uuid

    orphan = str(uuid.uuid4())
    _aged_dir(index_dir / orphan, 3600)
    held, _taken = lock
    held.add(orphan)

    assert await bm25_scheduler.sweep_orphans(now=time.time()) == []
    assert (index_dir / orphan).is_dir()


async def test_an_orphan_recreated_under_the_lock_is_kept(
    users, db_session, lock, index_dir, monkeypatch
):
    pid = await _new_project(db_session, "recreated")
    _aged_dir(index_dir / pid, 3600)

    # The id listing ran before the project existed; the re-check under the
    # lock must see it and keep the directory.
    async def no_projects() -> set[str]:
        return set()

    monkeypatch.setattr(bm25_scheduler, "_project_ids", no_projects)
    assert await bm25_scheduler.sweep_orphans(now=time.time()) == []
    assert (index_dir / pid).is_dir()


async def test_a_missing_index_root_sweeps_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "bm25_index_dir", str(tmp_path / "absent"))
    assert await bm25_scheduler.sweep_orphans(now=time.time()) == []


async def test_a_failing_sweep_never_kills_the_tick(users, db_session, lock, builds, monkeypatch):
    calls, _ = builds

    async def boom(now):
        raise OSError("disk gone")

    monkeypatch.setattr(bm25_scheduler, "sweep_orphans", boom)
    pid = await _new_project(db_session, "still-builds")
    await _dirty(pid)
    assert await bm25_scheduler.tick(_later()) == [pid]
    assert calls == [(pid, 1)]


async def test_tick_runs_the_sweep(users, db_session, lock, index_dir, fresh_store):
    import uuid

    orphan = str(uuid.uuid4())
    _aged_dir(index_dir / orphan, 3600)
    await bm25_scheduler.tick(_later())
    assert not (index_dir / orphan).exists()
