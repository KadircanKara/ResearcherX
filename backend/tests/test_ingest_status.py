"""Idempotent, status-tracked ingest.

A paper is `pending` from the moment its bytes are accepted, `ready` in the
same commit as its chunks, and `failed` (with the reason kept server-side)
when the work raises. The SHA-256 of the bytes makes a retry of the same file
free, and refuses the same file as a second paper of one project.
"""

import hashlib
import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    ChatConversation,
    ChatMessage,
    Paper,
    PaperChunkEmbedding,
    PaperFile,
    PaperStatus,
    Project,
    ProjectMember,
    User,
)
from app.db.seed import seed_users
from app.db.session import SessionLocal
from app.services import paper_ingest_service as svc
from app.services.structured_chunker import ChunkRecord
from app.services.text_extraction import TEXT

DOC = b"# Intro\n\nSwarm search with many drones.\n\n# Method\n\nA grid sweep."


def _embed_ok():
    async def embed(texts, task_type=None):
        return [[0.0] * 768 for _ in texts]

    return AsyncMock(side_effect=embed)


@pytest.fixture(autouse=True)
def no_metadata():
    with patch(
        "app.services.paper_ingest_service.apply_metadata", new=AsyncMock(return_value="none")
    ):
        yield


@pytest_asyncio.fixture
async def you(db_session: AsyncSession) -> User:
    await seed_users(db_session)
    await db_session.commit()
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


async def _project(db: AsyncSession, owner: User, title: str) -> Project:
    p = Project(owner_id=owner.id, title=title, topic_keywords=[])
    db.add(p)
    await db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=owner.id, role="owner"))
    await db.commit()
    await db.refresh(p)
    return p


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    return await _project(db_session, you, "Status")


async def _paper(db: AsyncSession, project: Project, title: str, **kw) -> Paper:
    p = Paper(project_id=project.id, title=title, source=kw.pop("source", "upload"), **kw)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _fresh(paper_id: str) -> Paper:
    """Read the row through a NEW session, so what is asserted is what was
    committed, not what an identity map remembers."""
    async with SessionLocal() as db:
        return (await db.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()


async def _ingest(db: AsyncSession, paper_id: str, data: bytes = DOC, embed=None) -> int:
    with patch.object(svc._embedding_svc, "embed_batch", embed or _embed_ok()):
        return await svc.ingest(db, paper_id, data, file_format=TEXT)


def test_source_hash_is_sha256_hex():
    assert svc.source_hash(DOC) == hashlib.sha256(DOC).hexdigest()
    assert len(svc.source_hash(b"")) == 64


async def test_ingest_marks_ready_in_the_chunk_commit(db_session, project):
    paper = await _paper(db_session, project, "A")
    n = await _ingest(db_session, paper.id)
    assert n > 0
    row = await _fresh(paper.id)
    assert row.status == PaperStatus.READY
    assert row.source_hash == svc.source_hash(DOC)
    assert row.ingest_error is None


async def test_index_chunks_commits_ready_with_its_chunks(db_session, project):
    """The ready flag and the chunks land in ONE commit: no reader can see a
    ready paper without chunks, or chunks under a pending paper."""
    paper = await _paper(db_session, project, "A")
    with patch.object(svc._embedding_svc, "embed_batch", _embed_ok()):
        n = await svc.index_chunks(
            db_session, paper.id, [ChunkRecord(text="x", section=(), page=None)], title="A"
        )
    assert n == 1
    assert (await _fresh(paper.id)).status == PaperStatus.READY


async def test_pending_is_committed_before_extraction(db_session, project):
    """A crash mid-parse must leave a durable `pending` row with its hash,
    which is what keeps it out of retrieval and visible in the Papers tab."""
    paper = await _paper(db_session, project, "A", status=PaperStatus.READY)
    seen = {}
    real = svc.extract_markdown

    def spy(fmt, data):
        import asyncio

        async def read():
            row = await _fresh(paper.id)
            seen["status"], seen["hash"] = row.status, row.source_hash

        # Called in a worker thread: run the read on a private loop.
        asyncio.run(read())
        return real(fmt, data)

    with patch.object(svc, "extract_markdown", spy):
        await _ingest(db_session, paper.id)
    assert seen == {"status": PaperStatus.PENDING, "hash": svc.source_hash(DOC)}


async def test_reingest_same_bytes_same_paper_skips_work(db_session, project):
    paper = await _paper(db_session, project, "A")
    first = await _ingest(db_session, paper.id)
    embed = _embed_ok()
    second = await _ingest(db_session, paper.id, embed=embed)
    assert embed.await_count == 0
    assert second == first
    assert (await _fresh(paper.id)).status == PaperStatus.READY


async def test_reingest_different_bytes_same_paper_redoes_the_work(db_session, project):
    paper = await _paper(db_session, project, "A")
    await _ingest(db_session, paper.id)
    embed = _embed_ok()
    await _ingest(db_session, paper.id, b"Entirely new text.", embed=embed)
    assert embed.await_count == 1
    assert (await _fresh(paper.id)).source_hash == svc.source_hash(b"Entirely new text.")


async def test_same_bytes_other_ready_paper_raises_duplicate_source(db_session, project):
    a = await _paper(db_session, project, "First copy")
    b = await _paper(db_session, project, "Second copy")
    await _ingest(db_session, a.id)
    embed = _embed_ok()
    with pytest.raises(svc.DuplicateSource) as caught:
        await _ingest(db_session, b.id, embed=embed)
    assert caught.value.paper_id == a.id
    assert caught.value.title == "First copy"
    assert embed.await_count == 0
    row = await _fresh(b.id)
    assert row.source_hash is None
    assert row.status == PaperStatus.PENDING


async def test_a_race_past_the_check_still_raises_duplicate_source(db_session, project):
    """The existence check is the detection mechanism; the unique index is
    the backstop. Two requests that both pass the check must not 500."""
    a_id = (await _paper(db_session, project, "First copy")).id
    b_id = (await _paper(db_session, project, "Second copy")).id
    await _ingest(db_session, a_id)
    real = svc._find_holder
    calls = []

    async def blind_first(*args):
        calls.append(args)
        return None if len(calls) == 1 else await real(*args)

    with patch.object(svc, "_find_holder", blind_first):
        with pytest.raises(svc.DuplicateSource) as caught:
            await _ingest(db_session, b_id)
    assert caught.value.paper_id == a_id


async def test_same_bytes_other_project_is_allowed(db_session, project, you):
    other = await _project(db_session, you, "Elsewhere")
    a = await _paper(db_session, project, "A")
    b = await _paper(db_session, other, "B")
    await _ingest(db_session, a.id)
    assert await _ingest(db_session, b.id) > 0
    assert (await _fresh(b.id)).status == PaperStatus.READY


async def test_failed_ingest_marks_failed_and_stores_error(db_session, project):
    pid = (await _paper(db_session, project, "A")).id
    boom = AsyncMock(side_effect=RuntimeError("provider exploded"))
    with pytest.raises(RuntimeError):
        await _ingest(db_session, pid, embed=boom)
    row = await _fresh(pid)
    assert row.status == PaperStatus.FAILED
    assert row.ingest_error == "RuntimeError: provider exploded"
    # The hash stays: the bytes were accepted, only the work failed.
    assert row.source_hash == svc.source_hash(DOC)


async def test_ingest_error_is_capped(db_session, project):
    pid = (await _paper(db_session, project, "A")).id
    boom = AsyncMock(side_effect=RuntimeError("x" * 2000))
    with pytest.raises(RuntimeError):
        await _ingest(db_session, pid, embed=boom)
    assert (await _fresh(pid)).ingest_error == "RuntimeError: " + "x" * 500


async def test_failed_then_retry_is_not_short_circuited(db_session, project):
    pid = (await _paper(db_session, project, "A")).id
    with pytest.raises(RuntimeError):
        await _ingest(db_session, pid, embed=AsyncMock(side_effect=RuntimeError("x")))
    embed = _embed_ok()
    n = await _ingest(db_session, pid, embed=embed)
    assert embed.await_count == 1
    assert n > 0
    row = await _fresh(pid)
    assert row.status == PaperStatus.READY
    assert row.ingest_error is None


async def test_index_chunks_looks_the_paper_up_before_paying_for_embeddings(db_session):
    embed = _embed_ok()
    with patch.object(svc._embedding_svc, "embed_batch", embed):
        with pytest.raises(NoResultFound):
            await svc.index_chunks(
                db_session,
                "no-such-paper",
                [ChunkRecord(text="x", section=(), page=None)],
                title="",
            )
    assert embed.await_count == 0


async def test_index_manual_marks_ready_and_hashes_the_text(db_session, project):
    paper = await _paper(db_session, project, "M", source="manual")
    with patch.object(svc._embedding_svc, "embed_batch", _embed_ok()):
        await svc.index_manual(db_session, paper.id, "An abstract.", "A body.")
    row = await _fresh(paper.id)
    assert row.status == PaperStatus.READY
    assert row.source_hash == svc.source_hash(b"An abstract.\nA body.")


async def test_index_manual_with_no_text_stores_no_hash(db_session, project):
    """Every textless manual paper would share the hash of "\\n" and collide."""
    a = await _paper(db_session, project, "M1", source="manual")
    b = await _paper(db_session, project, "M2", source="manual")
    with patch.object(svc._embedding_svc, "embed_batch", _embed_ok()):
        await svc.index_manual(db_session, a.id, None, None)
        await svc.index_manual(db_session, b.id, "", "")
    assert (await _fresh(a.id)).source_hash is None
    assert (await _fresh(b.id)).status == PaperStatus.READY


async def test_index_manual_refuses_a_duplicate_manual_paper(db_session, project):
    a = await _paper(db_session, project, "M1", source="manual")
    b = await _paper(db_session, project, "M2", source="manual")
    embed = _embed_ok()
    with patch.object(svc._embedding_svc, "embed_batch", embed):
        await svc.index_manual(db_session, a.id, "Same.", "Text.")
        with pytest.raises(svc.DuplicateSource):
            await svc.index_manual(db_session, b.id, "Same.", "Text.")
    assert embed.await_count == 1


async def test_index_manual_leaves_an_uploads_hash_alone(db_session, project):
    """`reindex_papers` falls back to `index_manual` for non-manual papers
    with hand-entered text; that must not overwrite the upload's file hash."""
    paper = await _paper(db_session, project, "U", source="upload", source_hash="f" * 64)
    with patch.object(svc._embedding_svc, "embed_batch", _embed_ok()):
        await svc.index_manual(db_session, paper.id, "An abstract.", None)
    row = await _fresh(paper.id)
    assert row.source_hash == "f" * 64
    assert row.status == PaperStatus.READY


# ── routes ───────────────────────────────────────────────────────────────────


async def _create(client: AsyncClient, you: User, project: Project, title: str) -> str:
    r = await client.post(
        f"/v1/projects/{project.id}/papers",
        json={"title": title, "source": "upload"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def _upload(client, you, project, paper_id, data: bytes = DOC):
    return await client.post(
        f"/v1/projects/{project.id}/papers/{paper_id}/ingest",
        params={"ext": "txt"},
        content=data,
        headers={"X-Dev-User-Id": you.id, "Content-Type": "application/octet-stream"},
    )


async def test_route_maps_duplicate_source_to_409(client, db_session, you, project):
    a = await _create(client, you, project, "Original")
    b = await _create(client, you, project, "Copy")
    with patch.object(svc._embedding_svc, "embed_batch", _embed_ok()):
        assert (await _upload(client, you, project, a)).status_code == 200
        r = await _upload(client, you, project, b)
    assert r.status_code == 409
    assert r.json()["detail"] == {"error": "duplicate_source", "paper_id": a, "title": "Original"}
    # Refused before the file is stored: it is not this paper's file.
    async with SessionLocal() as db:
        assert await db.get(PaperFile, b) is None


async def test_route_retry_of_the_same_file_returns_the_stored_count(
    client, db_session, you, project
):
    a = await _create(client, you, project, "Original")
    with patch.object(svc._embedding_svc, "embed_batch", _embed_ok()):
        first = await _upload(client, you, project, a)
    embed = _embed_ok()
    with patch.object(svc._embedding_svc, "embed_batch", embed):
        second = await _upload(client, you, project, a)
    assert second.status_code == 200
    assert second.json() == first.json()
    assert embed.await_count == 0


async def test_route_failure_stays_generic_and_marks_failed(client, db_session, you, project):
    a = await _create(client, you, project, "Original")
    boom = AsyncMock(side_effect=RuntimeError("secret provider detail"))
    with patch.object(svc._embedding_svc, "embed_batch", boom):
        r = await _upload(client, you, project, a)
    assert r.status_code == 500
    assert "secret provider detail" not in r.text
    assert (await _fresh(a)).status == PaperStatus.FAILED


async def test_url_route_maps_duplicate_source_to_409(client, db_session, you, project):
    a = await _create(client, you, project, "Original")
    dup = svc.DuplicateSource(paper_id="p-1", title="Held")
    with (
        patch(
            "app.services.paper_fetch_service.fetch_pdf", new=AsyncMock(return_value=(b"%PDF", "u"))
        ),
        patch("app.services.paper_ingest_service.ingest", new=AsyncMock(side_effect=dup)),
        patch.object(settings, "feature_paper_url", True),
    ):
        r = await client.post(
            f"/v1/projects/{project.id}/papers/{a}/ingest-from-url",
            json={"url": "https://example.org/p.pdf"},
            headers={"X-Dev-User-Id": you.id},
        )
    assert r.status_code == 409
    assert r.json()["detail"] == {"error": "duplicate_source", "paper_id": "p-1", "title": "Held"}


async def test_paper_list_reports_every_paper_with_its_status(client, db_session, you, project):
    await _paper(db_session, project, "Ready", status=PaperStatus.READY)
    await _paper(db_session, project, "Stuck", status=PaperStatus.PENDING)
    await _paper(db_session, project, "Broke", status=PaperStatus.FAILED, ingest_error="secret")
    r = await client.get(f"/v1/projects/{project.id}/papers", headers={"X-Dev-User-Id": you.id})
    assert r.status_code == 200
    assert {p["title"]: p["status"] for p in r.json()} == {
        "Ready": "ready",
        "Stuck": "pending",
        "Broke": "failed",
    }
    assert "secret" not in r.text


# ── chat scope ───────────────────────────────────────────────────────────────


async def _conversation(db: AsyncSession, project: Project, you: User) -> ChatConversation:
    conv = ChatConversation(project_id=project.id, title="c", created_by=you.id)
    db.add(conv)
    await db.flush()
    db.add(ChatMessage(conversation_id=conv.id, role="user", content="What is in it?"))
    await db.commit()
    await db.refresh(conv)
    return conv


async def _respond(conv_id: str, mentions=None):
    from app.services.chat_service import ChatService

    async def fake_stream(*args, **kwargs):
        yield "answer"

    chat = ChatService()
    retrieve = AsyncMock(return_value=[])
    stream = patch.object(chat._chat_agent, "stream", return_value=fake_stream())
    with (
        patch.object(chat._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(chat, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(chat, "_retrieve_paper_chunks", retrieve),
        stream as agent_stream,
        patch.object(chat._conv_svc, "save_message", AsyncMock()),
    ):
        events = [e async for e in chat.respond(conv_id, "What is in it?", mentions)]
    return events, retrieve, agent_stream


async def test_pending_papers_are_out_of_retrieval_scope(db_session, project, you):
    ready = await _paper(db_session, project, "Ready paper", status=PaperStatus.READY)
    await _paper(db_session, project, "Pending paper", status=PaperStatus.PENDING)
    await _paper(db_session, project, "Failed paper", status=PaperStatus.FAILED)
    conv = await _conversation(db_session, project, you)

    events, retrieve, agent_stream = await _respond(conv.id)

    assert [e["event"] for e in events][-1] == "done"
    scope = retrieve.await_args.args[1]
    assert [p.paper_id for p in scope] == [ready.id]
    # The PAPERS block lists the same set: a paper with no chunks must not
    # invite questions that have no evidence.
    agent_input = agent_stream.call_args.args[0]
    assert [p.title for p in agent_input.papers] == ["Ready paper"]


async def test_mentioning_a_pending_paper_widens_instead_of_crashing(db_session, project, you):
    await _paper(db_session, project, "Ready paper", status=PaperStatus.READY)
    pending = await _paper(db_session, project, "Pending paper", status=PaperStatus.PENDING)
    conv = await _conversation(db_session, project, you)

    events, _retrieve, _ = await _respond(conv.id, [pending.id])

    kinds = [e["event"] for e in events]
    assert "error" not in kinds
    assert kinds[-1] == "done"
    retrieving = json.loads(next(e["data"] for e in events if e["event"] == "retrieving"))
    assert retrieving["widened"] is True


async def test_ingested_chunks_carry_the_papers_project(db_session, project):
    """`index_chunks` reads project_id from the paper (now before embedding)."""
    paper = await _paper(db_session, project, "A")
    await _ingest(db_session, paper.id)
    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == paper.id)
                )
            )
            .scalars()
            .all()
        )
    assert rows and all(r.project_id == project.id for r in rows)


# ── fix round 1 ──────────────────────────────────────────────────────────────


async def test_same_bytes_other_failed_paper_raises_duplicate_source(db_session, project):
    """Deliberate: the unique index allows one row per hash whatever its
    status, so a FAILED holder blocks the bytes too. The fix is to retry it."""
    a_id = (await _paper(db_session, project, "Broken copy")).id
    b_id = (await _paper(db_session, project, "New copy")).id
    with pytest.raises(RuntimeError):
        await _ingest(db_session, a_id, embed=AsyncMock(side_effect=RuntimeError("x")))
    assert (await _fresh(a_id)).status == PaperStatus.FAILED
    with pytest.raises(svc.DuplicateSource) as caught:
        await _ingest(db_session, b_id)
    assert (caught.value.paper_id, caught.value.title) == (a_id, "Broken copy")


async def test_reupload_after_an_embedding_model_change_reindexes(db_session, project):
    """Ready under the same hash but nothing searchable at the configured
    model: re-index, never report a silent 0."""
    pid = (await _paper(db_session, project, "A")).id
    await _ingest(db_session, pid)
    embed = _embed_ok()
    with patch.object(settings, "embedding_model", "some-new-model"):
        n = await _ingest(db_session, pid, embed=embed)
        assert await svc.stored_chunk_count(db_session, pid) == n
    assert embed.await_count == 1
    assert n > 0


async def test_a_noop_retry_and_a_duplicate_do_not_charge_the_meter(
    client, db_session, you, project, monkeypatch
):
    from app.db.models import UsageEvent

    monkeypatch.setattr(settings, "user_ingests_per_day", 5)
    a = await _create(client, you, project, "Original")
    b = await _create(client, you, project, "Copy")
    with patch.object(svc._embedding_svc, "embed_batch", _embed_ok()):
        assert (await _upload(client, you, project, a)).status_code == 200
        assert (await _upload(client, you, project, a)).status_code == 200
        assert (await _upload(client, you, project, b)).status_code == 409
    events = (await db_session.execute(select(UsageEvent))).scalars().all()
    assert len(events) == 1


async def test_url_route_noop_retry_commits_the_resolved_url(client, db_session, you, project):
    data = b"%PDF-ish bytes served by a mirror"
    pid = await _create(client, you, project, "Original")
    await _ingest(db_session, pid, data)
    embed = _embed_ok()
    with (
        patch.object(svc._embedding_svc, "embed_batch", embed),
        patch(
            "app.services.paper_fetch_service.fetch_pdf",
            new=AsyncMock(return_value=(data, "https://mirror.example.org/p.pdf")),
        ),
        patch.object(settings, "feature_paper_url", True),
    ):
        r = await client.post(
            f"/v1/projects/{project.id}/papers/{pid}/ingest-from-url",
            json={"url": "https://example.org/p.pdf"},
            headers={"X-Dev-User-Id": you.id},
        )
    assert r.status_code == 200
    assert r.json()["chunks_stored"] > 0
    assert embed.await_count == 0
    assert (await _fresh(pid)).resolved_pdf_url == "https://mirror.example.org/p.pdf"


async def test_a_manual_create_race_is_a_409_not_a_500(client, db_session, you, project):
    """Two identical manual entries that both pass the existence check: the
    unique index catches the second, which is mapped to the same 409."""
    body = {"title": "M", "source": "manual", "abstract": "Same.", "body": "Text."}
    headers = {"X-Dev-User-Id": you.id}
    real = svc._find_holder
    calls = []

    async def blind_first(*args):
        calls.append(args)
        return None if len(calls) == 1 else await real(*args)

    with (
        patch.object(settings, "feature_manual_papers", True),
        patch.object(svc._embedding_svc, "embed_batch", _embed_ok()),
    ):
        first = await client.post(f"/v1/projects/{project.id}/papers", json=body, headers=headers)
        assert first.status_code == 201
        with patch.object(svc, "_find_holder", blind_first):
            r = await client.post(
                f"/v1/projects/{project.id}/papers", json={**body, "title": "M2"}, headers=headers
            )
    assert r.status_code == 409
    assert r.json()["detail"]["paper_id"] == first.json()["id"]
    async with SessionLocal() as db:
        titles = (await db.execute(select(Paper.title))).scalars().all()
    assert titles == ["M"]


# ── a re-upload drops the old chunks with the claim ──────────────────────────


async def _chunks(paper_id: str) -> int:
    async with SessionLocal() as db:
        return len(
            (
                await db.execute(
                    select(PaperChunkEmbedding.id).where(PaperChunkEmbedding.paper_id == paper_id)
                )
            ).all()
        )


async def test_a_claim_drops_the_old_chunks_before_parsing(db_session, project):
    """A re-upload commits `pending` and the old chunks' removal together: a
    stale chunk left in the HNSW index is dropped by the ready filter and
    forces the whole-project exact fallback on every turn that meets it."""
    pid = (await _paper(db_session, project, "A")).id
    assert await _ingest(db_session, pid) > 0
    seen = {}
    real = svc.extract_markdown

    def spy(fmt, data):
        import asyncio

        async def read():
            seen["status"] = (await _fresh(pid)).status
            seen["chunks"] = await _chunks(pid)

        asyncio.run(read())
        return real(fmt, data)

    with patch.object(svc, "extract_markdown", spy):
        await _ingest(db_session, pid, b"Entirely new text.")
    assert seen == {"status": PaperStatus.PENDING, "chunks": 0}


async def test_a_failed_reupload_leaves_no_chunks(db_session, project):
    pid = (await _paper(db_session, project, "A")).id
    assert await _ingest(db_session, pid) > 0
    with pytest.raises(RuntimeError):
        await _ingest(
            db_session, pid, b"Entirely new text.", embed=AsyncMock(side_effect=RuntimeError("x"))
        )
    assert (await _fresh(pid)).status == PaperStatus.FAILED
    assert await _chunks(pid) == 0


async def test_a_duplicate_retry_leaves_a_ready_papers_chunks_alone(db_session, project):
    pid = (await _paper(db_session, project, "A")).id
    n = await _ingest(db_session, pid)
    assert n > 0
    assert await _ingest(db_session, pid) == n
    assert await _chunks(pid) == n
    assert (await _fresh(pid)).status == PaperStatus.READY
