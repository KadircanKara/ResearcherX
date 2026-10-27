"""The live OpenRouter summary client: chain, retry-from-next, never raises."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings, settings
from app.services import paper_summary_client as client_mod
from app.services.paper_summary_client import GeneratedSummary, generate_summary

CHAIN = [
    "deepseek/deepseek-v4.1-flash",
    "deepseek/deepseek-v4.1-flash-1017",
    "anthropic/claude-haiku-5.5",
]
GOOD = json.dumps({"summary": "It studies X. It finds Y.", "keywords": ["UAV", "PPO"]})


def _response(model, content=GOOD, finish_reason="stop", cost=0.0012):
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=200, total_tokens=1200)
    if cost is not None:
        usage.cost = cost
    choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
    return SimpleNamespace(model=model, choices=[choice], usage=usage)


@pytest.fixture
def fake_create(monkeypatch):
    monkeypatch.setattr(settings, "summary_api_key", "sk-test")
    monkeypatch.setattr(settings, "summary_models", list(CHAIN))
    create = AsyncMock()
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(client_mod, "_get_client", lambda: fake)
    return create


async def _run():
    return await generate_summary("A Title", "An abstract.", "Full text.")


async def test_sends_models_chain_and_reasoning_off(fake_create):
    fake_create.return_value = _response(CHAIN[0])
    await _run()
    kwargs = fake_create.await_args.kwargs
    assert kwargs["model"] == CHAIN[0]
    body = kwargs["extra_body"]
    assert body["models"] == CHAIN
    assert body["reasoning"] == {"enabled": False}
    assert body["provider"] == {"require_parameters": True}
    assert body["usage"] == {"include": True}
    assert kwargs["max_tokens"] == settings.paper_summary_max_tokens
    assert kwargs["response_format"]["type"] == "json_schema"


async def test_returns_served_model(fake_create):
    fake_create.return_value = _response(CHAIN[2])
    out = await _run()
    assert isinstance(out, GeneratedSummary)
    assert out.model == CHAIN[2]
    assert out.record.summary == "It studies X. It finds Y."
    assert out.record.keywords == ("UAV", "PPO")
    assert (out.input_tokens, out.output_tokens) == (1000, 200)
    assert out.cost_usd == pytest.approx(0.0012)


async def test_unusable_answer_retries_from_next_model(fake_create):
    fake_create.side_effect = [
        _response(CHAIN[0], content='{"summary": "x"}'),  # schema mismatch
        _response(CHAIN[1]),
    ]
    out = await _run()
    assert out is not None and out.model == CHAIN[1]
    second = fake_create.await_args_list[1].kwargs
    assert second["model"] == CHAIN[1]
    assert second["extra_body"]["models"] == CHAIN[1:]


async def test_finish_reason_length_counts_as_unusable(fake_create):
    fake_create.side_effect = [
        _response(CHAIN[1], finish_reason="length"),
        _response(CHAIN[2]),
    ]
    out = await _run()
    assert out is not None and out.model == CHAIN[2]
    # Served model was CHAIN[1], so the retry starts after it, not at CHAIN[1].
    assert fake_create.await_args_list[1].kwargs["extra_body"]["models"] == CHAIN[2:]


async def test_unknown_served_model_drops_only_the_head(fake_create):
    fake_create.side_effect = [_response("someone/else", content="nope"), _response(CHAIN[1])]
    out = await _run()
    assert out is not None and out.model == CHAIN[1]
    assert fake_create.await_args_list[1].kwargs["extra_body"]["models"] == CHAIN[1:]


async def test_missing_cost_is_none(fake_create):
    fake_create.return_value = _response(CHAIN[0], cost=None)
    out = await _run()
    assert out is not None and out.cost_usd is None


async def test_exhausted_chain_returns_none(fake_create):
    fake_create.side_effect = [_response(m, content="not json") for m in CHAIN]
    assert await _run() is None
    assert fake_create.await_count == 3


async def test_exception_returns_none_and_logs(fake_create, monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(client_mod, "log", log)
    fake_create.side_effect = RuntimeError("upstream exploded")
    assert await _run() is None
    log.warning.assert_called_once()
    args, kwargs = log.warning.call_args
    assert args[0] == "paper_summary_failed"
    assert "upstream exploded" in kwargs["error"]
    assert "sk-test" not in repr(log.warning.call_args)


async def test_cancellation_propagates(fake_create):
    import asyncio

    fake_create.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await _run()


async def test_empty_key_makes_no_call(fake_create, monkeypatch):
    monkeypatch.setattr(settings, "summary_api_key", "")
    assert await _run() is None
    fake_create.assert_not_awaited()


def test_summary_models_parses_csv_env(monkeypatch):
    monkeypatch.setenv("SUMMARY_MODELS", "a/one, b/two ,,c/three")
    assert Settings().summary_models == ["a/one", "b/two", "c/three"]
    assert Settings(summary_models=["x/y"]).summary_models == ["x/y"]
    monkeypatch.delenv("SUMMARY_MODELS")
    assert Settings(_env_file=None).summary_models == CHAIN
