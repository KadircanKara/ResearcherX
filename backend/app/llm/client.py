"""Provider pool with daily-quota failover over OpenAI-compatible endpoints.

The pool is [primary from LLM_* env] + LLM_FALLBACKS (in order). All calls
go through :func:`create_chat_completion`, which injects the active
provider's model and rotates to the next provider when one keeps returning
429 after the SDK's own backoff retries — that's the signature of an
exhausted daily quota (Groq free tier: 100k tokens/day), where retrying
harder cannot help but a different provider can.

The active index is sticky module state (single-worker, like the bus and
limiter): once failed over, later calls start at the working provider
instead of re-burning the dead one's backoff. A process restart resets to
the primary — uvicorn --reload makes that automatic in dev.
"""

from functools import lru_cache

from openai import AsyncOpenAI, RateLimitError

from app.core.config import settings
from app.core.logging import log


class Provider:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model


class ProviderPool:
    def __init__(self, providers: list[Provider]) -> None:
        assert providers, "at least the primary provider must exist"
        self._providers = providers
        self._idx = 0

    def __len__(self) -> int:
        return len(self._providers)

    @property
    def current(self) -> Provider:
        return self._providers[self._idx]

    def advance_from(self, failed: Provider) -> Provider:
        """Move past `failed` — but only if it is still the active provider.

        Parallel calls can fail on the same provider concurrently; without
        the guard each failure advances the index, skipping straight past
        the healthy provider (observed live: two simultaneous Gemini 429s
        hopped the pool back onto exhausted Groq). Single event loop and no
        awaits here, so check-then-set is atomic.
        """
        if self.current is failed:
            self._idx = (self._idx + 1) % len(self._providers)
            nxt = self.current
            log.warning("llm_provider_failover", to=nxt.base_url, model=nxt.model)
        return self.current

    def client(self, provider: Provider) -> AsyncOpenAI:
        return _client_for(provider.base_url, provider.api_key)


@lru_cache(maxsize=8)
def _client_for(base_url: str, api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        # SDK retries connection errors/5xx/429s with exponential backoff —
        # absorbs per-minute bursts; only sustained exhaustion reaches the
        # pool's failover.
        max_retries=settings.llm_max_retries,
    )


_pool: ProviderPool | None = None


def get_pool() -> ProviderPool:
    global _pool
    if _pool is None:
        providers = [Provider(settings.llm_base_url, settings.llm_api_key, settings.llm_model)]
        for fb in settings.llm_fallbacks:
            if not fb.api_key:
                log.warning("llm_fallback_skipped_no_key", base_url=fb.base_url)
                continue
            providers.append(Provider(fb.base_url, fb.api_key, fb.model))
        _pool = ProviderPool(providers)
        if len(providers) > 1:
            log.info("llm_failover_enabled", providers=[p.base_url for p in providers])
    return _pool


def reset_pool() -> None:
    """Testing hook: rebuild the pool from current settings on next use."""
    global _pool
    _pool = None


def current_provider() -> Provider:
    return get_pool().current


def rotate_current() -> Provider:
    """Force failover past the active provider (mid-stream failures: the
    initial request succeeded, so create_chat_completion couldn't rotate).
    Only safe where no concurrent LLM calls are in flight — synthesis runs
    sequentially after the search gather, so its caller qualifies."""
    pool = get_pool()
    return pool.advance_from(pool.current)


async def create_chat_completion(**kwargs):
    """chat.completions.create against the active provider, with failover.

    The active provider's model is injected (each provider names its own).
    Tries each provider at most once per call; if all are exhausted, the
    last RateLimitError propagates (→ sanitized run failure upstream).
    For streams, only the initial request can 429 — failover cannot rescue
    a stream that already started, which is fine: quota errors happen at
    request time.
    """
    pool = get_pool()
    last_exc: RateLimitError | None = None
    for _ in range(len(pool)):
        provider = pool.current
        try:
            return await pool.client(provider).chat.completions.create(
                model=provider.model, **kwargs
            )
        except RateLimitError as exc:
            log.warning(
                "llm_provider_exhausted",
                base_url=provider.base_url,
                model=provider.model,
                error=str(exc)[:200],
            )
            last_exc = exc
            pool.advance_from(provider)
    assert last_exc is not None
    raise last_exc
