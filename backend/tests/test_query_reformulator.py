"""QueryReformulatorAgent unit tests — LLM mocked."""

from unittest.mock import AsyncMock, patch

from app.agents.query_reformulator import (
    QueryReformulatorAgent,
    ReformulatedQuery,
    ReformulatorInput,
)

HISTORY = [
    {"role": "user", "content": "What is the TCT planner?"},
    {"role": "assistant", "content": "TCT is a cooperative UAV path planner."},
]


async def test_reformulator_returns_the_rewritten_query():
    agent = QueryReformulatorAgent()
    with patch(
        "app.agents.query_reformulator.parse_structured",
        new=AsyncMock(
            return_value=ReformulatedQuery(query="How does TCT compare to the RL planner?")
        ),
    ):
        got = await agent.run(
            ReformulatorInput(query="And against the RL one?", prior_messages=HISTORY)
        )
    assert got == "How does TCT compare to the RL planner?"


async def test_reformulator_fails_open_to_the_original_query():
    """A flaky rewrite must never be worse than no rewrite. The original
    question is always a usable retrieval query; an exception is not."""
    agent = QueryReformulatorAgent()
    with patch(
        "app.agents.query_reformulator.parse_structured",
        new=AsyncMock(side_effect=ValueError("llm down")),
    ):
        got = await agent.run(
            ReformulatorInput(query="And against the RL one?", prior_messages=HISTORY)
        )
    assert got == "And against the RL one?"


async def test_reformulator_ignores_an_empty_rewrite():
    """An empty string would embed to a meaningless vector and retrieve noise,
    so it is treated exactly like a failure."""
    agent = QueryReformulatorAgent()
    with patch(
        "app.agents.query_reformulator.parse_structured",
        new=AsyncMock(return_value=ReformulatedQuery(query="   ")),
    ):
        got = await agent.run(ReformulatorInput(query="original", prior_messages=HISTORY))
    assert got == "original"


async def test_reformulator_takes_its_budget_from_settings():
    """Provider-specific for the same reason as chat_answer_max_tokens: the
    output is one short string, but a reasoning model spends ~1,900 tokens
    thinking before emitting a character and would truncate the call."""
    from app.core.config import settings

    agent = QueryReformulatorAgent()
    parse = AsyncMock(return_value=ReformulatedQuery(query="q"))
    with (
        patch("app.agents.query_reformulator.parse_structured", new=parse),
        patch.object(settings, "query_reformulator_max_tokens", 1234),
    ):
        await agent.run(ReformulatorInput(query="q", prior_messages=HISTORY))

    assert parse.await_args.kwargs["max_tokens"] == 1234
