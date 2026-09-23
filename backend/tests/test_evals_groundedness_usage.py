"""The eval's token tally, read from the run's own spans."""

import json
from types import SimpleNamespace

from evals.groundedness.usage import format_usage, summarize


def _span(model=None, usage=None, reasoning=None):
    attrs = {}
    if model:
        attrs["langfuse.observation.model.name"] = model
    if usage is not None:
        attrs["langfuse.observation.usage_details"] = json.dumps(usage)
    if reasoning is not None:
        attrs["langfuse.observation.metadata.reasoning_tokens"] = reasoning
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
