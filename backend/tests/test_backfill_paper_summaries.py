"""The batch backfill: `openrouter_batch` (HTTP, no DB) and the submit/collect script.

Every HTTP call goes through an `httpx.MockTransport`; nothing reaches
OpenRouter. Rows are only ever written through Core statements or
`write_record` (an ORM write binds `doc_embedding` as varchar on Postgres),
and read here with Core selects.
"""

import contextlib
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
        self.listing: dict = {"object": "list", "data": [], "has_more": False}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            status, payload = self.submit_response
            return httpx.Response(status, json=payload)
        if request.url.path.endswith("/batches"):
            return httpx.Response(200, json=self.listing)
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


async def test_submit_402_releases_the_marker(db_session, project, server):
    """A definite refusal: no batch exists, so the rows leave flight at once
    (failed, re-selected next run) and a good summary is untouched."""
    a = await _paper(db_session, project, title="A")
    good = await _paper(db_session, project, title="Good")
    await _set_row(good.id, status="ready", summary="good summary", source="llm")
    server.submit_response = (402, {"error": {"message": "Insufficient credits"}})
    async with server.client() as client:
        with pytest.raises(ob.BatchError):
            await script.run_submit(force=True, client=client)
    row = await _row(a.id)
    assert (row.status, row.batch_id) == ("failed", None)
    assert "Insufficient credits" in row.error
    kept = await _row(good.id)
    assert (kept.status, kept.batch_id, kept.summary) == ("ready", None, "good summary")


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


# --- fix round 1: money safety ---------------------------------------------


def _raising_client(exc: Exception, server: Server) -> httpx.AsyncClient:
    def handler(request):
        server.requests.append(request)
        raise exc

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _marker_of(paper_id: str) -> str:
    row = await _row(paper_id)
    assert row.batch_id.startswith(script.MARKER_PREFIX)
    return row.batch_id


async def test_submit_wire_bytes_keep_key_order_and_content_type(db_session, project, server):
    await _paper(db_session, project, title="A")
    async with server.client() as client:
        await script.run_submit(client=client)
    request = server.requests[0]
    assert request.headers["content-type"] == "application/json"
    raw = request.content.decode()
    order = [raw.index(f'"{k}"') for k in ("endpoint", "model", "provider", "requests")]
    assert order == sorted(order)
    assert raw.startswith('{"endpoint"')


async def test_submit_connection_error_keeps_the_marker(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    async with _raising_client(httpx.ReadTimeout("slow"), server) as client:
        with pytest.raises(script.OutcomeUnknown) as info:
            await script.run_submit(client=client)
    marker = await _marker_of(a.id)
    assert info.value.marker == marker
    assert (await _row(a.id)).status == "pending"
    # A re-run must not send it again: the batch may exist.
    fresh = Server()
    async with fresh.client() as client:
        result = await script.run_submit(client=client)
    assert result["papers"] == 0 and fresh.posted() == []


async def test_collect_adopt_repoints_marker_rows_and_collects(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    async with _raising_client(httpx.ConnectError("reset"), server) as client:
        with pytest.raises(script.OutcomeUnknown):
            await script.run_submit(client=client)
    marker = await _marker_of(a.id)
    # The sweep reports a marker but never fetches it.
    async with server.client() as client:
        swept = await script.run_collect(client=client)
    assert swept["markers"] == [marker] and (await _row(a.id)).batch_id == marker
    adopted = _batch("completed", results=[_ok(a.id)])
    adopted["request_counts"] = {"total": 1, "completed": 1, "failed": 0}
    server.batches["batch_9"] = (200, adopted)
    async with server.client() as client:
        await script.run_collect(batch_id="batch_9", adopt=marker, client=client)
    row = await _row(a.id)
    assert (row.status, row.batch_id, row.source) == ("ready", None, "llm")


async def test_collect_adopt_needs_a_batch_id_and_a_marker(db_session):
    with pytest.raises(ValueError):
        await script.run_collect(adopt="submitting:x")
    with pytest.raises(ValueError):
        await script.run_collect(batch_id="b", adopt="batch_1")


async def test_collect_release_frees_marker_rows_for_the_next_submit(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    async with _raising_client(httpx.ConnectError("reset"), server) as client:
        with pytest.raises(script.OutcomeUnknown):
            await script.run_submit(client=client)
    marker = await _marker_of(a.id)
    async with server.client() as client:
        await script.run_collect(release=marker, client=client)
        row = await _row(a.id)
        assert (row.status, row.batch_id) == ("pending", None)
        await script.run_submit(client=client)
    assert [r["custom_id"] for r in server.posted()[0]["requests"]] == [a.id]


async def test_list_prints_remote_batches(server, capsys):
    server.listing = {
        "object": "list",
        "data": [
            {
                "id": "batch_x",
                "status": "in_progress",
                "created_at": 1_800_000_000,
                "request_counts": {"total": 200, "completed": 3, "failed": 0},
                "model": "deepseek/deepseek-v4.1-flash",
                "results": None,
            }
        ],
        "has_more": False,
    }
    async with server.client() as client:
        batches = await script.run_list(limit=5, client=client)
    assert [b["id"] for b in batches] == ["batch_x"]
    assert server.requests[0].url.params["limit"] == "5"
    out = capsys.readouterr().out
    assert "batch_x" in out and "in_progress" in out and "200 requests" in out
    assert "2027-01-15" in out


async def test_resubmitting_a_ready_row_never_loses_its_summary(db_session, project, server):
    good = await _paper(db_session, project, title="Good")
    await _set_row(good.id, status="ready", summary="good summary", source="llm")
    async with server.client() as client:
        await script.run_submit(force=True, client=client)
        row = await _row(good.id)
        assert (row.status, row.batch_id, row.summary) == ("ready", "batch_1", "good summary")
        # In flight over a good row: not re-selected, even with --force.
        server.submit_response = (202, {"id": "batch_2"})
        assert (await script.run_submit(force=True, client=client))["papers"] == 0
        server.batches["batch_1"] = (200, _batch("expired", error={"message": "too slow"}))
        result = await script.run_collect(client=client)
    row = await _row(good.id)
    assert (row.status, row.batch_id, row.summary, row.error) == (
        "ready",
        None,
        "good summary",
        None,
    )
    assert result["kept"] == 1 and result["failed"] == 0


async def test_collect_request_error_over_a_ready_row_keeps_the_summary(
    db_session, project, server
):
    good = await _paper(db_session, project, title="Good", abstract="abs")
    await _set_row(good.id, status="ready", summary="good summary", batch_id="batch_1")
    length = _ok(good.id, finish="length")
    server.batches["batch_1"] = (200, _batch("completed", results=[length]))
    async with server.client() as client:
        result = await script.run_collect(client=client)
    row = await _row(good.id)
    assert (row.status, row.batch_id, row.summary) == ("ready", None, "good summary")
    assert result["kept"] == 1 and result["fallback"] == 0


async def test_collect_finish_length_on_a_new_row_falls_back_to_the_abstract(
    db_session, project, server
):
    a = await _paper(db_session, project, title="A", abstract="The abstract.")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (200, _batch("completed", results=[_ok(a.id, finish="length")]))
    async with server.client() as client:
        result = await script.run_collect(client=client)
    row = await _row(a.id)
    assert (row.status, row.source, row.summary) == ("ready", "abstract", "The abstract.")
    assert result["fallback"] == 1


async def test_collect_404_on_a_named_batch_fails_its_rows(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    # Not in server.batches: GET answers 404.
    async with server.client() as client:
        await script.run_collect(client=client)
        assert (await _row(a.id)).batch_id == "batch_1"  # sweep: only reported
        await script.run_collect(batch_id="batch_1", client=client)
    row = await _row(a.id)
    assert (row.status, row.batch_id) == ("failed", None)


async def test_collect_410_fails_rows_and_keeps_ready_ones(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    good = await _paper(db_session, project, title="Good")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    await _set_row(good.id, status="ready", summary="kept", batch_id="batch_1")
    server.batches["batch_1"] = (410, {"error": {"message": "results expired"}})
    async with server.client() as client:
        await script.run_collect(client=client)
    row = await _row(a.id)
    assert (row.status, row.batch_id) == ("failed", None)
    assert "results expired" in row.error
    kept = await _row(good.id)
    assert (kept.status, kept.summary, kept.batch_id) == ("ready", "kept", None)


async def test_collect_402_on_get_leaves_rows_in_flight(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (402, _batch("completed", results=None))
    async with server.client() as client:
        await script.run_collect(client=client)
    assert (await _row(a.id)).batch_id == "batch_1"


async def test_collect_leaves_cancelling_batches_alone(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (200, _batch("cancelling"))
    async with server.client() as client:
        await script.run_collect(client=client)
    assert ((await _row(a.id)).status, (await _row(a.id)).batch_id) == ("pending", "batch_1")


async def test_collect_expired_batch_fails_pending_rows(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (200, _batch("expired", error={"message": "past expires_at"}))
    async with server.client() as client:
        await script.run_collect(client=client)
    row = await _row(a.id)
    assert (row.status, row.batch_id) == ("failed", None)
    assert "expired" in row.error


async def test_collect_fails_rows_with_no_result(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    lost = await _paper(db_session, project, title="Lost")
    for p in (a, lost):
        await _set_row(p.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (200, _batch("completed", results=[_ok(a.id)]))
    async with server.client() as client:
        await script.run_collect(client=client)
    row = await _row(lost.id)
    assert (row.status, row.batch_id, row.error) == ("failed", None, "no result for this paper")
    assert (await _row(a.id)).status == "ready"


async def test_collect_fails_a_paper_no_longer_ready(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    async with SessionLocal() as db:
        paper = await db.get(Paper, a.id)
        paper.status = PaperStatus.PENDING
        await db.commit()
    server.batches["batch_1"] = (200, _batch("completed", results=[_ok(a.id)]))
    async with server.client() as client:
        await script.run_collect(client=client)
    row = await _row(a.id)
    assert (row.status, row.batch_id, row.summary) == ("failed", None, None)


async def test_collect_abandon_takes_a_batch_out_of_flight(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    good = await _paper(db_session, project, title="Good")
    await _set_row(a.id, status="pending", batch_id="batch_x")
    await _set_row(good.id, status="ready", summary="kept", batch_id="batch_x")
    # Unknown to OpenRouter (GET 404): nothing can still arrive, so allowed.
    async with server.client() as client:
        await script.run_collect(abandon="batch_x", client=client)
    assert [r.method for r in server.requests] == ["GET"]
    assert ((await _row(a.id)).status, (await _row(a.id)).batch_id) == ("failed", None)
    assert ((await _row(good.id)).status, (await _row(good.id)).summary) == ("ready", "kept")


async def test_validation_failure_fails_only_the_named_request(db_session, project, server):
    bad = await _paper(db_session, project, title="Bad")
    fine = await _paper(db_session, project, title="Fine")
    for p in (bad, fine):
        await _set_row(p.id, status="pending", batch_id="batch_1")
    message = f"request {bad.id}: max_tokens is greater than the model limit"
    server.batches["batch_1"] = (200, _batch("failed", error={"message": message}))
    async with server.client() as client:
        result = await script.run_collect(client=client)
        assert result["failed"] == 1 and result["released"] == 1
        row = await _row(bad.id)
        assert (row.status, row.batch_id) == ("failed", None)
        row = await _row(fine.id)
        assert (row.status, row.batch_id) == ("pending", None)
        # Both are re-selected by the next submit.
        await script.run_submit(client=client)
    assert {r["custom_id"] for r in server.posted()[0]["requests"]} == {bad.id, fine.id}


async def test_submit_estimates_per_paper_and_per_char_and_refuses_over_max_cost(
    db_session, project, server
):
    big = "x" * 200_000
    await _paper(db_session, project, title="Long", body=big, abstract=None)
    async with server.client() as client:
        dry = await script.run_submit(dry_run=True, client=client)
        est = dry["estimates"]
        assert est["per_paper"] == pytest.approx(script.SPIKE_USD_PER_PAPER)
        assert est["per_char"] == pytest.approx(200_000 * script.SPIKE_USD_PER_CHAR)
        assert dry["estimate_usd"] == pytest.approx(max(est["per_paper"], est["per_char"]))
        with pytest.raises(script.CostExceeded):
            await script.run_submit(max_cost=0.001, client=client)
    assert server.requests == []
    assert script.SPIKE_USD_PER_CHAR == pytest.approx(0.0008 * 20 / 1_119_154)


async def test_a_busy_lock_refuses_both_commands(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    with patch.object(script, "_try_lock", new=AsyncMock(return_value=False)):
        async with server.client() as client:
            with pytest.raises(script.LockBusy):
                await script.run_submit(client=client)
            with pytest.raises(script.LockBusy):
                await script.run_collect(client=client)
    assert server.requests == [] and await _row(a.id) is None


def test_still_ours_locks_the_row_for_update():
    from sqlalchemy.dialects import postgresql

    sql = str(script._ours_stmt("p").compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql


# --- fix round 2: only a definite refusal releases; checked adopt/abandon ---


@pytest.mark.parametrize("status", [503, 429, 500, 408, 504, 524])
async def test_submit_unclear_status_keeps_the_marker(db_session, project, server, status):
    a = await _paper(db_session, project, title="A")
    server.submit_response = (status, {"error": {"message": "upstream trouble"}})
    async with server.client() as client:
        with pytest.raises(script.OutcomeUnknown) as info:
            await script.run_submit(client=client)
    marker = await _marker_of(a.id)
    assert info.value.marker == marker
    assert (await _row(a.id)).status == "pending"


@pytest.mark.parametrize("payload", [{"status": "validating"}, {"id": ""}, None])
async def test_submit_202_without_a_readable_id_keeps_the_marker(
    db_session, project, server, payload
):
    a = await _paper(db_session, project, title="A")

    def handler(request):
        server.requests.append(request)
        if payload is None:
            return httpx.Response(202, content=b"<html>accepted</html>")
        return httpx.Response(202, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(script.OutcomeUnknown):
            await script.run_submit(client=client)
    await _marker_of(a.id)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
async def test_submit_definite_refusal_releases_the_marker(db_session, project, server, status):
    a = await _paper(db_session, project, title="A")
    server.submit_response = (status, {"error": {"message": "refused"}})
    async with server.client() as client:
        with pytest.raises(ob.BatchError) as info:
            await script.run_submit(client=client)
    assert info.value.definite
    row = await _row(a.id)
    assert (row.status, row.batch_id) == ("failed", None)


def test_batch_error_is_not_definite_unless_said_so():
    assert not ob.BatchError(500, "x").definite
    assert ob.BatchError(402, "x", definite=True).definite
    assert ob.DEFINITE_REFUSALS == {400, 401, 402, 403, 404, 413, 422}


async def _orphan(project, db_session, server, n: int = 1) -> tuple[list[Paper], str]:
    papers = [await _paper(db_session, project, title=f"O{i}") for i in range(n)]
    async with _raising_client(httpx.ReadTimeout("slow"), server) as client:
        with pytest.raises(script.OutcomeUnknown):
            await script.run_submit(client=client)
    return papers, await _marker_of(papers[0].id)


async def test_adopt_refuses_an_unknown_batch(db_session, project, server):
    (a,), marker = await _orphan(project, db_session, server)
    async with server.client() as client:  # batch_wrong: GET 404
        with pytest.raises(ValueError, match="404"):
            await script.run_collect(batch_id="batch_wrong", adopt=marker, client=client)
    assert (await _row(a.id)).batch_id == marker


async def test_adopt_refuses_a_request_count_mismatch(db_session, project, server):
    papers, marker = await _orphan(project, db_session, server, n=2)
    other = _batch("in_progress")
    other["request_counts"] = {"total": 200, "completed": 0, "failed": 0}
    server.batches["batch_other"] = (200, other)
    async with server.client() as client:
        with pytest.raises(ValueError, match="200 requests"):
            await script.run_collect(batch_id="batch_other", adopt=marker, client=client)
    for p in papers:
        assert (await _row(p.id)).batch_id == marker


async def test_adopt_accepts_a_402_batch_and_leaves_it_in_flight(db_session, project, server):
    (a,), marker = await _orphan(project, db_session, server)
    withheld = _batch("completed", results=None)
    withheld["request_counts"] = {"total": 1, "completed": 1, "failed": 0}
    server.batches["batch_9"] = (402, withheld)
    async with server.client() as client:
        await script.run_collect(batch_id="batch_9", adopt=marker, client=client)
    assert (await _row(a.id)).batch_id == "batch_9"


async def test_abandon_refuses_a_batch_still_in_progress(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (200, _batch("in_progress"))
    async with server.client() as client:
        with pytest.raises(ValueError, match="not final"):
            await script.run_collect(abandon="batch_1", client=client)
        assert (await _row(a.id)).batch_id == "batch_1"
        # A 5xx is not proof either.
        server.batches["batch_1"] = (503, {"error": {"message": "down"}})
        with pytest.raises(ValueError, match="503"):
            await script.run_collect(abandon="batch_1", client=client)
        assert (await _row(a.id)).batch_id == "batch_1"
        await script.run_collect(abandon="batch_1", force_abandon=True, client=client)
    row = await _row(a.id)
    assert (row.status, row.batch_id) == ("failed", None)


async def test_abandon_accepts_a_final_batch(db_session, project, server):
    a = await _paper(db_session, project, title="A")
    await _set_row(a.id, status="pending", batch_id="batch_1")
    server.batches["batch_1"] = (200, _batch("cancelled"))
    async with server.client() as client:
        await script.run_collect(abandon="batch_1", client=client)
    assert ((await _row(a.id)).status, (await _row(a.id)).batch_id) == ("failed", None)


async def test_the_lock_connection_commits_after_locking_and_unlock_never_masks(monkeypatch):
    """Postgres path, faked: the lock connection ends its transaction right
    after locking, and a failing unlock does not replace the run's error."""
    calls: list[str] = []

    class Conn:
        dialect = type("D", (), {"name": "postgresql"})()

        async def execute(self, stmt, params=None):
            sql = str(stmt)
            calls.append(sql)
            if "unlock" in sql:
                raise RuntimeError("connection gone")
            return type("R", (), {"scalar": lambda self: True})()

        async def commit(self):
            calls.append("COMMIT")

    class Engine:
        @contextlib.asynccontextmanager
        async def connect(self):
            yield Conn()

    monkeypatch.setattr(script, "engine", Engine())
    with pytest.raises(KeyError, match="original"):
        async with script._run_lock():
            assert calls == ["SELECT pg_try_advisory_lock(:k)", "COMMIT"]
            raise KeyError("original")
    assert calls[-1] == "SELECT pg_advisory_unlock(:k)"
