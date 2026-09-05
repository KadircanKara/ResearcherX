"""Cohere `/v2/rerank`, over plain httpx.

No vendor SDK, for the reason `app/llm/client.py` uses the plain OpenAI SDK
against any compatible endpoint: this is one POST with four fields, and
Voyage and Jina answer the same shape, so a thin client is both smaller and
more portable than a dependency.

FAIL-OPEN IS THE CONTRACT. `rerank` returns `None` for every failure — no
key, HTTP error, timeout, malformed body — and never raises. The caller
then keeps its fused RRF order, so a reranker outage degrades a turn's
ORDERING and never fails the turn. A raising reranker would make an
optional quality stage into a hard dependency of answering at all.

Called with Cohere's defaults otherwise: `rerank-v3.5`, no
`max_tokens_per_doc` override, `top_n` set because the pipeline needs a
fixed depth (Cohere returns every document without it).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.logging import log

DEFAULT_MODEL = "rerank-v3.5"
DEFAULT_BASE_URL = "https://api.cohere.com/v2/rerank"
# One chunk is ~1,762 characters on the measured corpus. Cohere splits a
# document past roughly 500 tokens and bills each piece separately, so this
# caps the tail rather than paying for it — relevance is decided by the
# opening of a chunk far more often than by its end.
DEFAULT_MAX_DOC_CHARS = 2000
DEFAULT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class RerankResult:
    """`index` addresses the documents list the caller passed in."""

    index: int
    relevance_score: float


class CohereReranker:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_doc_chars: int = DEFAULT_MAX_DOC_CHARS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._max_doc_chars = max_doc_chars
        self._client = client

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankResult] | None:
        """Order `documents` by relevance to `query`. None means "no opinion"
        — the caller keeps the order it already had."""
        if not documents:
            return []
        if not self._api_key:
            log.warning("rerank_skipped_no_api_key")
            return None

        payload = {
            "model": self._model,
            "query": query,
            "documents": [d[: self._max_doc_chars] for d in documents],
            "top_n": min(top_n, len(documents)),
        }
        client = self._client or httpx.AsyncClient(timeout=self._timeout_s)
        try:
            response = await client.post(
                self._base_url,
                json=payload,
                headers={"authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            raw = response.json()["results"]
            results = [
                RerankResult(index=int(r["index"]), relevance_score=float(r["relevance_score"]))
                for r in raw
                # An index the caller cannot map is worse than a shorter
                # list: it would address the wrong candidate.
                if 0 <= int(r["index"]) < len(documents)
            ]
        except Exception as exc:
            log.warning("rerank_failed_open", error=f"{type(exc).__name__}: {str(exc)[:200]}")
            return None
        finally:
            if self._client is None:
                await client.aclose()

        # Sorted here rather than trusted: the pipeline's output order IS
        # this list, and a provider that returns unsorted results would
        # silently reorder the model's excerpt catalog.
        return sorted(results, key=lambda r: r.relevance_score, reverse=True)
