"""Per-project bm25 index state: dirty marking, debounce and the version swap."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bm25IndexState, Paper, Project, ProjectMember, User
from app.db.seed import seed_users
from app.db.session import SessionLocal
from app.services import bm25_state
from app.services import paper_ingest_service as svc
from app.services.structured_chunker import ChunkRecord


@pytest_asyncio.fixture
async def you(db_session: AsyncSession) -> User:
    await seed_users(db_session)
    await db_session.commit()
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Bm25", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def _state(project_id: str) -> Bm25IndexState | None:
    """Read through a NEW session: what is asserted is what was committed."""
    async with SessionLocal() as db:
        return (
            await db.execute(select(Bm25IndexState).where(Bm25IndexState.project_id == project_id))
        ).scalar_one_or_none()


def _embed_ok():
    async def embed(texts, task_type=None):
        return [[0.0] * 768 for _ in texts]

    return AsyncMock(side_effect=embed)


async def test_mark_dirty_sets_both_timestamps_once(db_session, project):
    await bm25_state.mark_dirty(db_session, project.id)
    await db_session.commit()

    s = await _state(project.id)
    assert s is not None
    assert s.dirty_since is not None and s.last_change_at is not None
    assert s.dirty_since == s.last_change_at
    assert s.current_version == 0 and s.chunk_count == 0 and s.tokenizer_ver == 0
    assert s.built_at is None


async def test_mark_dirty_keeps_first_dirty_since(db_session, project):
    await bm25_state.mark_dirty(db_session, project.id)
    await db_session.commit()
    first = await _state(project.id)

    await bm25_state.mark_dirty(db_session, project.id)
    await db_session.commit()
    second = await _state(project.id)

    assert second.dirty_since == first.dirty_since
    assert second.last_change_at > first.last_change_at


async def test_mark_dirty_survives_a_lost_insert_race(db_session, project):
    """Another transaction inserted the row between our UPDATE and INSERT: the
    PK violation must fall back to an UPDATE, not 500 the chunk write."""
    real_update_count = {"n": 0}
    original = bm25_state._touch

    async def racing_touch(db, project_id, now):
        real_update_count["n"] += 1
        if real_update_count["n"] == 1:
            # First attempt sees no row, though one exists by the time we INSERT.
            async with SessionLocal() as other:
                other.add(
                    Bm25IndexState(project_id=project_id, dirty_since=now, last_change_at=now)
                )
                await other.commit()
            return 0
        return await original(db, project_id, now)

    with patch.object(bm25_state, "_touch", racing_touch):
        await bm25_state.mark_dirty(db_session, project.id)
    await db_session.commit()

    assert real_update_count["n"] == 2
    assert (await _state(project.id)).dirty_since is not None


async def test_due_projects_waits_for_quiet_period(db_session, project):
    await bm25_state.mark_dirty(db_session, project.id)
    await db_session.commit()
    s = await _state(project.id)
    last = s.last_change_at.replace(tzinfo=timezone.utc)

    just_before = last + timedelta(seconds=59)
    assert await bm25_state.due_projects(db_session, quiet_s=60, now=just_before) == []
    at = last + timedelta(seconds=60)
    assert await bm25_state.due_projects(db_session, quiet_s=60, now=at) == [project.id]


async def test_due_projects_ignores_clean_projects(db_session, project):
    await bm25_state.mark_dirty(db_session, project.id)
    await db_session.commit()
    s = await _state(project.id)
    assert await bm25_state.swap_version(
        db_session,
        project.id,
        new_version=1,
        snapshot_change_at=s.last_change_at.replace(tzinfo=timezone.utc),
        chunk_count=3,
        tokenizer_ver=1,
    )
    await db_session.commit()
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert await bm25_state.due_projects(db_session, quiet_s=60, now=later) == []


async def test_swap_clears_dirty_only_if_unchanged(db_session, project):
    await bm25_state.mark_dirty(db_session, project.id)
    await db_session.commit()
    s = await _state(project.id)

    cleared = await bm25_state.swap_version(
        db_session,
        project.id,
        new_version=7,
        snapshot_change_at=s.last_change_at.replace(tzinfo=timezone.utc),
        chunk_count=12,
        tokenizer_ver=2,
    )
    await db_session.commit()

    assert cleared is True
    after = await _state(project.id)
    assert after.dirty_since is None
    assert (after.current_version, after.chunk_count, after.tokenizer_ver) == (7, 12, 2)
    assert after.built_at is not None


async def test_swap_keeps_dirty_when_changed_during_build(db_session, project):
    await bm25_state.mark_dirty(db_session, project.id)
    await db_session.commit()
    snapshot = (await _state(project.id)).last_change_at.replace(tzinfo=timezone.utc)

    # A paper lands while the build runs.
    await bm25_state.mark_dirty(db_session, project.id)
    await db_session.commit()

    cleared = await bm25_state.swap_version(
        db_session,
        project.id,
        new_version=1,
        snapshot_change_at=snapshot,
        chunk_count=5,
        tokenizer_ver=1,
    )
    await db_session.commit()

    assert cleared is False
    after = await _state(project.id)
    assert after.dirty_since is not None
    assert after.current_version == 1  # the built version is still swapped in


async def test_swap_without_a_state_row_is_false(db_session, project):
    assert not await bm25_state.swap_version(
        db_session,
        project.id,
        new_version=1,
        snapshot_change_at=datetime.now(timezone.utc),
        chunk_count=0,
        tokenizer_ver=1,
    )


async def test_index_chunks_marks_project_dirty_in_same_commit(db_session, project):
    paper = Paper(project_id=project.id, title="T", source="manual")
    db_session.add(paper)
    await db_session.commit()

    with patch.object(svc._embedding_svc, "embed_batch", _embed_ok()):
        await svc.index_chunks(
            db_session, paper.id, [ChunkRecord(text="hello", section=(), page=None)], title="T"
        )

    s = await _state(project.id)
    assert s is not None and s.dirty_since is not None


async def test_index_chunks_with_no_chunks_marks_project_dirty(db_session, project):
    paper = Paper(project_id=project.id, title="T", source="manual")
    db_session.add(paper)
    await db_session.commit()

    await svc.index_chunks(db_session, paper.id, [], title="T")

    assert (await _state(project.id)) is not None


async def test_reupload_claim_marks_project_dirty(db_session, project):
    paper = Paper(project_id=project.id, title="T", source="upload")
    db_session.add(paper)
    await db_session.commit()

    await svc._claim_hash(db_session, paper, "a" * 64)

    s = await _state(project.id)
    assert s is not None and s.dirty_since is not None


async def test_delete_paper_marks_project_dirty(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project
):
    paper = Paper(project_id=project.id, title="T", source="manual")
    db_session.add(paper)
    await db_session.commit()
    assert await _state(project.id) is None

    resp = await client.delete(
        f"/v1/projects/{project.id}/papers/{paper.id}", headers={"X-Dev-User-Id": you.id}
    )

    assert resp.status_code == 204
    assert (await _state(project.id)) is not None
