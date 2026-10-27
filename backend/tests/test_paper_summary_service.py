"""`apply_summary`, the shared `write_record`, and the ingest hook.

A summary is written AFTER a paper is ready, in its own sessions, and never
raises: indexing is load-bearing, a summary is not. The summary commits
before the doc embedding is computed, so an embedding failure can never lose
the summary. `conftest.py` stubs `schedule_summary` for every test, so ingest
tests spawn no background task; this module keeps the real one.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Paper, PaperStatus, PaperSummary, Project, ProjectMember, User
from app.db.seed import seed_users
from app.db.session import SessionLocal
from app.services import paper_ingest_service as ingest_svc
from app.services import paper_summary_service as svc
from app.services.paper_summary_client import GeneratedSummary
from app.services.paper_summary_prompt import SUMMARY_VER, SummaryRecord, embed_text
from app.services.task_registry import TaskRegistry, registry
from app.services.text_extraction import TEXT

# Captured at import, before conftest's autouse stub replaces the attribute.
REAL_SCHEDULE = svc.schedule_summary

DOC = b"# Intro\n\nSwarm search with many drones.\n\n# Method\n\nA grid sweep."
VEC = [0.25] * 768

GENERATED = GeneratedSummary(
    record=SummaryRecord(summary="A swarm searches a grid.", keywords=("UAV", "swarm")),
    model="deepseek/deepseek-v4.1-flash",
    input_tokens=1200,
    output_tokens=80,
    cost_usd=0.0004,
)


@pytest.fixture(autouse=True)
def no_metadata():
    with patch(
        "app.services.paper_ingest_service.apply_metadata", new=AsyncMock(return_value="none")
    ):
        yield


@pytest_asyncio.fixture
async def project(db_session: AsyncSession) -> Project:
    await seed_users(db_session)
    await db_session.commit()
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    p = Project(owner_id=you.id, title="Summaries", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    return p


async def _paper(db: AsyncSession, project: Project, **kw) -> Paper:
    kw.setdefault("title", "Swarm Search")
    kw.setdefault("status", PaperStatus.READY)
    p = Paper(project_id=project.id, source=kw.pop("source", "upload"), **kw)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _row(paper_id: str) -> PaperSummary | None:
    async with SessionLocal() as db:
        return await db.get(PaperSummary, paper_id)


def _generate(result):
    return patch.object(svc, "generate_summary", new=AsyncMock(return_value=result))


def _embed(vec=None, exc: Exception | None = None):
    mock = AsyncMock(side_effect=exc) if exc else AsyncMock(return_value=vec or VEC)
    return patch.object(svc._embedding_svc, "embed", new=mock)


async def test_apply_summary_writes_llm_record_and_embedding(db_session, project):
    paper = await _paper(
        db_session,
        project,
        abstract="We search with drones.",
        extracted_pages=[{"page": 2, "text": "second"}, {"page": 1, "text": "first"}],
    )
    with _generate(GENERATED) as gen, _embed() as emb:
        status = await svc.apply_summary(paper.id)

    assert status == "ready"
    title, abstract, text = gen.await_args.args
    assert (title, abstract, text) == ("Swarm Search", "We search with drones.", "first\n\nsecond")
    emb.assert_awaited_once()
    assert emb.await_args.args[0] == embed_text(
        "Swarm Search", "We search with drones.", "A swarm searches a grid."
    )
    row = await _row(paper.id)
    assert row.status == "ready"
    assert row.source == "llm"
    assert row.summary == "A swarm searches a grid."
    assert row.keywords == ["UAV", "swarm"]
    assert row.summary_model == "deepseek/deepseek-v4.1-flash"
    assert row.summary_ver == SUMMARY_VER
    assert row.batch_id is None
    assert row.error is None
    assert row.model == settings.embedding_model
    assert json.loads(row.doc_embedding) == VEC


async def test_apply_summary_caps_the_input_text(db_session, project, monkeypatch):
    monkeypatch.setattr(settings, "paper_summary_input_chars", 5)
    paper = await _paper(db_session, project, body="abcdefghij")
    with _generate(None) as gen, _embed():
        await svc.apply_summary(paper.id)
    assert gen.await_args.args[2] == "abcde"


async def test_apply_summary_falls_back_to_abstract(db_session, project):
    paper = await _paper(db_session, project, abstract="  We search with drones.  ")
    with _generate(None), _embed():
        status = await svc.apply_summary(paper.id)
    assert status == "ready"
    row = await _row(paper.id)
    assert row.source == "abstract"
    assert row.summary == "We search with drones."
    assert row.summary_model is None
    assert row.keywords == []
    assert row.doc_embedding is not None


async def test_apply_summary_without_abstract_marks_failed(db_session, project):
    paper = await _paper(db_session, project, abstract="   ")
    with _generate(None), _embed() as emb:
        status = await svc.apply_summary(paper.id)
    assert status == "failed"
    emb.assert_not_awaited()
    row = await _row(paper.id)
    assert row.status == "failed"
    assert row.summary is None
    assert row.error
    assert row.doc_embedding is None


async def test_apply_summary_never_raises_on_db_error(db_session, project):
    paper = await _paper(db_session, project, abstract="An abstract.")
    with (
        _generate(GENERATED),
        _embed(),
        patch.object(svc, "write_record", new=AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        status = await svc.apply_summary(paper.id)
    assert status == "error"
    assert await _row(paper.id) is None


async def test_apply_summary_lets_cancellation_through(db_session, project):
    paper = await _paper(db_session, project, abstract="An abstract.")
    with (
        patch.object(svc, "generate_summary", new=AsyncMock(side_effect=asyncio.CancelledError)),
        pytest.raises(asyncio.CancelledError),
    ):
        await svc.apply_summary(paper.id)


async def test_embedding_failure_keeps_summary(db_session, project):
    paper = await _paper(db_session, project, abstract="An abstract.")
    with _generate(GENERATED), _embed(exc=RuntimeError("embedder down")):
        status = await svc.apply_summary(paper.id)
    assert status == "ready"
    row = await _row(paper.id)
    assert row.status == "ready"
    assert row.summary == "A swarm searches a grid."
    assert row.doc_embedding is None


async def test_write_record_updates_an_existing_row(db_session, project):
    """The ONE writer upserts: a re-run replaces the row, clears a batch id
    and a stale error, and re-embeds."""
    paper = await _paper(db_session, project, abstract="An abstract.")
    db_session.add(
        PaperSummary(
            paper_id=paper.id,
            summary="old",
            status="failed",
            summary_ver=0,
            batch_id="batch-1",
            error="old error",
            doc_embedding=json.dumps([0.0] * 768),
            model="old-model",
        )
    )
    await db_session.commit()
    with _embed():
        status = await svc.write_record(db_session, paper, GENERATED)
    assert status == "ready"
    row = await _row(paper.id)
    assert (row.summary, row.status, row.summary_ver) == (
        "A swarm searches a grid.",
        "ready",
        SUMMARY_VER,
    )
    assert row.batch_id is None and row.error is None
    assert json.loads(row.doc_embedding) == VEC
    assert row.model == settings.embedding_model


async def test_paper_deleted_before_task_runs_is_a_noop(db_session, project):
    paper = await _paper(db_session, project, abstract="An abstract.")
    paper_id = paper.id
    await db_session.delete(paper)
    await db_session.commit()
    with _generate(GENERATED) as gen, _embed():
        status = await svc.apply_summary(paper_id)
    assert status == "skipped"
    gen.assert_not_awaited()
    assert await _row(paper_id) is None


async def test_a_paper_that_is_not_ready_is_skipped(db_session, project):
    paper = await _paper(db_session, project, abstract="x", status=PaperStatus.PENDING)
    with _generate(GENERATED) as gen:
        assert await svc.apply_summary(paper.id) == "skipped"
    gen.assert_not_awaited()


async def test_paper_deleted_during_generation_writes_nothing(db_session, project):
    paper = await _paper(db_session, project, abstract="An abstract.")
    paper_id = paper.id

    async def delete_then_answer(*_args):
        async with SessionLocal() as db:
            await db.delete(await db.get(Paper, paper_id))
            await db.commit()
        return GENERATED

    with patch.object(svc, "generate_summary", new=delete_then_answer), _embed() as emb:
        assert await svc.apply_summary(paper_id) == "skipped"
    emb.assert_not_awaited()
    assert await _row(paper_id) is None


# ---- the ingest hook ------------------------------------------------------


async def _ingest(db: AsyncSession, paper_id: str, data: bytes = DOC) -> int:
    async def embed(texts, task_type=None):
        return [[0.0] * 768 for _ in texts]

    with patch.object(ingest_svc._embedding_svc, "embed_batch", AsyncMock(side_effect=embed)):
        return await ingest_svc.ingest(db, paper_id, data, file_format=TEXT)


async def test_ingest_schedules_summary_after_metadata(db_session, project):
    paper = await _paper(db_session, project, status=PaperStatus.PENDING)
    order: list[str] = []

    async def metadata(*_a, **_k):
        order.append("metadata")
        return "none"

    with (
        patch("app.services.paper_ingest_service.apply_metadata", new=metadata),
        patch.object(svc, "schedule_summary", side_effect=lambda pid: order.append(pid)),
    ):
        await _ingest(db_session, paper.id)
    assert order == ["metadata", paper.id]


async def test_failed_ingest_schedules_nothing(db_session, project):
    paper = await _paper(db_session, project, status=PaperStatus.PENDING)
    with (
        patch.object(ingest_svc, "extract_markdown", side_effect=RuntimeError("bad file")),
        patch.object(svc, "schedule_summary") as schedule,
        pytest.raises(RuntimeError),
    ):
        await _ingest(db_session, paper.id)
    schedule.assert_not_called()


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, 1),
        ({"status": "ready", "summary_ver": SUMMARY_VER}, 0),
        ({"status": "failed", "summary_ver": SUMMARY_VER}, 1),
        ({"status": "pending", "summary_ver": SUMMARY_VER}, 1),
        ({"status": "ready", "summary_ver": SUMMARY_VER - 1}, 1),
    ],
)
async def test_unchanged_reingest_schedules_only_a_missing_or_stale_summary(
    db_session, project, existing, expected
):
    paper = await _paper(db_session, project, status=PaperStatus.PENDING)
    await _ingest(db_session, paper.id)
    if existing is not None:
        db_session.add(PaperSummary(paper_id=paper.id, summary="s", **existing))
        await db_session.commit()
    with patch.object(svc, "schedule_summary") as schedule:
        await _ingest(db_session, paper.id)  # same bytes: the short-circuit
    assert schedule.call_count == expected


async def test_manual_paper_create_and_edit_schedule_a_summary(
    client: AsyncClient, db_session, project
):
    with (
        patch(
            "app.services.paper_ingest_service.EmbeddingService.embed_batch",
            new=AsyncMock(side_effect=lambda texts, task_type=None: [[0.0] * 768 for _ in texts]),
        ),
        patch.object(svc, "schedule_summary") as schedule,
    ):
        created = await client.post(
            f"/v1/projects/{project.id}/papers",
            json={"title": "Manual", "abstract": "Only an abstract.", "source": "manual"},
        )
        assert created.status_code == 201, created.text
        paper_id = created.json()["id"]
        schedule.assert_called_once_with(paper_id)

        edited = await client.patch(
            f"/v1/projects/{project.id}/papers/{paper_id}",
            json={"body": "A new body for the manual paper."},
        )
        assert edited.status_code == 200, edited.text
        assert schedule.call_count == 2

        renamed = await client.patch(
            f"/v1/projects/{project.id}/papers/{paper_id}", json={"title": "Renamed"}
        )
        assert renamed.status_code == 200
        assert schedule.call_count == 2  # no text change, no re-index, no summary


# ---- scheduling -----------------------------------------------------------


async def test_schedule_is_idempotent_per_paper():
    release = asyncio.Event()
    calls: list[str] = []

    async def slow(paper_id):
        calls.append(paper_id)
        await release.wait()
        return "ready"

    with patch.object(svc, "apply_summary", new=slow):
        REAL_SCHEDULE("p1")
        REAL_SCHEDULE("p1")
        await asyncio.sleep(0)
        assert calls == ["p1"]
        assert registry.is_running("summary:p1")
        release.set()
        await registry.get("summary:p1")
        await asyncio.sleep(0)
        assert not registry.is_running("summary:p1")
        REAL_SCHEDULE("p1")  # a finished task does not block the next one
        await registry.get("summary:p1")
    assert calls == ["p1", "p1"]


async def test_registry_is_running_reports_only_live_tasks():
    reg = TaskRegistry()
    assert not reg.is_running("k")
    done = asyncio.get_running_loop().create_future()
    done.set_result(None)
    reg._tasks["k"] = done  # a finished task not yet discarded
    assert not reg.is_running("k")
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    reg.register("k", task)
    assert reg.is_running("k")
    gate.set()
    await task
    await asyncio.sleep(0)
    assert not reg.is_running("k")


# ---- fix round 1 ----------------------------------------------------------


async def test_write_record_upserts_atomically_over_a_row_it_did_not_see(db_session, project):
    """The upsert is ONE ON CONFLICT statement: a row inserted between any
    check and the write (the backfill racing a live task) is updated, not
    a primary-key error."""
    paper = await _paper(db_session, project, abstract="An abstract.")
    async with SessionLocal() as other:
        other.add(PaperSummary(paper_id=paper.id, summary="racer", status="pending"))
        await other.commit()
    with _embed():
        assert await svc.write_record(db_session, paper, GENERATED) == "ready"
    row = await _row(paper.id)
    assert row.summary == "A swarm searches a grid."
    assert row.keywords == ["UAV", "swarm"]
    assert json.loads(row.doc_embedding) == VEC
    assert row.updated_at >= row.created_at


async def test_a_finished_task_does_not_untrack_its_replacement():
    reg = TaskRegistry()
    gate_a, gate_b = asyncio.Event(), asyncio.Event()
    a = asyncio.create_task(gate_a.wait())
    reg.register("k", a)
    b = asyncio.create_task(gate_b.wait())
    reg.register("k", b)  # replaces a while a is still running
    gate_a.set()
    await a  # a's done callbacks have run by the time this await resumes
    assert reg.get("k") is b
    assert reg.is_running("k")
    gate_b.set()
    await b
    assert reg.get("k") is None


async def test_reingest_during_generation_writes_nothing_and_reschedules(db_session, project):
    paper = await _paper(db_session, project, abstract="An abstract.", source_hash="old")
    paper_id = paper.id

    async def reingest_then_answer(*_args):
        async with SessionLocal() as db:
            (await db.get(Paper, paper_id)).source_hash = "new"
            await db.commit()
        return GENERATED

    with patch.object(svc, "generate_summary", new=reingest_then_answer), _embed() as emb:
        assert await svc.apply_summary(paper_id) == svc.STALE
    emb.assert_not_awaited()
    assert await _row(paper_id) is None

    outcomes = iter([svc.STALE, "ready"])
    calls: list[str] = []

    async def fake_apply(pid):
        calls.append(pid)
        return next(outcomes)

    # The reschedule looks `schedule_summary` up in the module, where
    # conftest has stubbed it: put the real one back for this block.
    with (
        patch.object(svc, "apply_summary", new=fake_apply),
        patch.object(svc, "schedule_summary", new=REAL_SCHEDULE),
    ):
        REAL_SCHEDULE(paper_id)
        first = registry.get(f"summary:{paper_id}")
        await first
        second = registry.get(f"summary:{paper_id}")
        assert second is not None and second is not first
        await second
    assert calls == [paper_id, paper_id]


async def test_a_failing_summary_check_never_fails_the_ingest(db_session, project):
    paper = await _paper(db_session, project, status=PaperStatus.PENDING)
    first = await _ingest(db_session, paper.id)
    with (
        patch.object(svc, "needs_summary", new=AsyncMock(side_effect=RuntimeError("db hiccup"))),
        patch.object(svc, "schedule_summary") as schedule,
    ):
        assert await _ingest(db_session, paper.id) == first  # the short-circuit
    schedule.assert_not_called()


# ---- final review fixes ---------------------------------------------------


async def test_an_abstract_row_needs_a_summary_only_once_a_key_is_set(
    db_session, project, monkeypatch
):
    """A row written by the no-key fallback heals once a key arrives."""
    paper = await _paper(db_session, project, abstract="An abstract.")
    db_session.add(
        PaperSummary(
            paper_id=paper.id,
            summary="An abstract.",
            source="abstract",
            status="ready",
            summary_ver=SUMMARY_VER,
        )
    )
    await db_session.commit()
    monkeypatch.setattr(settings, "summary_api_key", "")
    assert not await svc.needs_summary(db_session, paper.id)
    monkeypatch.setattr(settings, "summary_api_key", "sk-test")
    assert await svc.needs_summary(db_session, paper.id)


async def test_an_llm_row_never_needs_a_summary_with_a_key(db_session, project, monkeypatch):
    paper = await _paper(db_session, project, abstract="An abstract.")
    db_session.add(
        PaperSummary(
            paper_id=paper.id, summary="s", source="llm", status="ready", summary_ver=SUMMARY_VER
        )
    )
    await db_session.commit()
    monkeypatch.setattr(settings, "summary_api_key", "sk-test")
    assert not await svc.needs_summary(db_session, paper.id)


async def test_a_closing_registry_schedules_nothing():
    """`cancel_all` closes the registry: a STALE run finishing during the
    shutdown gather must not start an untracked replacement."""
    calls: list[str] = []

    async def fake(paper_id):
        calls.append(paper_id)
        return "ready"

    with patch.object(svc, "apply_summary", new=fake):
        await registry.cancel_all()
        assert registry.closing
        try:
            REAL_SCHEDULE("p1")
            await asyncio.sleep(0)
            assert calls == []
            assert registry.get("summary:p1") is None
        finally:
            registry.closing = False


async def test_cancel_all_sets_closing_on_a_fresh_registry():
    reg = TaskRegistry()
    assert not reg.closing
    await reg.cancel_all()
    assert reg.closing
