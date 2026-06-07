"""Provider pool rotation on daily-quota exhaustion."""

import httpx
import pytest
from openai import RateLimitError

import app.llm.client as client_mod
from app.core.config import LLMFallback, settings
from app.llm.client import ProviderPool, Provider, create_chat_completion, get_pool


def rate_limit_error(msg: str = "tokens per day (TPD) exhausted") -> RateLimitError:
    request = httpx.Request("POST", "http://test/chat/completions")
    response = httpx.Response(429, request=request, json={"error": {"message": msg}})
    return RateLimitError(msg, response=response, body=None)


class FakeCompletions:
    """Scripted completions endpoint: each entry is a return value or exception."""

    def __init__(self, script: list):
        self._script = script
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._script.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeClient:
    def __init__(self, script: list):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(script)


@pytest.fixture
def pool_of_three(monkeypatch):
    """Pool of three providers whose clients run scripted responses."""

    def build(scripts: dict[str, list]):
        providers = [Provider(url, "key", f"model-{i}") for i, url in enumerate(scripts)]
        pool = ProviderPool(providers)
        clients = {url: FakeClient(script) for url, script in scripts.items()}
        monkeypatch.setattr(pool, "client", lambda p: clients[p.base_url])
        monkeypatch.setattr(client_mod, "_pool", pool)
        return pool, clients

    return build


async def test_no_rotation_on_success(pool_of_three):
    pool, clients = pool_of_three({"http://a": ["ok"], "http://b": [], "http://c": []})
    assert await create_chat_completion(messages=[]) == "ok"
    assert pool.current.base_url == "http://a"
    # The active provider's model was injected.
    assert clients["http://a"].chat.completions.calls[0]["model"] == "model-0"


async def test_rotates_on_rate_limit(pool_of_three):
    pool, clients = pool_of_three(
        {"http://a": [rate_limit_error()], "http://b": ["rescued"], "http://c": []}
    )
    assert await create_chat_completion(messages=[]) == "rescued"
    assert pool.current.base_url == "http://b"
    assert clients["http://b"].chat.completions.calls[0]["model"] == "model-1"


async def test_sticky_index_skips_dead_provider(pool_of_three):
    pool, clients = pool_of_three(
        {"http://a": [rate_limit_error()], "http://b": ["first", "second"], "http://c": []}
    )
    await create_chat_completion(messages=[])
    # Next call starts at the failed-over provider — no re-burning a's backoff.
    assert await create_chat_completion(messages=[]) == "second"
    assert len(clients["http://a"].chat.completions.calls) == 1


async def test_raises_when_all_providers_exhausted(pool_of_three):
    pool, _ = pool_of_three(
        {
            "http://a": [rate_limit_error("a dead")],
            "http://b": [rate_limit_error("b dead")],
            "http://c": [rate_limit_error("c dead")],
        }
    )
    with pytest.raises(RateLimitError, match="c dead"):
        await create_chat_completion(messages=[])


async def test_non_rate_errors_propagate_without_rotation(pool_of_three):
    pool, clients = pool_of_three(
        {"http://a": [ValueError("not a quota problem")], "http://b": ["unused"], "http://c": []}
    )
    with pytest.raises(ValueError):
        await create_chat_completion(messages=[])
    assert pool.current.base_url == "http://a"
    assert clients["http://b"].chat.completions.calls == []


async def test_concurrent_failures_advance_only_once(pool_of_three):
    """Two parallel calls failing on the same provider must not double-advance.

    Observed live: simultaneous Gemini 429s each advanced the index,
    skipping past healthy OpenRouter back onto exhausted Groq.
    """
    import asyncio

    pool, clients = pool_of_three(
        {
            "http://a": [rate_limit_error(), rate_limit_error()],  # both calls fail here
            "http://b": ["one", "two"],  # both must be rescued HERE
            "http://c": ["never"],
        }
    )
    results = await asyncio.gather(
        create_chat_completion(messages=[]), create_chat_completion(messages=[])
    )
    assert sorted(results) == ["one", "two"]
    assert pool.current.base_url == "http://b"  # not double-advanced to c
    assert clients["http://c"].chat.completions.calls == []


def test_pool_built_from_settings(monkeypatch):
    monkeypatch.setattr(
        settings,
        "llm_fallbacks",
        [
            LLMFallback(base_url="http://fb1", api_key="k1", model="m1"),
            LLMFallback(base_url="http://fb2", api_key="", model="m2"),  # no key → skipped
        ],
    )
    client_mod.reset_pool()
    try:
        pool = get_pool()
        urls = [p.base_url for p in pool._providers]
        assert urls[0] == settings.llm_base_url  # primary first
        assert "http://fb1" in urls
        assert "http://fb2" not in urls  # keyless fallback skipped
        assert len(pool) == 2
    finally:
        client_mod.reset_pool()
