"""ScopeWidenerAgent — may only widen, and fails open to the mention."""

from unittest.mock import AsyncMock, patch

from app.agents.scope_widener import ScopeWidenerAgent, WidenerInput, WidenDecision


async def test_returns_true_when_the_model_says_widen():
    agent = ScopeWidenerAgent()
    with patch(
        "app.agents.scope_widener.parse_structured",
        new=AsyncMock(return_value=WidenDecision(widen=True)),
    ):
        assert await agent.run(WidenerInput(query="do others use it?", mentioned_titles=["A"]))


async def test_a_failed_call_falls_open_to_not_widening():
    """Fail-open direction is 'answer from what the user pointed at'. An LLM
    that could narrow scope would reintroduce the guessing this replaces; one
    that widens on error would silently ignore an explicit instruction."""
    agent = ScopeWidenerAgent()
    with patch(
        "app.agents.scope_widener.parse_structured",
        new=AsyncMock(side_effect=RuntimeError("provider down")),
    ):
        assert await agent.run(WidenerInput(query="q", mentioned_titles=["A"])) is False


async def test_the_prompt_carries_titles_only_and_no_history():
    """O(1) in library size, and per-turn by construction: no prior messages
    reach this call, so an earlier turn cannot widen a later one."""
    agent = ScopeWidenerAgent()
    fake = AsyncMock(return_value=WidenDecision(widen=False))
    with patch("app.agents.scope_widener.parse_structured", new=fake):
        await agent.run(WidenerInput(query="what reward?", mentioned_titles=["Paper A", "Paper B"]))

    user_prompt = fake.await_args.kwargs["user"]
    assert "Paper A" in user_prompt and "Paper B" in user_prompt
    assert "what reward?" in user_prompt
    assert "PRIOR CONVERSATION" not in user_prompt
