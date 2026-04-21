from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import settings


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    """Groq via the OpenAI-compatible SDK.

    Swap providers: change `groq_api_key`/`groq_base_url`/`llm_model` in
    config.py (and the corresponding env vars). To go back to Anthropic,
    rewrite this module around AsyncAnthropic — the agent/tool interfaces
    (pydantic in/out) don't change.
    """
    return AsyncOpenAI(
        api_key=settings.groq_api_key,
        base_url=settings.groq_base_url,
        # SDK retries 429/5xx with exponential backoff.
        max_retries=5,
    )
