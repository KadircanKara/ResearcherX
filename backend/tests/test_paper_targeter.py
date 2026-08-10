"""PaperTargeterAgent unit tests — LLM mocked."""

from unittest.mock import AsyncMock, patch

from app.agents.paper_targeter import PaperTargeterAgent, TargetedPaper, TargeterInput

CANDIDATES = [
    {"paper_id": "p1", "title": "Cooperative Multi-Target Search with UAV Swarms"},
    {"paper_id": "p2", "title": "Joint Optimization of Connectivity and Coverage"},
]


async def test_targeter_returns_the_chosen_paper_id():
    agent = PaperTargeterAgent()
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(return_value=TargetedPaper(paper_id="p1")),
    ):
        got = await agent.run(
            TargeterInput(
                query="What reward functions does the UAV swarm search paper use?",
                candidates=CANDIDATES,
                prior_messages=[],
            )
        )
    assert got == "p1"


async def test_targeter_returns_none_when_no_paper_is_identified():
    """ "None" is a normal answer, not a failure: "what values of M were
    tested?" names no paper in a 100-paper corpus, and guessing one would
    scope retrieval to the wrong paper with no safety net."""
    agent = PaperTargeterAgent()
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(return_value=TargetedPaper(paper_id="")),
    ):
        got = await agent.run(
            TargeterInput(
                query="What values of M were tested?", candidates=CANDIDATES, prior_messages=[]
            )
        )
    assert got is None


async def test_targeter_rejects_an_id_that_was_not_offered():
    """A hallucinated or mangled id would scope retrieval to nothing, or to
    another project's paper. Only ids from the candidate list are trusted."""
    agent = PaperTargeterAgent()
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(return_value=TargetedPaper(paper_id="p99")),
    ):
        got = await agent.run(TargeterInput(query="q", candidates=CANDIDATES, prior_messages=[]))
    assert got is None


async def test_targeter_fails_open_to_none():
    """A flaky call must leave the pipeline no worse than not calling it —
    None falls through to unscoped global retrieval, today's behaviour."""
    agent = PaperTargeterAgent()
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(side_effect=ValueError("llm down")),
    ):
        got = await agent.run(TargeterInput(query="q", candidates=CANDIDATES, prior_messages=[]))
    assert got is None


async def test_targeter_prompt_carries_titles_only():
    """THE load-bearing test. The targeter's cost must be O(1) in library
    size, which holds only while it receives titles and ids — never abstracts
    and never chunk text. The retrieval planner this replaces died of exactly
    that: its prompt carried every paper's title AND 300-char abstract, ~13k
    tokens at 100 papers, and could not emit a usable answer at all.
    """
    agent = PaperTargeterAgent()
    parse = AsyncMock(return_value=TargetedPaper(paper_id="p1"))
    with patch("app.agents.paper_targeter.parse_structured", new=parse):
        await agent.run(
            TargeterInput(
                query="q",
                candidates=[
                    {
                        "paper_id": "p1",
                        "title": "Cooperative Multi-Target Search",
                        "abstract": "SHOULD NOT APPEAR",
                        "text": "ALSO SHOULD NOT APPEAR",
                    }
                ],
                prior_messages=[],
            )
        )

    sent = parse.await_args.kwargs["user"]
    assert "Cooperative Multi-Target Search" in sent
    assert "p1" in sent
    assert "SHOULD NOT APPEAR" not in sent
    assert "ALSO SHOULD NOT APPEAR" not in sent


async def test_targeter_takes_its_budget_from_settings():
    """Provider-specific like the other agent budgets: the output is a single
    id, but a reasoning model spends ~1,900 tokens thinking before emitting a
    character and would truncate the call outright."""
    from app.core.config import settings

    agent = PaperTargeterAgent()
    parse = AsyncMock(return_value=TargetedPaper(paper_id="p1"))
    with (
        patch("app.agents.paper_targeter.parse_structured", new=parse),
        patch.object(settings, "paper_targeter_max_tokens", 1234),
    ):
        await agent.run(TargeterInput(query="q", candidates=CANDIDATES, prior_messages=[]))

    assert parse.await_args.kwargs["max_tokens"] == 1234
