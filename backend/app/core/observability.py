"""Tracing for the RAG pipeline: OpenTelemetry spans exported to Langfuse.

OFF UNLESS BOTH LANGFUSE KEYS ARE SET. With no keys, `tracer()` is the OTel
no-op tracer: every span below is a non-recording stub, attribute writes are
dropped, and the content capture (`set_json`) is skipped before it serialises
anything. Tests and any box without keys pay nothing.

Langfuse ingests OTLP over HTTP directly (`/api/public/otel`), so the Langfuse
SDK is not a dependency: what it would add is its attribute names, and those
are plain span attributes (`langfuse.observation.*`, `langfuse.trace.*`),
written here. The exporter is the stock OTLP/HTTP one with Basic auth.

The provider is PRIVATE to this module, never installed as the global OTel
provider. Only code that asks `tracer()` is traced, and a test can swap in an
in-memory exporter (`install_provider`) without fighting the global's
set-once rule. Parent/child still flows through the global context API, which
is per-task contextvars and independent of any provider.

FAIL-OPEN IS THE CONTRACT, as for the reranker: nothing here may fail a chat
turn. The SDK never raises from span operations, and every helper that builds
a value (JSON, usage objects the provider may not send) swallows its own
errors. An exporter outage drops spans in the background thread and logs.

WHAT IS SENT. With `LANGFUSE_CAPTURE_CONTENT` on (the default), prompts,
excerpts and answers leave the box for Langfuse Cloud: that is the point of
inspecting a trace, and it is also the user's paper text. Off, spans carry
timings, models, token counts and ids only.

Async generators: a span must never be CURRENT across a `yield`. The consumer
(sse-starlette) runs between yields, and a generator abandoned mid-stream is
finalised in another context, where detaching the token fails. So streaming
code creates its spans with `start_span` (not current) and makes one current
only around an await that does not yield: `under(span)`, `iterate_under`.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterable, AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from app.core.config import settings
from app.core.logging import log

_INSTRUMENTATION = "researcherx"

# One attribute past this is cut, with a marker. A chat answer's prompt is the
# whole excerpt catalog (~60 chunks of ~1.8k chars); the cap sits above that
# and exists for the pathological turn, so one span cannot make an export
# batch too large to ingest.
MAX_CONTENT_CHARS = 250_000

_provider: Any = None  # TracerProvider | None; typed loosely so the SDK stays lazy


def enabled() -> bool:
    return _provider is not None


def tracer() -> trace.Tracer:
    if _provider is None:
        return trace.NoOpTracer()
    return _provider.get_tracer(_INSTRUMENTATION)


def langfuse_otlp_endpoint(host: str) -> str:
    return host.rstrip("/") + "/api/public/otel/v1/traces"


def langfuse_headers(public_key: str, secret_key: str) -> dict[str, str]:
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        # Real-time ingestion on Langfuse's v4 data model.
        "x-langfuse-ingestion-version": "4",
    }


def keys_configured() -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def langfuse_span_processor() -> Any:
    """The batching OTLP exporter to Langfuse. Shared by the app and the eval
    harness, which installs its own provider but must ship spans the same way."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    exporter = OTLPSpanExporter(
        endpoint=langfuse_otlp_endpoint(settings.langfuse_host),
        headers=langfuse_headers(settings.langfuse_public_key, settings.langfuse_secret_key),
        timeout=10,
    )
    # Small batches: a chat answer's span can carry a ~100KB prompt, and the
    # default 512-span batch could make one request too large to ingest.
    return BatchSpanProcessor(exporter, max_export_batch_size=64)


def init_tracing() -> bool:
    """Build the exporter when both Langfuse keys are set. Idempotent."""
    global _provider
    if _provider is not None:
        return True
    if not keys_configured():
        log.info("tracing_disabled", reason="LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY unset")
        return False

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": "researcherx-backend",
                "deployment.environment": settings.environment,
            }
        )
    )
    provider.add_span_processor(langfuse_span_processor())
    _provider = provider
    log.info("tracing_enabled", host=settings.langfuse_host)
    return True


async def shutdown_tracing() -> None:
    """Flush queued spans and stop the export thread. Blocking I/O, so it runs
    off the event loop; the SDK bounds it with its own timeout."""
    global _provider
    provider, _provider = _provider, None
    if provider is None:
        return
    try:
        await asyncio.to_thread(provider.shutdown)
    except Exception as exc:
        log.warning("tracing_shutdown_failed", error=str(exc)[:200])


def install_provider(provider: Any) -> None:
    """Swap the provider outright. Tests only (in-memory exporter)."""
    global _provider
    _provider = provider


def capture_content() -> bool:
    return settings.langfuse_capture_content


# ── spans ────────────────────────────────────────────────────────────────


def _base_attributes(kind: str, attributes: dict[str, Any] | None) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "langfuse.observation.type": kind,
        "langfuse.environment": settings.environment,
    }
    for key, value in (attributes or {}).items():
        if value is not None:
            attrs[key] = value
    return attrs


def start_span(
    name: str,
    *,
    kind: str = "span",
    attributes: dict[str, Any] | None = None,
    root: bool = False,
) -> Span:
    """A span that is NOT made current; the caller must `end()` it. The parent
    is the current span, or none when `root` (a new trace)."""
    ctx = otel_context.Context() if root else None
    return tracer().start_span(name, context=ctx, attributes=_base_attributes(kind, attributes))


@contextmanager
def span(
    name: str,
    *,
    kind: str = "span",
    attributes: dict[str, Any] | None = None,
    parent: Span | None = None,
) -> Iterator[Span]:
    """A current span for a block that does not yield. `parent` pins the
    parent explicitly, for code inside a generator that keeps its root span
    out of the current context (see the module docstring)."""
    ctx = trace.set_span_in_context(parent) if parent is not None else None
    with tracer().start_as_current_span(
        name, context=ctx, attributes=_base_attributes(kind, attributes)
    ) as s:
        try:
            yield s
        except BaseException as exc:
            # The SDK records the exception and the ERROR status itself.
            mark_error(s, exc, record=False)
            raise


@contextmanager
def under(parent: Span) -> Iterator[None]:
    """Make `parent` current for a block that does not yield, so spans the
    block's callees open (embedding, LLM, rerank) nest under it."""
    token = otel_context.attach(trace.set_span_in_context(parent))
    try:
        yield
    finally:
        otel_context.detach(token)


async def iterate_under(source: AsyncIterable, parent: Span) -> AsyncIterator:
    """Iterate `source` with `parent` current while each item is PRODUCED,
    and not while the caller holds it. Attach and detach never straddle a
    yield, which is what makes this safe inside an async generator."""
    it = source.__aiter__()
    while True:
        token = otel_context.attach(trace.set_span_in_context(parent))
        try:
            item = await it.__anext__()
        except StopAsyncIteration:
            return
        finally:
            otel_context.detach(token)
        yield item


def set_attributes(s: Span, attributes: dict[str, Any]) -> None:
    if not s.is_recording():
        return
    for key, value in attributes.items():
        if value is not None:
            s.set_attribute(key, value)


def set_json(s: Span, key: str, value: Any) -> None:
    """A JSON-valued attribute (usage, parameters, content). Serialised only
    when the span records, and never raises: a value that will not
    serialise costs its attribute, never the turn."""
    if not s.is_recording():
        return
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except Exception:
        return
    if len(text) > MAX_CONTENT_CHARS:
        text = text[:MAX_CONTENT_CHARS] + f"…[truncated {len(text) - MAX_CONTENT_CHARS} chars]"
    s.set_attribute(key, text)


def set_content(s: Span, key: str, value: Any) -> None:
    """Like set_json, but only when content capture is on."""
    if capture_content():
        set_json(s, key, value)


def set_metadata(s: Span, **values: Any) -> None:
    set_attributes(s, {f"langfuse.observation.metadata.{k}": v for k, v in values.items()})


def mark_error(s: Span, exc: BaseException, *, record: bool = True) -> None:
    """Record a failure on a span the caller will end. A cancellation (the
    reader went away) is a warning, not an error: nothing failed."""
    if not s.is_recording():
        return
    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        set_attributes(
            s,
            {
                "langfuse.observation.level": "WARNING",
                "langfuse.observation.status_message": "cancelled by the consumer",
            },
        )
        return
    if record:
        s.record_exception(exc)
        s.set_status(Status(StatusCode.ERROR, type(exc).__name__))
    set_attributes(
        s,
        {
            "langfuse.observation.level": "ERROR",
            # The type only: exception text can carry provider response bodies.
            "langfuse.observation.status_message": type(exc).__name__,
        },
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def reasoning_tokens(usage: Any) -> int | None:
    """Hidden thinking tokens, when the provider reports them. Already inside
    `completion_tokens` (billed as output), so this is METADATA, never a
    usage key: a usage key would be priced a second time."""
    try:
        value = usage.completion_tokens_details.reasoning_tokens
    except Exception:
        return None
    return value if isinstance(value, int) else None


def usage_details(usage: Any) -> dict[str, int] | None:
    """OpenAI-shaped usage into Langfuse's keys, or None when absent."""
    if usage is None:
        return None
    try:
        details = {
            "input": getattr(usage, "prompt_tokens", None),
            "output": getattr(usage, "completion_tokens", None),
            "total": getattr(usage, "total_tokens", None),
        }
        return {k: int(v) for k, v in details.items() if isinstance(v, int)} or None
    except Exception:
        return None
