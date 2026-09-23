"""Per-model request shaping: the gpt-5 family gets the parameters it accepts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.llm.params import adapt_request, is_reasoning_model

OPENAI = "https://api.openai.com/v1"


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5-mini", True),
        ("gpt-5.4-mini", True),
        ("GPT-5.1", True),
        ("o3-mini", True),
        ("o4-mini", True),
        ("gpt-4.1-mini", False),
        ("gpt-4o-mini", False),
        ("opus", False),
        ("llama-3.3-70b-versatile", False),
        # A router normalises its own parameters; rewriting for it is a guess.
        ("openai/gpt-5-mini", False),
    ],
)
def test_reasoning_models_are_recognised_by_bare_name(model, expected):
    assert is_reasoning_model(model) is expected


def test_a_reasoning_model_gets_max_completion_tokens_and_no_temperature():
    kwargs = {"max_tokens": 6000, "temperature": 0.2, "messages": []}
    out = adapt_request(model="gpt-5-mini", base_url=OPENAI, kwargs=kwargs, reasoning_effort="low")
    assert out == {"max_completion_tokens": 6000, "messages": [], "reasoning_effort": "low"}
    assert kwargs == {"max_tokens": 6000, "temperature": 0.2, "messages": []}  # not mutated


def test_an_empty_reasoning_effort_is_not_sent():
    out = adapt_request(model="gpt-5-mini", base_url=OPENAI, kwargs={"max_tokens": 10})
    assert "reasoning_effort" not in out


def test_an_older_model_is_left_alone_even_with_an_effort_configured():
    kwargs = {"max_tokens": 10, "temperature": 0.2}
    out = adapt_request(
        model="gpt-4.1-mini", base_url=OPENAI, kwargs=kwargs, reasoning_effort="low"
    )
    assert out == kwargs


def test_openai_streams_ask_for_usage_and_other_hosts_do_not():
    on_openai = adapt_request(model="gpt-4.1-mini", base_url=OPENAI, kwargs={"stream": True})
    assert on_openai["stream_options"] == {"include_usage": True}
    proxy = adapt_request(
        model="opus", base_url="http://host.docker.internal:8787/v1", kwargs={"stream": True}
    )
    assert "stream_options" not in proxy
    unstreamed = adapt_request(model="gpt-4.1-mini", base_url=OPENAI, kwargs={})
    assert "stream_options" not in unstreamed


async def test_the_pool_shapes_each_provider_by_its_own_model(monkeypatch):
    """A gpt-4.1 primary and a gpt-5 fallback in one pool: the retry against
    the fallback must not carry the primary's `max_tokens`."""
    import httpx
    from openai import RateLimitError

    from app.llm import client

    monkeypatch.setattr(settings, "llm_reasoning_effort", "low")
    request = httpx.Request("POST", OPENAI)
    exhausted = RateLimitError("quota", response=httpx.Response(429, request=request), body=None)
    create = AsyncMock(side_effect=[exhausted, SimpleNamespace(choices=[], usage=None)])
    providers = [
        SimpleNamespace(base_url=OPENAI, model="gpt-4.1-mini"),
        SimpleNamespace(base_url=OPENAI, model="gpt-5-mini"),
    ]
    pool = MagicMock()
    pool.__len__.return_value = 2
    type(pool).current = property(lambda self: providers[create.await_count])
    pool.client.return_value = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    with patch.object(client, "get_pool", return_value=pool):
        await client.create_chat_completion(max_tokens=100, messages=[])

    first, second = (c.kwargs for c in create.await_args_list)
    assert first["model"] == "gpt-4.1-mini" and first["max_tokens"] == 100
    assert "reasoning_effort" not in first
    assert second["model"] == "gpt-5-mini" and second["max_completion_tokens"] == 100
    assert "max_tokens" not in second and second["reasoning_effort"] == "low"
