"""The eval's token tally, read from the run's own spans."""

import json
from types import SimpleNamespace

from evals.groundedness.usage import format_usage, summarize


def _span(model=None, usage=None, reasoning=None, cached=None):
    attrs = {}
    if model:
        attrs["langfuse.observation.model.name"] = model
    if usage is not None:
        attrs["langfuse.observation.usage_details"] = json.dumps(usage)
    if reasoning is not None:
        attrs["langfuse.observation.metadata.reasoning_tokens"] = reasoning
    if cached is not None:
        attrs["langfuse.observation.metadata.cached_tokens"] = cached
    return SimpleNamespace(attributes=attrs)


def test_usage_is_summed_per_model_and_calls_without_usage_still_count():
    summary = summarize(
        [
            _span("gpt-5-mini", {"input": 100, "output": 20, "total": 120}, reasoning=8),
            _span("gpt-5-mini", {"input": 50, "output": 5, "total": 55}, reasoning=2),
            _span("gpt-4.1-mini", None),
            _span("rerank-v3.5", {"search_units": 1}),
            _span(None, {"input": 999}),  # a plain span: not a model call
        ]
    )
    assert summary["gpt-5-mini"] == {
        "calls": 2,
        "input": 150,
        "output": 25,
        "total": 175,
        "reasoning": 10,
    }
    assert summary["gpt-4.1-mini"] == {"calls": 1}
    assert summary["rerank-v3.5"] == {"calls": 1, "search_units": 1}
    assert len(summary) == 3


def test_the_report_names_every_model_and_non_token_units():
    lines = format_usage(summarize([_span("rerank-v3.5", {"search_units": 3})]))
    assert any("rerank-v3.5" in line and "search_units=3" in line for line in lines)


def test_cached_input_is_tallied_and_printed_beside_input():
    summary = summarize(
        [
            _span("gpt-5-mini", {"input": 60000, "output": 300}, cached=30000),
            _span("gpt-5-mini", {"input": 61000, "output": 280}, cached=30000),
            _span("gpt-5-mini", {"input": 59000, "output": 310}),  # cache miss: no field
        ]
    )
    assert summary["gpt-5-mini"]["cached"] == 60000
    assert summary["gpt-5-mini"]["input"] == 180000  # cached stays inside input
    row = next(line for line in format_usage(summary) if "gpt-5-mini" in line)
    assert "180000" in row and "60000" in row
    assert "cached=" not in row  # its own column, not an "other" unit


def test_cached_tokens_reads_openai_prompt_details_and_tolerates_absence():
    from app.core.observability import cached_tokens

    usage = SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=1024))
    assert cached_tokens(usage) == 1024
    assert cached_tokens(SimpleNamespace(prompt_tokens_details=None)) is None
    assert cached_tokens(None) is None
