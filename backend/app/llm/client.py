from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import settings


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    """Groq via the OpenAI-compatible SDK.

    Swap providers via env only: point `LLM_BASE_URL`/`LLM_API_KEY`/
    `LLM_MODEL` at any OpenAI-compatible endpoint. To go to a non-OpenAI SDK,
    rewrite this module — the agent/tool interfaces (pydantic in/out) don't
    change.
    """
    return AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        # SDK retries connection errors/5xx/429s with exponential backoff —
        # the backoff budget for Groq free-tier rate limits.
        max_retries=settings.llm_max_retries,
    )
