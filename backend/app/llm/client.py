from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import settings


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    """Local Ollama via the OpenAI-compatible SDK.

    Ollama serves an OpenAI-compatible API at http://<host>:11434/v1 and
    ignores the api key (we pass a placeholder). Swap providers via env only:
    point `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` at any OpenAI-compatible
    endpoint (e.g. Groq). To go to a non-OpenAI SDK (Anthropic), rewrite this
    module around AsyncAnthropic — the agent/tool interfaces (pydantic in/out)
    don't change.
    """
    return AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        # SDK retries connection errors/5xx with exponential backoff — covers
        # transient blips while a local model loads into memory.
        max_retries=settings.llm_max_retries,
    )
