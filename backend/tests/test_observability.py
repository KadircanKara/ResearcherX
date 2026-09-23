"""Tracing: spans reach Langfuse's attribute names, nest into one trace per
chat turn, and never change what the traced code returns or raises."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import RateLimitError
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import observability
from app.core.config import settings


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observability.install_provider(provider)
    yield exporter
    observability.install_provider(None)


def by_name(exporter: InMemorySpanExporter) -> dict:
    return {s.name: s for s in exporter.get_finished_spans()}


# ── setup ─────────────────────────────────────────────────────────────────


def test_tracing_is_off_without_keys():
    assert settings.langfuse_public_key == ""
    assert observability.init_tracing() is False
    assert observability.enabled() is False
    with observability.span("x") as s:
        assert not s.is_recording()


def test_the_exporter_targets_langfuse_otlp_with_basic_auth():
    assert (
        observability.langfuse_otlp_endpoint("https://cloud.langfuse.com/")
        == "https://cloud.langfuse.com/api/public/otel/v1/traces"
    )
    headers = observability.langfuse_headers("pk-lf-1", "sk-lf-2")
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-2").decode()
    assert headers["x-langfuse-ingestion-version"] == "4"


async def test_init_builds_a_provider_when_both_keys_are_set(monkeypatch):
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-lf-test")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-lf-test")
    try:
        assert observability.init_tracing() is True
        assert observability.enabled() is True
    finally:
        # Nothing was recorded, so shutdown exports nothing.
        await observability.shutdown_tracing()
    assert observability.enabled() is False


def test_a_value_that_will_not_serialise_costs_its_attribute_not_the_call(spans):
    cycle: dict = {}
    cycle["self"] = cycle
    with observability.span("x") as s:
        observability.set_json(s, "a", cycle)  # ValueError: circular reference
        observability.set_json(s, "b", {"when": object()})  # default=str rescues it
    attrs = by_name(spans)["x"].attributes
    assert "a" not in attrs
    assert "b" in attrs


def test_oversized_content_is_truncated(spans):
    with observability.span("x") as s:
        observability.set_json(s, "big", "a" * (observability.MAX_CONTENT_CHARS + 10))
    value = by_name(spans)["x"].attributes["big"]
    assert value.endswith("[truncated 10 chars]")


def test_content_capture_can_be_switched_off(spans, monkeypatch):
    monkeypatch.setattr(settings, "langfuse_capture_content", False)
    with observability.span("x") as s:
        observability.set_content(s, "langfuse.observation.input", "secret paper text")
    assert "langfuse.observation.input" not in by_name(spans)["x"].attributes


# ── LLM generations ───────────────────────────────────────────────────────


def _completion(text: str, prompt: int, completion: int):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
        ),
    )


def _fake_pool(create):
    provider = SimpleNamespace(base_url="http://llm.test/v1", model="test-model")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    pool = MagicMock()
    pool.__len__.return_value = 1
    pool.current = provider
    pool.client.return_value = client
    return pool


async def test_a_completion_is_one_generation_with_model_usage_and_content(spans):
    from app.llm import client

    create = AsyncMock(return_value=_completion("hello", 12, 3))
    with patch.object(client, "get_pool", return_value=_fake_pool(create)):
        response = await client.create_chat_completion(
            observation="chat.widen",
            max_tokens=50,
            messages=[{"role": "user", "content": "hi"}],
        )

    assert response.choices[0].message.content == "hello"
    assert "observation" not in create.call_args.kwargs  # never reaches the SDK
    attrs = by_name(spans)["chat.widen"].attributes
    assert attrs["langfuse.observation.type"] == "generation"
    assert attrs["langfuse.observation.model.name"] == "test-model"
    assert json.loads(attrs["langfuse.observation.usage_details"]) == {
        "input": 12,
        "output": 3,
        "total": 15,
    }
    assert json.loads(attrs["langfuse.observation.input"]) == [{"role": "user", "content": "hi"}]
    assert attrs["langfuse.observation.output"] == "hello"
    assert json.loads(attrs["langfuse.observation.model.parameters"]) == {"max_tokens": 50}


async def test_a_stream_records_first_token_output_and_usage_then_closes(spans):
    from app.llm import client

    def chunk(content, usage=None):
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=content))] if content else [],
            usage=usage,
        )

    async def stream():
        yield chunk("Hel")
        yield chunk("lo")
        yield chunk(
            None,
            SimpleNamespace(prompt_tokens=100, completion_tokens=2, total_tokens=102),
        )

    create = AsyncMock(return_value=stream())
    with patch.object(client, "get_pool", return_value=_fake_pool(create)):
        response = await client.create_chat_completion(
            observation="chat.answer", stream=True, messages=[]
        )
        assert "chat.answer" not in by_name(spans)  # open until drained
        got = [c async for c in response]

    assert len(got) == 3  # chunks pass through untouched
    attrs = by_name(spans)["chat.answer"].attributes
    assert attrs["langfuse.observation.output"] == "Hello"
    assert "langfuse.observation.completion_start_time" in attrs
    assert attrs["langfuse.observation.metadata.ttft_ms"] >= 0
    assert json.loads(attrs["langfuse.observation.usage_details"])["input"] == 100


async def test_an_abandoned_stream_still_closes_its_span(spans):
    from app.llm import client

    async def stream():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="a"))])
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="b"))])

    with patch.object(
        client, "get_pool", return_value=_fake_pool(AsyncMock(return_value=stream()))
    ):
        response = await client.create_chat_completion(stream=True, messages=[])
        async for _ in response:
            break
        await response.aclose()

    attrs = by_name(spans)["llm"].attributes
    assert attrs["langfuse.observation.level"] == "WARNING"
    assert attrs["langfuse.observation.output"] == "a"


async def test_a_failed_completion_is_an_error_span_and_still_raises(spans):
    from app.llm import client

    request = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    exc = RateLimitError("quota", response=httpx.Response(429, request=request), body=None)
    with (
        patch.object(client, "get_pool", return_value=_fake_pool(AsyncMock(side_effect=exc))),
        pytest.raises(RateLimitError),
    ):
        await client.create_chat_completion(messages=[])

    s = by_name(spans)["llm"]
    assert s.attributes["langfuse.observation.level"] == "ERROR"
    assert [e.name for e in s.events][:1] == ["provider_exhausted"]


# ── embeddings and rerank ─────────────────────────────────────────────────


async def test_an_embedding_call_is_an_embedding_observation_without_the_texts(spans):
    from app.services.embedding_service import EmbeddingService

    svc = EmbeddingService()
    response = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=[0.1])],
        usage=SimpleNamespace(prompt_tokens=7, total_tokens=7),
    )
    with patch.object(svc._client.embeddings, "create", AsyncMock(return_value=response)):
        assert await svc.embed("a question", task_type="RETRIEVAL_QUERY") == [0.1]

    attrs = by_name(spans)["embedding"].attributes
    assert attrs["langfuse.observation.type"] == "embedding"
    assert attrs["langfuse.observation.model.name"] == settings.embedding_model
    assert json.loads(attrs["langfuse.observation.usage_details"]) == {"input": 7, "total": 7}
    assert "a question" not in json.dumps(dict(attrs))


async def test_a_rerank_records_cohere_billed_search_units(spans):
    from app.local_rag.rerank import CohereReranker

    def handler(request):
        return httpx.Response(
            200,
            json={
                "results": [{"index": 1, "relevance_score": 0.9}],
                "meta": {"billed_units": {"search_units": 1}},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reranker = CohereReranker(api_key="k", client=http)
        results = await reranker.rerank("q", ["a", "b"], top_n=1)

    assert [r.index for r in results] == [1]
    attrs = by_name(spans)["cohere.rerank"].attributes
    assert json.loads(attrs["langfuse.observation.usage_details"]) == {"search_units": 1}
    assert attrs["langfuse.observation.metadata.n_documents"] == 2


async def test_a_failed_rerank_is_a_warning_and_still_fails_open(spans):
    from app.local_rag.rerank import CohereReranker

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    ) as http:
        assert await CohereReranker(api_key="k", client=http).rerank("q", ["a"], top_n=1) is None

    assert by_name(spans)["cohere.rerank"].attributes["langfuse.observation.level"] == "WARNING"


# ── the chat turn ─────────────────────────────────────────────────────────


async def _turn(conversation_id: str, stream):
    from app.services.chat_service import ChatService

    svc = ChatService()
    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[])),
        patch.object(svc._chat_agent, "stream", return_value=stream),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        return [e async for e in svc.respond(conversation_id, "Test question")]


async def test_a_chat_turn_is_one_trace_rooted_at_chat_turn(spans, db_session):
    from app.core.observability import start_span

    conv = await _conversation(db_session)

    async def stream():
        # The answer's own generation opens while the model streams; it must
        # nest under the turn even though the turn is never current across a
        # yield.
        gen = start_span("chat.answer", kind="generation")
        gen.end()
        yield "ok"

    events = await _turn(conv.id, stream())
    assert [e["event"] for e in events][-1] == "done"

    got = by_name(spans)
    turn = got["chat.turn"]
    assert turn.parent is None
    assert turn.attributes["langfuse.session.id"] == conv.id
    assert turn.attributes["langfuse.trace.input"] == "Test question"
    assert turn.attributes["langfuse.trace.output"] == "ok"
    assert turn.attributes["langfuse.trace.metadata.project_id"] == conv.project_id
    for child in ("chat.load", "chat.answer", "chat.persist"):
        assert got[child].parent.span_id == turn.context.span_id, child
        assert got[child].context.trace_id == turn.context.trace_id


async def test_a_failed_turn_marks_the_trace_and_still_yields_the_error_event(spans, db_session):
    conv = await _conversation(db_session)

    async def stream():
        raise RuntimeError("boom")
        yield  # pragma: no cover

    events = await _turn(conv.id, stream())
    assert events[-1]["event"] == "error"
    turn = by_name(spans)["chat.turn"]
    assert turn.attributes["langfuse.observation.level"] == "ERROR"
    assert "boom" not in turn.attributes["langfuse.observation.status_message"]


async def _conversation(db):
    from sqlalchemy import select

    from app.db.models import ChatConversation, ChatMessage, Project, ProjectMember, User
    from app.db.seed import seed_users

    await seed_users(db)
    await db.commit()
    you = (await db.execute(select(User).where(User.email == "you@researcherx.dev"))).scalar_one()
    project = Project(owner_id=you.id, title="Tracing", topic_keywords=[])
    db.add(project)
    await db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=you.id, role="owner"))
    conv = ChatConversation(project_id=project.id, title="t", created_by=you.id)
    db.add(conv)
    await db.flush()
    db.add(ChatMessage(conversation_id=conv.id, role="user", content="Test question"))
    await db.commit()
    await db.refresh(conv)
    return conv
