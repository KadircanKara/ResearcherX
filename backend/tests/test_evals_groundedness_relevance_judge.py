"""The relevance judge calls, with the transport faked.

`Judge._structured` is the judge's only contact with a model, so replacing it
exercises everything above it -- slicing, merging, the refusal to fail open --
without a network.
"""

import pytest

from evals.groundedness.judge import (
    AnswerRelevance,
    ContextRelevanceResponse,
    ExcerptRelevance,
    Judge,
)


class FakeTransport:
    """Stands in for `Judge._structured`, recording every request."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    async def __call__(self, *, system, user, model_cls):
        self.calls.append({"system": system, "user": user, "model_cls": model_cls})
        return self.reply(user)


def _numbers_in(user: str) -> list[int]:
    import re

    block = user.split("EXCERPTS\n", 1)[1]
    return [int(n) for n in re.findall(r"^\[(\d+)\] \(from:", block, flags=re.M)]


def _judge(transport, **kwargs) -> Judge:
    judge = Judge(model="sonnet", **kwargs)
    judge._structured = transport
    return judge


async def test_context_relevance_returns_the_excerpt_numbers_judged_relevant():
    transport = FakeTransport(
        lambda user: ContextRelevanceResponse(
            verdicts=[ExcerptRelevance(n=n, relevant=n % 2 == 1) for n in _numbers_in(user)]
        )
    )
    relevant = await _judge(transport).judge_context_relevance(
        question="q", excerpts=[(1, "A", "x"), (2, "A", "y"), (3, "B", "z")]
    )
    assert relevant == frozenset({1, 3})


async def test_the_context_judge_is_never_shown_the_answer():
    """Context relevance is a property of RETRIEVAL. A judge that can see the
    answer marks an excerpt relevant because the answer used it, which turns
    the column into a second, worse citation metric."""
    transport = FakeTransport(
        lambda user: ContextRelevanceResponse(
            verdicts=[ExcerptRelevance(n=n, relevant=True) for n in _numbers_in(user)]
        )
    )
    await _judge(transport).judge_context_relevance(question="q", excerpts=[(1, "A", "x")])
    assert "ANSWER" not in transport.calls[0]["user"]


async def test_a_large_catalog_is_sliced_and_every_slice_is_judged():
    """Slicing is not sampling: no excerpt may be left unjudged, or precision
    is computed over a catalog the model was not shown."""
    transport = FakeTransport(
        lambda user: ContextRelevanceResponse(
            verdicts=[ExcerptRelevance(n=n, relevant=n == 3) for n in _numbers_in(user)]
        )
    )
    excerpts = [(n, "A", "x" * 40) for n in (1, 2, 3, 4)]
    relevant = await _judge(transport, excerpt_chars_per_request=100).judge_context_relevance(
        question="q", excerpts=excerpts
    )
    assert len(transport.calls) == 2
    assert relevant == frozenset({3})


async def test_an_excerpt_the_judge_skipped_is_an_error_not_an_irrelevant_excerpt():
    """Defaulting a missing verdict to "not relevant" would report a precision
    the judge never measured -- the same reason `judge_claims` refuses to
    default a missing claim to "supported"."""
    transport = FakeTransport(
        lambda user: ContextRelevanceResponse(verdicts=[ExcerptRelevance(n=1, relevant=True)])
    )
    with pytest.raises(ValueError, match=r"\[2\]"):
        await _judge(transport).judge_context_relevance(
            question="q", excerpts=[(1, "A", "x"), (2, "A", "y")]
        )


async def test_a_verdict_for_an_excerpt_that_was_never_shown_is_ignored():
    transport = FakeTransport(
        lambda user: ContextRelevanceResponse(
            verdicts=[ExcerptRelevance(n=1, relevant=True), ExcerptRelevance(n=99, relevant=True)]
        )
    )
    relevant = await _judge(transport).judge_context_relevance(
        question="q", excerpts=[(1, "A", "x")]
    )
    assert relevant == frozenset({1})


async def test_an_empty_catalog_costs_no_call():
    transport = FakeTransport(lambda user: pytest.fail("no call expected"))
    assert await _judge(transport).judge_context_relevance(question="q", excerpts=[]) == frozenset()
    assert transport.calls == []


async def test_answer_relevance_is_asked_with_the_question_and_answer_only():
    """No excerpts: whether the answer is SUPPORTED is the support judge's
    question. Showing them here lets a well-grounded non-answer score high."""
    transport = FakeTransport(lambda user: AnswerRelevance(score=0.9, reason="direct"))
    result = await _judge(transport).judge_answer_relevance(question="Why?", answer="Because.")
    assert result.score == 0.9
    assert "Why?" in transport.calls[0]["user"]
    assert "Because." in transport.calls[0]["user"]
    assert "EXCERPTS" not in transport.calls[0]["user"]


def test_a_relevance_score_outside_zero_to_one_is_a_malformed_response():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AnswerRelevance(score=1.4, reason="")


# -- tolerant JSON parsing --------------------------------------------------
#
# The judge's endpoint is not guaranteed to have a real JSON mode. Through
# `tools/claude-proxy`, `response_format` is emulated by a line appended to the
# system prompt, so the model sometimes answers with the object and then keeps
# talking. Measured on the 51-case run of 2026-09-20: two of fifty cases died
# with `Invalid JSON: trailing characters at line 3 column 1`, losing those
# cases their context-relevance column for a sentence of prose after the
# closing brace.


class FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1

        class _Message:
            content = self.content

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        return _Response()


def _judge_with_content(content: str) -> tuple[Judge, FakeCompletions]:
    judge = Judge(model="sonnet")
    completions = FakeCompletions(content)
    judge._client.chat.completions = completions
    return judge, completions


async def test_prose_after_the_closing_brace_is_tolerated():
    """The exact shape that failed live: a valid object, then a sentence."""
    judge, _ = _judge_with_content(
        '{"verdicts": [{"n": 1, "relevant": true}]}\n\n'
        "I have judged every excerpt, keyed by number as given."
    )
    relevant = await judge.judge_context_relevance(question="q", excerpts=[(1, "A", "x")])
    assert relevant == frozenset({1})


async def test_a_fenced_object_is_tolerated():
    judge, _ = _judge_with_content('```json\n{"score": 0.5, "reason": "ok"}\n```')
    assert (await judge.judge_answer_relevance(question="q", answer="a")).score == 0.5


async def test_a_reasoning_block_before_the_object_is_tolerated():
    judge, _ = _judge_with_content(
        '<think>weighing this</think>{"stance": "refused", "reason": ""}'
    )
    assert (await judge.judge_stance(question="q", answer="a")).stance == "refused"


async def test_the_raw_content_is_still_preferred_when_it_parses():
    """Extraction is a FALLBACK, not a rewrite: a clean response must not be
    reshaped by the brace-slicer on its way through."""
    judge, completions = _judge_with_content('{"score": 1.0, "reason": "a {brace} inside"}')
    result = await judge.judge_answer_relevance(question="q", answer="a")
    assert result.reason == "a {brace} inside"
    assert completions.calls == 1


async def test_content_with_no_object_at_all_still_raises():
    """A judge that answered in prose has not judged. Salvaging nothing from it
    is correct -- the case becomes an error row and leaves the denominator."""
    judge, _ = _judge_with_content("I cannot grade these excerpts.")
    with pytest.raises(Exception):
        await judge.judge_stance(question="q", answer="a")
