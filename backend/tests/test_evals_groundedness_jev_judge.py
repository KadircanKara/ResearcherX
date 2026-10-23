"""The Jev judge: class answers folded into the verdicts the metrics read."""

import httpx
import pytest

from evals.groundedness.jev_judge import (
    _CLAIM_GATE,
    _CONTEXT,
    _GRADE,
    _JOINT_SUPPORT,
    _PAIR_SUPPORT,
    _STANCE,
    JevJudge,
    is_jev_model,
)

EXCERPTS = [(1, "Paper A", "alpha"), (2, "Paper B", "beta"), (3, "Paper C", "gamma")]


def _judge(answer_for):
    """A JevJudge whose `_ask` answers from `answer_for(state, question)`."""
    judge = JevJudge(model="~typesafe/jev-latest", api_key="k", base_url="https://x/systemone")
    calls = []

    async def ask(state, question, usage):
        calls.append((state, question))
        return {"choice": answer_for(state, question), "confidence": 0.9}

    judge._ask = ask
    return judge, calls


def _pairs(by_excerpt, gate="claim", joint="unsupported"):
    def answer_for(state, question):
        if question is _CLAIM_GATE:
            return gate
        if question is _JOINT_SUPPORT:
            return joint
        assert question is _PAIR_SUPPORT
        for body, choice in by_excerpt.items():
            if state["excerpt"].endswith(body):
                return choice
        return "silent"

    return answer_for


async def _verdict(answer_for):
    judge, _ = _judge(answer_for)
    verdicts = await judge.judge_claims(question="q", claims=[(0, "claim")], excerpts=EXCERPTS)
    return verdicts[0]


async def test_a_sentence_asserting_nothing_is_no_claim_and_costs_no_pair_calls():
    judge, calls = _judge(_pairs({}, gate="no_claim"))
    verdicts = await judge.judge_claims(question="q", claims=[(0, "Here:")], excerpts=EXCERPTS)
    assert verdicts[0].verdict == "no_claim"
    assert len(calls) == 1


async def test_every_excerpt_that_supports_alone_is_a_supporting_excerpt():
    v = await _verdict(_pairs({"alpha": "supports", "gamma": "supports", "beta": "contradicts"}))
    assert v.verdict == "supported"
    assert v.supporting_excerpts == [1, 3]


async def test_partial_excerpts_that_carry_the_claim_together_support_it():
    v = await _verdict(
        _pairs({"alpha": "partially_supports", "beta": "partially_supports"}, joint="supported")
    )
    assert v.verdict == "supported"
    assert v.supporting_excerpts == [1, 2]


async def test_partial_support_that_never_adds_up_is_unsupported():
    v = await _verdict(_pairs({"alpha": "partially_supports"}, joint="unsupported"))
    assert v.verdict == "unsupported"
    assert v.supporting_excerpts == []


async def test_a_contradicting_excerpt_with_no_support_contradicts():
    v = await _verdict(_pairs({"beta": "contradicts"}))
    assert v.verdict == "contradicted"


async def test_silence_everywhere_is_unsupported():
    assert (await _verdict(_pairs({}))).verdict == "unsupported"


async def test_context_relevance_counts_answers_and_parts_only():
    classes = {"alpha": "answers", "beta": "supplies_part", "gamma": "same_topic_only"}

    def answer_for(state, question):
        assert question is _CONTEXT
        return next(c for body, c in classes.items() if state["excerpt"].endswith(body))

    judge, _ = _judge(answer_for)
    assert await judge.judge_context_relevance(question="q", excerpts=EXCERPTS) == {1, 2}


async def test_stance_is_the_choice():
    judge, _ = _judge(lambda state, question: "refused" if question is _STANCE else "?")
    assert (await judge.judge_stance(question="q", answer="no")).stance == "refused"


async def test_the_grade_sees_cited_excerpts_and_expected_facts():
    judge, calls = _judge(lambda state, question: "incomplete")
    grade = await judge.judge_answer_grade(
        question="q", answer="a [1]", cited_excerpts=EXCERPTS[:1], expected_facts=["20 drones"]
    )
    assert grade.grade == "incomplete"
    state, question = calls[0]
    assert question is _GRADE
    assert "alpha" in state["cited_excerpts"] and state["expected_facts"] == ["20 drones"]


async def test_an_answer_outside_the_options_raises_instead_of_guessing():
    def handler(request):
        return httpx.Response(200, json={"answers": {"q": {"choice": "maybe"}}})

    judge = JevJudge(model="m", api_key="k", base_url="https://x/systemone")
    judge._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    import asyncio

    judge._sem = asyncio.Semaphore(1)
    with pytest.raises(ValueError):
        await judge.judge_stance(question="q", answer="a")


def test_jev_models_are_recognised():
    assert is_jev_model("~typesafe/jev-latest")
    assert not is_jev_model("gpt-4.1-mini")
