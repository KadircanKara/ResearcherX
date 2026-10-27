"""Live paper summaries through OpenRouter, with a model fallback chain.

A SEPARATE client, never `ProviderPool` / `create_chat_completion`: the pool
injects the chat model and rotates on quota, so a summary would silently be
written by whichever chat provider happened to be active.

The chain goes out as OpenRouter's `models` list, so an HTTP-level failure
falls through server-side. A 200 does not: DeepSeek can answer OK with JSON
that fails the schema, or stop on `length`. Either is UNUSABLE, and the
client retries with the chain cut to the models after the one that served.

`generate_summary` never raises (a cancellation still propagates) and fails
open to None, never to an invented record: the caller then falls back to the
abstract.
"""

from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.core import observability
from app.core.config import settings
from app.core.logging import log
from app.services.paper_summary_prompt import (
    SummaryRecord,
    messages,
    parse,
    response_format,
)

# Keyed by the credentials it was built with, so a changed key or base URL
# never reuses a client holding the old ones.
_client: tuple[tuple[str, str], AsyncOpenAI] | None = None
# OpenRouter already falls back between models server-side; SDK retries only
# cover a transient transport error, and each one can wait out the timeout.
_MAX_RETRIES = 2


@dataclass(frozen=True)
class GeneratedSummary:
    record: SummaryRecord
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


def _get_client() -> AsyncOpenAI:
    """Built on first use, so an empty key or a test never constructs one."""
    global _client
    key = (settings.summary_base_url, settings.summary_api_key)
    if _client is None or _client[0] != key:
        _client = (
            key,
            AsyncOpenAI(
                base_url=settings.summary_base_url,
                api_key=settings.summary_api_key,
                timeout=settings.paper_summary_timeout_s,
                max_retries=_MAX_RETRIES,
            ),
        )
    return _client[1]


def reset_client() -> None:
    """Drop the cached client."""
    global _client
    _client = None


async def generate_summary(title: str, abstract: str | None, text: str) -> GeneratedSummary | None:
    if not settings.summary_api_key:
        return None
    chain = list(settings.summary_models)
    if not chain:
        log.warning("paper_summary_no_models")
        return None
    try:
        msgs = messages(title, abstract, text)
        while chain:
            response = await _request(chain, msgs)
            served = getattr(response, "model", None) or ""
            result = _usable(response, served or chain[0])
            if result is not None:
                return result
            log.info(
                "paper_summary_unusable",
                model=served,
                finish_reason=_finish_reason(response),
            )
            chain = chain[chain.index(served) + 1 :] if served in chain else chain[1:]
        log.warning("paper_summary_failed", error="no model in the chain gave a usable answer")
        return None
    except Exception as exc:
        log.warning("paper_summary_failed", error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return None


async def _request(chain: list[str], msgs: list[dict]) -> Any:
    """One HTTP call, one generation span."""
    with observability.span(
        "paper.summary",
        kind="generation",
        attributes={
            "gen_ai.request.model": chain[0],
            "langfuse.observation.model.name": chain[0],
        },
    ) as gen:
        observability.set_metadata(gen, models=",".join(chain))
        observability.set_content(gen, "langfuse.observation.input", msgs)
        response = await _get_client().chat.completions.create(
            model=chain[0],
            messages=msgs,
            response_format=response_format(),
            max_tokens=settings.paper_summary_max_tokens,
            extra_body={
                "models": chain,
                "reasoning": {"enabled": False},
                "provider": {"require_parameters": True},
                "usage": {"include": True},
            },
        )
        _record(gen, response)
        return response


def _record(gen, response: Any) -> None:
    """Telemetry only: it must never fail a call whose answer is valid."""
    try:
        served = getattr(response, "model", None)
        if served:
            observability.set_attributes(gen, {"langfuse.observation.model.name": served})
        observability.set_content(gen, "langfuse.observation.output", _content(response))
        usage = getattr(response, "usage", None)
        details = observability.usage_details(usage)
        if details:
            observability.set_json(gen, "langfuse.observation.usage_details", details)
        cost = _cost(usage)
        if cost is not None:
            observability.set_json(gen, "langfuse.observation.cost_details", {"total": cost})
        observability.set_metadata(gen, finish_reason=_finish_reason(response))
    except Exception as exc:
        log.debug("paper_summary_usage_unparsed", error=f"{type(exc).__name__}: {str(exc)[:200]}")


def _usable(response: Any, model: str) -> GeneratedSummary | None:
    """A record only from a complete, schema-valid, non-empty choice."""
    if not getattr(response, "choices", None):
        return None
    if _finish_reason(response) == "length":
        return None
    record = parse(_content(response), max_keywords=settings.paper_summary_max_keywords)
    if record is None:
        return None
    usage = getattr(response, "usage", None)
    return GeneratedSummary(
        record=record,
        model=model,
        input_tokens=_int(getattr(usage, "prompt_tokens", None)),
        output_tokens=_int(getattr(usage, "completion_tokens", None)),
        cost_usd=_cost(usage),
    )


def _content(response: Any) -> str | None:
    try:
        content = response.choices[0].message.content
    except Exception:
        return None
    return content if isinstance(content, str) else None


def _finish_reason(response: Any) -> str | None:
    try:
        return response.choices[0].finish_reason
    except Exception:
        return None


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _cost(usage: Any) -> float | None:
    """OpenRouter's `usage.cost` (sent with `usage: {include: true}`), or None.
    The SDK keeps it as an extra field, so read the attribute, then model_extra."""
    if usage is None:
        return None
    value = getattr(usage, "cost", None)
    if value is None:
        extra = getattr(usage, "model_extra", None)
        if isinstance(extra, dict):
            value = extra.get("cost")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
