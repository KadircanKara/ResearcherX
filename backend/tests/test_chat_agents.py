"""RetrievalPlannerAgent and ChatAgent unit tests — LLM mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.retrieval_planner import PlannerInput, RetrievalPlan, RetrievalPlannerAgent
from app.agents.chat_agent import (
    ChatAgent,
    ChatAgentInput,
    ChunkContext,
    PaperMetaContext,
    build_papers_block,
)


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
        plan = await agent.run(
            PlannerInput(
                query="Compare methods in both papers",
                paper_list=[
                    {"paper_id": "p1", "title": "Paper A", "abstract": "Abstract A"},
                    {"paper_id": "p2", "title": "Paper B", "abstract": "Abstract B"},
                ],
                prior_messages=[],
            )
        )
    assert plan.mode == "comparative"
    assert len(plan.per_paper) == 2


async def test_retrieval_planner_fail_open():
    """Error in parse_structured → fail open with broad defaults."""
    agent = RetrievalPlannerAgent()
    with patch(
        "app.agents.retrieval_planner.parse_structured",
        new=AsyncMock(side_effect=ValueError("llm down")),
    ):
        plan = await agent.run(
            PlannerInput(
                query="Any question",
                paper_list=[
                    {"paper_id": "px", "title": "X", "abstract": "x"},
                ],
                prior_messages=[],
            )
        )
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
        async for token in agent.stream(
            ChatAgentInput(
                query="What is the MTSP problem?",
                prior_messages=[],
                paper_chunks=[
                    ChunkContext(n=1, paper_id="p1", title="Paper", chunk_index=0, text="MTSP text")
                ],
            )
        ):
            tokens.append(token)
    assert "hello " in tokens


def test_papers_block_renders_every_known_field():
    block = build_papers_block(
        [
            PaperMetaContext(
                title="Joint Optimization",
                authors=["Kadircan Kara", "Evşen Yanmaz"],
                year=2024,
                venue="IEEE ICRA",
            )
        ]
    )
    assert "Joint Optimization" in block
    assert "Kadircan Kara, Evşen Yanmaz" in block
    assert "2024" in block
    assert "IEEE ICRA" in block


def test_papers_block_omits_absent_fields_without_asserting_ignorance():
    """'Authors: unknown' reads as a discovered fact. Absence is silence."""
    block = build_papers_block([PaperMetaContext(title="A Preprint")])
    assert "A Preprint" in block
    assert "unknown" not in block.lower()
    assert "Authors" not in block
    assert "None" not in block


def test_papers_block_lists_one_line_per_paper():
    block = build_papers_block(
        [
            PaperMetaContext(title="First", authors=["A One"]),
            PaperMetaContext(title="Second", authors=["B Two"]),
        ]
    )
    assert len([ln for ln in block.splitlines() if ln.startswith("- ")]) == 2


def test_papers_block_is_empty_for_no_papers():
    assert build_papers_block([]) == ""


async def test_stream_sends_the_papers_block_to_the_model():
    agent = ChatAgent()

    async def aiter_list(items):
        for item in items:
            yield item

    fake_chunk = MagicMock()
    fake_chunk.choices = [MagicMock(delta=MagicMock(content="hi"))]
    fake_stream = MagicMock()
    fake_stream.__aiter__ = lambda self: aiter_list([fake_chunk])

    fake_create = AsyncMock(return_value=fake_stream)
    with patch("app.agents.chat_agent.create_chat_completion", new=fake_create):
        async for _ in agent.stream(
            ChatAgentInput(
                query="Who are the authors?",
                prior_messages=[],
                paper_chunks=[],
                papers=[PaperMetaContext(title="A Preprint", authors=["Kadircan Kara"])],
            )
        ):
            pass

    sent = fake_create.await_args.kwargs["messages"][-1]["content"]
    assert "Kadircan Kara" in sent
    assert "A Preprint" in sent


async def test_stream_still_works_without_papers():
    """The field is defaulted — existing callers must not need to pass it."""
    agent = ChatAgent()

    async def aiter_list(items):
        for item in items:
            yield item

    fake_chunk = MagicMock()
    fake_chunk.choices = [MagicMock(delta=MagicMock(content="hi"))]
    fake_stream = MagicMock()
    fake_stream.__aiter__ = lambda self: aiter_list([fake_chunk])

    with patch(
        "app.agents.chat_agent.create_chat_completion", new=AsyncMock(return_value=fake_stream)
    ):
        tokens = [
            t
            async for t in agent.stream(
                ChatAgentInput(query="q", prior_messages=[], paper_chunks=[])
            )
        ]
    assert tokens == ["hi"]
