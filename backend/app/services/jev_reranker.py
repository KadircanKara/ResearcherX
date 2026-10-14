"""TypeSafe Jev as a reranker, through OpenRouter's /systemone endpoint.

Jev is a decision model, not a text generator: it answers typed questions
about a `state` with calibrated probabilities. OpenRouter serves it on the
TypeSafe-compatible endpoint and REJECTS it on /chat/completions (400, "is a
decisions model"), so this is a plain httpx client like `CohereReranker`,
not a caller of `app/llm/client.py`.

POINTWISE: one request per (question, document). The docs offer no
multi-passage request, and a listwise `choice` would break the 32k state
limit at 100 chunks, force probabilities to sum to 1 and inherit a
documented first-option bias.

FAIL-OPEN IS THE CONTRACT, AS A WHOLE. Any request that still fails after
its retries, or any malformed answer, makes `rerank` return None and the
caller keeps its incoming order. Partial scores are never returned:
`apply_rerank` places unscored items at the tail, which would demote
exactly the chunks whose call failed.

The semaphore is created per call, never at module level: an
asyncio.Semaphore binds to the first event loop that contends it.
"""

from __future__ import annotations

import asyncio
import math
from typing import Literal

import httpx

from app.core import observability
from app.core.config import settings
from app.core.logging import log
from app.local_rag.rerank import RerankResult

Question = Literal["noul", "score"]

DEFAULT_MAX_DOC_CHARS = 2000  # the cap Cohere gets: same input, model isolated
_RETRIES = 2
_BACKOFF_S = 0.5
_MAX_RETRY_AFTER_S = 2.0  # a server-chosen wait must not stall a chat turn
# 499: OpenRouter answers it intermittently under sustained load (5 times in
# one six-arm eval run, 2026-10-15); a retry is the difference between one question
# failing open to RRF order and a real measurement.
_RETRY_STATUSES = frozenset({429, 499, 529, 500, 502, 503, 504})

_NOUL = {
    "type": "noul",
    "instructions": "Does `passage` contain information that helps answer `question`?",
    "criteria": {
        "true": (
            "`passage` states a fact, method, result, definition or comparison "
            "that an answer to `question` would use."
        ),
        "false": "`passage` is only on the same topic as `question`, or does not address it.",
    },
}
_SCORE_LEVELS = [
    "`passage` is unrelated to `question`.",
    "`passage` is on the same topic but does not help answer `question`.",
    "`passage` partly helps answer `question`.",
    "`passage` directly answers `question` or supplies evidence an answer needs.",
]
_SCORE = {
    "type": "score",
    "instructions": "How much does `passage` help answer `question`?",
    "criteria": _SCORE_LEVELS,
}


def build_payload(model: str, question: Question, query: str, document: str) -> dict:
    return {
        "model": model,
        "state": {"question": query, "passage": document},
        "questions": {"relevant": _NOUL if question == "noul" else _SCORE},
    }


def parse_relevance(body: dict, question: Question) -> float:
    """Relevance in [0, 1]. Raises ValueError on anything malformed."""
    try:
        answer = body["answers"]["relevant"]
    except (KeyError, TypeError) as exc:
        raise ValueError("no answers.relevant") from exc
    if not isinstance(answer, dict) or answer.get("type") != question:
        raise ValueError(f"expected a {question} answer")
    raw = answer.get("noul" if question == "noul" else "score")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise ValueError("non-numeric answer")
    value = float(raw) if question == "noul" else float(raw) / (len(_SCORE_LEVELS) - 1)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"answer out of range: {raw}")
    return value


class JevReranker:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        question: Question,
        concurrency: int,
        timeout_s: float,
        deadline_s: float = 60.0,
        max_doc_chars: int = DEFAULT_MAX_DOC_CHARS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self.question: Question = question
        self._concurrency = concurrency
        self._timeout_s = timeout_s
        self._deadline_s = deadline_s
        self._max_doc_chars = max_doc_chars
        self._client = client

    async def _one(
        self, client: httpx.AsyncClient, sem: asyncio.Semaphore, query: str, doc: str
    ) -> tuple[float, dict]:
        payload = build_payload(self._model, self.question, query, doc[: self._max_doc_chars])
        headers = {"authorization": f"Bearer {self._api_key}"}
        for attempt in range(_RETRIES + 1):
            async with sem:
                try:
                    resp = await client.post(
                        self._base_url, json=payload, headers=headers, timeout=self._timeout_s
                    )
                except httpx.TransportError:
                    if attempt == _RETRIES:
                        raise
                    resp = None
            if resp is not None and resp.status_code not in _RETRY_STATUSES:
                resp.raise_for_status()
                body = resp.json()
                return parse_relevance(body, self.question), body
            if attempt == _RETRIES:
                resp.raise_for_status()
            retry_after = resp.headers.get("retry-after", "") if resp is not None else ""
            await asyncio.sleep(
                min(float(retry_after), _MAX_RETRY_AFTER_S)
                if retry_after.isdigit()
                else _BACKOFF_S * (2**attempt)
            )
        raise RuntimeError("unreachable")

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankResult] | None:
        """Order `documents` by Jev's relevance answer. None = no opinion."""
        if not documents:
            return []
        if not self._api_key:
            log.warning("jev_rerank_skipped_no_api_key")
            return None

        span = observability.start_span(
            "jev.rerank",
            kind="generation",
            attributes={
                "langfuse.observation.model.name": self._model,
                "gen_ai.request.model": self._model,
                "langfuse.observation.metadata.n_documents": len(documents),
                "langfuse.observation.metadata.question": self.question,
            },
        )
        client = self._client or httpx.AsyncClient(timeout=self._timeout_s)
        sem = asyncio.Semaphore(self._concurrency)
        try:
            scored: list[tuple[float, dict] | None] = [None] * len(documents)

            async def run(i: int, doc: str) -> None:
                scored[i] = await self._one(client, sem, query, doc)

            failures: list[BaseException] = []
            deadline = asyncio.timeout(self._deadline_s)
            try:
                async with deadline:
                    # The first terminal failure cancels every sibling: the
                    # call fails open as a whole, so further paid requests
                    # could not change the outcome.
                    async with asyncio.TaskGroup() as tg:
                        for i, d in enumerate(documents):
                            tg.create_task(run(i, d))
            except* Exception as group:
                failures = list(group.exceptions)
            if deadline.expired():
                failures = [TimeoutError("deadline")]
            _record_usage(span, [o for o in scored if o is not None])
            observability.set_metadata(span, failures=len(failures))
            if failures:
                first = failures[0]
                log.warning(
                    "jev_rerank_failed_open",
                    failures=len(failures),
                    n_documents=len(documents),
                    reason="deadline" if deadline.expired() else "request_failed",
                    error=f"{type(first).__name__}: {str(first)[:200]}",
                )
                observability.mark_error(span, first)
                observability.set_attributes(span, {"langfuse.observation.level": "WARNING"})
                return None
            results = [
                RerankResult(index=i, relevance_score=score)
                for i, (score, _) in enumerate(scored)  # type: ignore[misc]
            ]
        finally:
            if self._client is None:
                await client.aclose()
            span.end()
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results[: max(0, min(top_n, len(results)))]


def _record_usage(span, successes: list[tuple[float, dict]]) -> None:
    """Telemetry only: it must never fail a call whose scores are valid."""
    try:
        tokens_in = tokens_out = 0
        cost = 0.0
        resolved = None
        for _, body in successes:
            usage = body.get("usage") or {}
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
            cost += float(usage.get("cost") or 0.0)
            resolved = resolved or body.get("model")
        observability.set_json(
            span, "langfuse.observation.usage_details", {"input": tokens_in, "output": tokens_out}
        )
        observability.set_json(span, "langfuse.observation.cost_details", {"total": cost})
        if resolved:
            observability.set_metadata(span, resolved_model=resolved)
    except Exception as exc:
        log.debug("jev_usage_unparsed", error=f"{type(exc).__name__}: {str(exc)[:200]}")


def build_jev_reranker() -> JevReranker | None:
    """Jev from settings, or None when no key is configured."""
    if not settings.decisions_api_key:
        log.warning("jev_disabled_no_api_key")
        return None
    return JevReranker(
        api_key=settings.decisions_api_key,
        model=settings.jev_model,
        base_url=settings.decisions_base_url,
        question=settings.jev_question,
        concurrency=settings.jev_concurrency,
        timeout_s=settings.jev_timeout_s,
        deadline_s=settings.jev_deadline_s,
    )
