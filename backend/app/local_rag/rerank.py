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
A 429 / 5xx is retried first (`cohere_retries`, waits capped by
`cohere_max_retry_wait_s`); only the last attempt's failure fails open.

Called with Cohere's defaults otherwise: `rerank-v3.5`, no
`max_tokens_per_doc` override, `top_n` set because the pipeline needs a
fixed depth (Cohere returns every document without it).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from app.core import observability
from app.core.config import settings
from app.core.logging import log

DEFAULT_MODEL = "rerank-v3.5"
DEFAULT_BASE_URL = "https://api.cohere.com/v2/rerank"
# One chunk is ~1,762 characters on the measured corpus. Cohere splits a
# document past roughly 500 tokens and bills each piece separately, so this
# caps the tail rather than paying for it — relevance is decided by the
# opening of a chunk far more often than by its end.
DEFAULT_MAX_DOC_CHARS = 2000
DEFAULT_TIMEOUT_S = 5.0
_BACKOFF_S = 0.5
# Same set as the Jev client: rate limits and transient upstream errors.
# Anything else (401, 400, ...) will not change on a retry.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


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
        retries: int | None = None,
        max_retry_wait_s: float | None = None,
    ) -> None:
        self._api_key = api_key
        # None reads settings, so every construction site — chat, local_rag,
        # the eval harnesses — picks up COHERE_RETRIES without being edited.
        self._retries = settings.cohere_retries if retries is None else retries
        self._max_retry_wait_s = (
            settings.cohere_max_retry_wait_s if max_retry_wait_s is None else max_retry_wait_s
        )
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
        # A GENERATION so Langfuse can price it: usage is Cohere's own billed
        # search units, matched against a model definition for this model.
        span = observability.start_span(
            "cohere.rerank",
            kind="generation",
            attributes={
                "langfuse.observation.model.name": self._model,
                "gen_ai.request.model": self._model,
                "langfuse.observation.metadata.n_documents": len(documents),
                "langfuse.observation.metadata.top_n": payload["top_n"],
            },
        )
        client = self._client or httpx.AsyncClient(timeout=self._timeout_s)
        try:
            response = await self._post(client, payload)
            response.raise_for_status()
            body = response.json()
            raw = body["results"]
            results = [
                RerankResult(index=int(r["index"]), relevance_score=float(r["relevance_score"]))
                for r in raw
                # An index the caller cannot map is worse than a shorter
                # list: it would address the wrong candidate.
                if 0 <= int(r["index"]) < len(documents)
            ]
            _record_billed_units(span, body)
        except Exception as exc:
            log.warning("rerank_failed_open", error=f"{type(exc).__name__}: {str(exc)[:200]}")
            observability.mark_error(span, exc)
            # Failed open: the turn went on with the fused order.
            observability.set_attributes(span, {"langfuse.observation.level": "WARNING"})
            return None
        finally:
            if self._client is None:
                await client.aclose()
            span.end()

        # Sorted here rather than trusted: the pipeline's output order IS
        # this list, and a provider that returns unsorted results would
        # silently reorder the model's excerpt catalog.
        return sorted(results, key=lambda r: r.relevance_score, reverse=True)

    async def _post(self, client: httpx.AsyncClient, payload: dict) -> httpx.Response:
        """POST with retries. The last attempt's response (or exception) is
        returned (or raised) as-is, so `rerank`'s fail-open path still decides."""
        headers = {"authorization": f"Bearer {self._api_key}"}
        for attempt in range(self._retries + 1):
            last = attempt == self._retries
            try:
                response = await client.post(
                    self._base_url, json=payload, headers=headers, timeout=self._timeout_s
                )
            except httpx.TransportError:
                if last:
                    raise
                response = None
            if last or (response is not None and response.status_code not in _RETRY_STATUSES):
                return response  # type: ignore[return-value]
            retry_after = response.headers.get("retry-after", "") if response is not None else ""
            wait = float(retry_after) if retry_after.isdigit() else _BACKOFF_S * (2**attempt)
            log.info(
                "rerank_retry", attempt=attempt + 1, status=getattr(response, "status_code", None)
            )
            await asyncio.sleep(min(wait, self._max_retry_wait_s))
        raise RuntimeError("unreachable")


def _record_billed_units(span, body: object) -> None:
    try:
        units = body["meta"]["billed_units"]["search_units"]  # type: ignore[index]
    except Exception:
        return
    if isinstance(units, (int, float)):
        observability.set_json(
            span, "langfuse.observation.usage_details", {"search_units": int(units)}
        )
