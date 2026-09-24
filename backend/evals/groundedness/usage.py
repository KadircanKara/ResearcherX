"""Token usage of a run, by model, read off the run's own trace spans.

The harness installs an in-memory tracer (`install_tally`) before any model
call. Every paid call it makes already opens a Langfuse-shaped span --
answer generations through `create_chat_completion`, embeddings, Cohere
rerank, and the judge -- each carrying `langfuse.observation.model.name` and
`langfuse.observation.usage_details`. Summing those is the run's measured
bill in tokens, with no second accounting path to drift from production's.

Content capture is switched off for the tally: the spans live in memory for
the whole run, and a captured catalog is ~100KB a call. A run exported to
Langfuse (`--langfuse`) keeps the configured capture setting instead, because
inspecting what the model and the judge were shown is the point of the export;
about 20MB for a full run.

Tokens only, never dollars: prices change and live outside the code. The
report prints the counts; whoever reads it prices them.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence

from app.core import observability
from app.core.config import settings

MODEL = "langfuse.observation.model.name"
USAGE = "langfuse.observation.usage_details"
REASONING = "langfuse.observation.metadata.reasoning_tokens"


def install_tally(*, processors: Sequence = (), capture_content: bool = False):
    """In-memory span exporter as the tracer; returns it for `summarize`.

    `processors` are added ahead of the tally -- the Langfuse export passes its
    attribute stamper and its OTLP exporter here, so one provider serves both.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    settings.langfuse_capture_content = capture_content
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    for processor in processors:
        provider.add_span_processor(processor)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observability.install_provider(provider)
    return exporter


def summarize(spans: Iterable) -> dict[str, dict[str, int]]:
    """{model: {"calls": n, "input": ..., "output": ..., ...}} over every span
    that names a model. A call with no usage still counts as a call, so a
    provider that stops reporting usage shows up as calls without tokens."""
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for span in spans:
        attrs = span.attributes or {}
        model = attrs.get(MODEL)
        if not model:
            continue
        row = totals[str(model)]
        row["calls"] += 1
        raw = attrs.get(USAGE)
        if raw:
            try:
                usage = json.loads(raw)
            except ValueError:
                usage = {}
            for key, value in usage.items():
                if isinstance(value, int):
                    row[key] += value
        reasoning = attrs.get(REASONING)
        if isinstance(reasoning, int):
            row["reasoning"] += reasoning
    return {model: dict(row) for model, row in sorted(totals.items())}


def format_usage(summary: dict[str, dict[str, int]]) -> list[str]:
    lines = ["TOKEN USAGE (this run, measured from each provider's reported usage)"]
    if not summary:
        return lines + ["  (no model calls recorded)"]
    lines.append(
        f"  {'model':<24}{'calls':>7}{'input':>12}{'output':>10}{'reasoning':>11}{'other':>16}"
    )
    for model, row in summary.items():
        other = ", ".join(
            f"{k}={v}"
            for k, v in row.items()
            if k not in {"calls", "input", "output", "total", "reasoning"}
        )
        lines.append(
            f"  {model:<24}{row.get('calls', 0):>7}{row.get('input', 0):>12}"
            f"{row.get('output', 0):>10}{row.get('reasoning', 0):>11}  {other}"
        )
    lines.append(
        "  reasoning is already inside output (billed as output); "
        "a model with calls but no tokens did not report usage"
    )
    return lines
