"""RetrievalPlannerAgent and ChatAgent unit tests — LLM mocked."""
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.retrieval_planner import PlannerInput, RetrievalPlan, RetrievalPlannerAgent
from app.agents.chat_agent import ChatAgent, ChatAgentInput, ChunkContext


FAKE_PLAN = RetrievalPlan(
    mode="comparative",
    reformulated_query="UAV coordination methods comparison",
    per_paper=[{"paper_id": "p1", "chunks": 3}, {"paper_id": "p2", "chunks": 3}],
)


async def test_retrieval_planner_returns_plan():
    agent = RetrievalPlannerAgent()
    with patch(
        "app.agents.retrieval_planner.parse_structured",
        new=AsyncMock(return_value=FAKE_PLAN),
    ):
        plan = await agent.run(PlannerInput(
            query="Compare methods in both papers",
            paper_list=[
                {"paper_id": "p1", "title": "Paper A", "abstract": "Abstract A"},
                {"paper_id": "p2", "title": "Paper B", "abstract": "Abstract B"},
            ],
            prior_messages=[],
        ))
    assert plan.mode == "comparative"
    assert len(plan.per_paper) == 2


async def test_retrieval_planner_fail_open():
    """Error in parse_structured → fail open with broad defaults."""
    agent = RetrievalPlannerAgent()
    with patch(
        "app.agents.retrieval_planner.parse_structured",
        new=AsyncMock(side_effect=ValueError("llm down")),
    ):
        plan = await agent.run(PlannerInput(
            query="Any question",
            paper_list=[
                {"paper_id": "px", "title": "X", "abstract": "x"},
            ],
            prior_messages=[],
        ))
    assert plan.mode == "broad"
    assert plan.per_paper[0].chunks == 2


async def test_chat_agent_streams_tokens():
    agent = ChatAgent()
    fake_chunk = MagicMock()
    fake_chunk.choices = [MagicMock(delta=MagicMock(content="hello "))]
    fake_stream = MagicMock()
    fake_stream.__aiter__ = lambda self: aiter_list([fake_chunk, fake_chunk])

    async def aiter_list(items):
        for item in items:
            yield item

    with patch(
        "app.agents.chat_agent.create_chat_completion",
        new=AsyncMock(return_value=fake_stream),
    ):
        tokens = []
        async for token in agent.stream(ChatAgentInput(
            query="What is the MTSP problem?",
            prior_messages=[],
            paper_chunks=[ChunkContext(n=1, paper_id="p1", title="Paper", chunk_index=0, text="MTSP text")],
        )):
            tokens.append(token)
    assert "hello " in tokens
