"""Embedding service — Gemini text-embedding-004 via OpenAI-compat SDK."""

from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.logging import log


@lru_cache(maxsize=1)
def _embedding_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.resolved_embedding_api_key,
        base_url=settings.embedding_base_url,
        max_retries=3,
    )


class EmbeddingService:
    def __init__(self) -> None:
        self._client = _embedding_client()

    async def embed(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
        """Embed a single string. Use task_type='RETRIEVAL_QUERY' for queries."""
        results = await self.embed_batch([text], task_type=task_type)
        return results[0]

    async def embed_batch(
        self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT"
    ) -> list[list[float]]:
        """Embed a list of strings in one API call (Gemini supports batch input).

        Returns embeddings in the same order as input.
        Empty input returns immediately without an API call.
        """
        if not texts:
            return []
        try:
            create_kwargs: dict = {"model": settings.embedding_model, "input": texts}
            if settings.embedding_dimensions:
                create_kwargs["dimensions"] = settings.embedding_dimensions
            response = await self._client.embeddings.create(**create_kwargs)
        except Exception as exc:
            log.error("embedding_failed", error=str(exc)[:200], n_texts=len(texts))
            raise
        # Sort by index — the API may not guarantee order
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
