"""The batch backfill: `openrouter_batch` (HTTP, no DB) and the submit/collect script.

Every HTTP call goes through an `httpx.MockTransport`; nothing reaches
OpenRouter. Rows are only ever written through Core statements or
`write_record` (an ORM write binds `doc_embedding` as varchar on Postgres),
and read here with Core selects.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Paper, PaperStatus, PaperSummary, Project, ProjectMember, User
from app.db.seed import seed_users
from app.db.session import SessionLocal
from app.services import openrouter_batch as ob
from app.services import paper_summary_service as svc
from app.services.paper_summary_prompt import SUMMARY_VER, response_format
from scripts import backfill_paper_summaries as script

KEY = "sk-or-test-never-printed"
VEC = [0.5] * 768
GOOD = json.dumps({"summary": "A swarm searches a grid.", "keywords": ["UAV", "swarm", "UAV"]})


@pytest.fixture(autouse=True)
def summary_key(monkeypatch):
    monkeypatch.setattr(settings, "summary_api_key", KEY)


@pytest.fixture(autouse=True)
def embed():
    mock = AsyncMock(return_value=VEC)
    with patch.object(svc._embedding_svc, "embed", new=mock):
        yield mock


class Server:
    """A fake OpenRouter: records every request, answers from a queue or a map."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.submit_response: tuple[int, dict] = (202, {"id": "batch_1", "status": "validating"})
        self.batches: dict[str, tuple[int, dict]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            status, payload = self.submit_response
            return httpx.Response(status, json=payload)
        batch_id = request.url.path.rsplit("/", 1)[-1]
        status, payload = self.batches.get(batch_id, (404, {"error": {"message": "no batch"}}))
        return httpx.Response(status, json=payload)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def posted(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.method == "POST"]


@pytest.fixture
def server() -> Server:
    return Server()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession) -> Project:
    await seed_users(db_session)
    await db_session.commit()
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    p = Project(owner_id=you.id, title="Backfill", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    return p


async def _paper(db: AsyncSession, project: Project, **kw) -> Paper:
    kw.setdefault("title", "Swarm Search")
    kw.setdefault("abstract", "Drones search a grid.")
    kw.setdefault("status", PaperStatus.READY)
    p = Paper(project_id=project.id, source="upload", **kw)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _set_row(paper_id: str, **values) -> None:
    async with SessionLocal() as db:
        await db.execute(insert(PaperSummary).values(paper_id=paper_id, **values))
        await db.commit()


async def _row(paper_id: str):
    async with SessionLocal() as db:
        return (
            await db.execute(
                select(
                    PaperSummary.status,
                    PaperSummary.batch_id,
                    PaperSummary.source,
                    PaperSummary.summary,
                    PaperSummary.summary_model,
                    PaperSummary.keywords,
                    PaperSummary.error,
                    PaperSummary.doc_embedding,
                    PaperSummary.model,
                ).where(PaperSummary.paper_id == paper_id)
            )
        ).first()


def _ok(custom_id: str, content: str = GOOD, finish: str = "stop") -> dict:
    return {
        "id": f"req_{custom_id}",
        "custom_id": custom_id,
        "response": {
            "status_code": 200,
            "request_id": "r",
            "body": {
                "model": "deepseek/deepseek-v4.1-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": finish,
                    }
                ],
                "usage": {"prompt_tokens": 900, "completion_tokens": 120},
            },
        },
        "error": None,
    }


def _err(custom_id: str) -> dict:
    return {
        "custom_id": custom_id,
        "response": None,
        "error": {"type": "invalid_request_error", "message": "boom", "param": None},
    }


def _batch(status: str, results=None, error=None) -> dict:
    return {
        "id": "batch_1",
        "object": "batch",
        "status": status,
        "provider": "Sail Research",
        "request_counts": {"total": 2, "completed": 2, "failed": 0},
        "usage": {"cost": 0.0016} if status == "completed" else None,
        "results": results,
        "error": error,
    }


# --- openrouter_batch: body ------------------------------------------------


def test_batch_body_key_order_puts_requests_last():
    body = ob.batch_body(
        [("p1", "T", "A", "text")], model="deepseek/deepseek-v4.1-flash", provider="sail-research"
    )
    assert list(body) == ["endpoint", "model", "provider", "requests"]
    # The order survives serialisation, which is what OpenRouter reads.
    raw = json.dumps(body)
    assert raw.index('"endpoint"') < raw.index('"provider"') < raw.index('"requests"')
    assert body["endpoint"] == "/v1/chat/completions"


def test_batch_body_pins_sail_and_disables_reasoning():
    body = ob.batch_body(
        [("p1", "Title", None, "full text"), ("p2", "T2", "Abs", "t2")],
        model="deepseek/deepseek-v4.1-flash",
        provider="sail-research",
    )
    assert body["model"] == "deepseek/deepseek-v4.1-flash"
    assert body["provider"] == {"only": ["sail-research"], "allow_fallbacks": False}
    assert [r["custom_id"] for r in body["requests"]] == ["p1", "p2"]
    req = body["requests"][0]["body"]
    assert list(req) == ["max_tokens", "reasoning", "response_format", "messages"]
    assert req["max_tokens"] == settings.paper_summary_max_tokens
    assert req["reasoning"] == {"enabled": False}
    assert req["response_format"] == response_format()
    assert req["messages"][1]["content"].startswith("Title: Title\n\nAbstract: \n\nFull text:\n")


def test_chunks_split_by_count_and_by_bytes():
    papers = [(f"p{i}", "T", None, "x" * 1000) for i in range(10)]
    by_count = ob.chunk_papers(papers, max_count=4, max_bytes=10**9, model="m", provider="p")
    assert [len(c) for c in by_count] == [4, 4, 2]
    one = len(json.dumps(ob.batch_body(papers[:1], model="m", provider="p")).encode())
    by_bytes = ob.chunk_papers(papers, max_count=100, max_bytes=one * 3, model="m", provider="p")
    assert all(len(c) >= 1 for c in by_bytes) and sum(map(len, by_bytes)) == 10
    for chunk in by_bytes:
        assert len(json.dumps(ob.batch_body(chunk, model="m", provider="p")).encode()) <= one * 3
    with pytest.raises(ValueError):
        ob.chunk_papers(papers, max_count=100, max_bytes=one - 1, model="m", provider="p")


# --- openrouter_batch: HTTP ------------------------------------------------


async def test_submit_raises_on_402_with_message(server):
    server.submit_response = (402, {"error": {"code": 402, "message": "Insufficient credits"}})
    body = ob.batch_body([("p1", "T", None, "t")], model="m", provider="sail-research")
    async with server.client() as client:
        with pytest.raises(ob.BatchError) as info:
            await ob.submit(body, client=client)
    assert info.value.status == 402
    assert "Insufficient credits" in info.value.message
    assert KEY not in str(info.value)
    # The key travelled in the header, and only there.
    assert server.requests[0].headers["authorization"] == f"Bearer {KEY}"
    assert str(server.requests[0].url) == "https://openrouter.ai/api/v1/batches"


async def test_submit_refuses_without_a_key(monkeypatch, server):
    monkeypatch.setattr(settings, "summary_api_key", "")
    async with server.client() as client:
        with pytest.raises(ob.BatchError):
            await ob.submit({"requests": []}, client=client)
    assert server.requests == []


# --- submit ----------------------------------------------------------------


async def test_submit_records_pending_rows_with_batch_id(db_session, project, server):
    a = await _paper(db_session, project, title="A", body="alpha body")
    b = await _paper(db_session, project, title="B")
    not_ready = await _paper(db_session, project, title="C", status=PaperStatus.PENDING)
    old = await _paper(db_session, project, title="D")
    await _set_row(old.id, status="ready", summary_ver=SUMMARY_VER, summary="kept")

    async with server.client() as client:
        result = await script.run_submit(client=client)

    assert result["batch_ids"] == ["batch_1"]
    sent = server.posted()
    assert len(sent) == 1
    assert sorted(r["custom_id"] for r in sent[0]["requests"]) == sorted([a.id, b.id])
    for paper in (a, b):
        row = await _row(paper.id)
        assert (row.status, row.batch_id) == ("pending", "batch_1")
    assert await _row(not_ready.id) is None
    assert (await _row(old.id)).status == "ready"


async def test_submit_skips_rows_already_pending(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    b = await _paper(db_session, project, title="B")
    failed = await _paper(db_session, project, title="F")
    done = await _paper(db_session, project, title="Done")
    await _set_row(a.id, status="pending", batch_id="batch_old")
    await _set_row(failed.id, status="failed", error="x")
    await _set_row(done.id, status="ready", summary="s", summary_ver=SUMMARY_VER)

    async with server.client() as client:
        await script.run_submit(client=client)
        sent = {r["custom_id"] for r in server.posted()[0]["requests"]}
        assert sent == {b.id, failed.id}
        # --force re-selects every ready paper, but never one in flight: b
        # and failed are now pending in batch_1, a in batch_old.
        server.submit_response = (202, {"id": "batch_2"})
        await script.run_submit(force=True, client=client)
    forced = {r["custom_id"] for r in server.posted()[1]["requests"]}
    assert forced == {done.id}
    assert (await _row(a.id)).batch_id == "batch_old"


async def test_submit_filters_by_project_id_as_a_string(db_session, project, server):
    mine = await _paper(db_session, project, title="Mine")
    other = Project(owner_id=project.owner_id, title="Other", topic_keywords=[])
    db_session.add(other)
    await db_session.commit()
    await _paper(db_session, other, title="Theirs")

    async with server.client() as client:
        await script.run_submit(project_id=str(project.id), client=client)
    assert [r["custom_id"] for r in server.posted()[0]["requests"]] == [mine.id]


async def test_submit_chunks_into_several_batches(db_session, project, server):
    papers = [await _paper(db_session, project, title=f"P{i}") for i in range(5)]
    ids = iter(["batch_a", "batch_b", "batch_c"])

    def handler(request):
        server.requests.append(request)
        return httpx.Response(202, json={"id": next(ids)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await script.run_submit(chunk=2, client=client)
    assert result["batch_ids"] == ["batch_a", "batch_b", "batch_c"]
    batches = {(await _row(p.id)).batch_id for p in papers}
    assert batches == {"batch_a", "batch_b", "batch_c"}


async def test_submit_dry_run_makes_no_network_call(db_session, project, server):
    await _paper(db_session, project, title="A")
    await _paper(db_session, project, title="B")
    async with server.client() as client:
        result = await script.run_submit(dry_run=True, client=client)
    assert server.requests == []
    assert result["papers"] == 2
    assert result["estimate_usd"] == pytest.approx(2 * script.SPIKE_USD_PER_PAPER)
    assert result["batch_ids"] == []


async def test_submit_402_records_nothing(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    server.submit_response = (402, {"error": {"message": "Insufficient credits"}})
    async with server.client() as client:
        with pytest.raises(ob.BatchError):
            await script.run_submit(client=client)
    assert await _row(a.id) is None


# --- collect ---------------------------------------------------------------


async def test_collect_skips_in_progress_batches(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (200, _batch("in_progress"))
    async with server.client() as client:
        await script.run_collect(client=client)
    row = await _row(a.id)
    assert (row.status, row.batch_id) == ("pending", "batch_1")


async def test_collect_writes_records_and_clears_batch_id(db_session, project, server, embed):
    a = await _paper(db_session, project, title="A")
    b = await _paper(db_session, project, title="B", abstract="B's abstract.")
    c = await _paper(db_session, project, title="C")
    for p in (a, b, c):
        await _set_row(p.id, status="pending", batch_id="batch_1")
    results = [_ok(a.id), _err(b.id), _ok(c.id, content='{"summary": ""}')]
    server.batches["batch_1"] = (200, _batch("completed", results=results))

    async with server.client() as client:
        result = await script.run_collect(client=client)

    ra = await _row(a.id)
    assert (ra.status, ra.batch_id, ra.source) == ("ready", None, "llm")
    assert ra.summary == "A swarm searches a grid."
    assert ra.summary_model == "deepseek/deepseek-v4.1-flash"
    assert list(ra.keywords) == ["UAV", "swarm"]
    assert ra.doc_embedding is not None and ra.model == settings.embedding_model
    rb = await _row(b.id)
    assert (rb.status, rb.batch_id, rb.source, rb.summary) == (
        "ready",
        None,
        "abstract",
        "B's abstract.",
    )
    assert (await _row(c.id)).source == "abstract"
    assert result["cost_usd"] == pytest.approx(0.0016)
    assert result["providers"] == {"Sail Research": 1}
    assert result["llm"] == 1 and result["fallback"] == 2


async def test_collect_twice_is_a_noop(db_session, project, server, embed):
    a = await _paper(db_session, project, title="A")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (200, _batch("completed", results=[_ok(a.id)]))
    async with server.client() as client:
        await script.run_collect(client=client)
        first = await _row(a.id)
        fetches, embeds = len(server.requests), embed.await_count
        second = await script.run_collect(client=client)
    assert len(server.requests) == fetches  # nothing pending: no batch fetched
    assert embed.await_count == embeds  # and nothing re-embedded
    assert await _row(a.id) == first
    assert second["llm"] == 0 and second["fallback"] == 0


async def test_collect_skips_deleted_papers(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    gone = await _paper(db_session, project, title="Gone")
    for p in (a, gone):
        await _set_row(p.id, status="pending", batch_id="batch_1")
    async with SessionLocal() as db:
        await db.execute(delete(Paper).where(Paper.id == gone.id))
        await db.commit()
    server.batches["batch_1"] = (200, _batch("completed", results=[_ok(gone.id), _ok(a.id)]))
    async with server.client() as client:
        result = await script.run_collect(client=client)
    assert (await _row(a.id)).status == "ready"
    assert await _row(gone.id) is None
    assert result["skipped"] == 1


async def test_collect_skips_rows_rewritten_since_submit(db_session, project, server):
    """A live ingest rewrote the row after submit: the batch result is stale."""
    a = await _paper(db_session, project, title="A")
    b = await _paper(db_session, project, title="B")
    await _set_row(a.id, status="ready", summary="live summary", source="llm")
    await _set_row(b.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (200, _batch("completed", results=[_ok(a.id), _ok(b.id)]))
    async with server.client() as client:
        result = await script.run_collect(client=client)
    assert (await _row(a.id)).summary == "live summary"
    assert (await _row(b.id)).status == "ready"
    assert result["skipped"] == 1


async def test_collect_marks_rows_failed_for_failed_batch(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    other = await _paper(db_session, project, title="Other batch")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    await _set_row(other.id, status="pending", batch_id="batch_2")
    server.batches["batch_1"] = (
        200,
        _batch("failed", error={"message": "request p1: max_tokens too large"}),
    )
    server.batches["batch_2"] = (200, _batch("validating"))
    async with server.client() as client:
        await script.run_collect(client=client)
    row = await _row(a.id)
    assert (row.status, row.batch_id) == ("failed", None)
    assert "max_tokens too large" in row.error
    assert (await _row(other.id)).status == "pending"  # still running, untouched


async def test_collect_survives_a_fetch_error(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (502, {"error": {"message": "upstream"}})
    async with server.client() as client:
        await script.run_collect(client=client)
    assert (await _row(a.id)).status == "pending"


async def test_collect_repairs_missing_and_stale_embeddings(db_session, project, server, embed):
    no_vec = await _paper(db_session, project, title="No vector")
    stale = await _paper(db_session, project, title="Stale model")
    fine = await _paper(db_session, project, title="Fine")
    failed = await _paper(db_session, project, title="Failed")
    await _set_row(no_vec.id, status="ready", summary="s1", source="llm")
    await _set_row(stale.id, status="ready", summary="s2", doc_embedding="[1]", model="old-model")
    await _set_row(
        fine.id, status="ready", summary="s3", doc_embedding="[1]", model=settings.embedding_model
    )
    await _set_row(failed.id, status="failed", error="x")
    async with server.client() as client:
        result = await script.run_collect(client=client)
    assert server.requests == []  # no LLM, no batch call
    assert result["repaired"] == 2
    assert embed.await_count == 2
    for paper in (no_vec, stale):
        row = await _row(paper.id)
        assert row.doc_embedding is not None and row.model == settings.embedding_model
        assert row.status == "ready"
    assert (await _row(fine.id)).doc_embedding == "[1]"
    assert (await _row(failed.id)).doc_embedding is None
