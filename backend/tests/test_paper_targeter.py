"""PaperTargeterAgent unit tests — LLM mocked."""

from unittest.mock import AsyncMock, patch

from app.agents.paper_targeter import PaperTargeterAgent, TargetedPaper, TargeterInput

CANDIDATES = [
    {"paper_id": "p1", "title": "Cooperative Multi-Target Search with UAV Swarms"},
    {"paper_id": "p2", "title": "Joint Optimization of Connectivity and Coverage"},
]


async def test_targeter_returns_the_chosen_paper_ids():
    inp = TargeterInput(
        query="What reward functions does the UAV swarm search paper use?",
        candidates=CANDIDATES,
        prior_messages=[],
    )
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(return_value=TargetedPaper(paper_ids=["p1"])),
    ):
        got = await PaperTargeterAgent().run(inp)
    assert got == ["p1"]


async def test_targeter_returns_empty_when_no_paper_is_identified():
    """An empty list means unscoped global retrieval. It must stay the normal
    answer for a general question -- guessing would scope to the wrong paper
    with no safety net."""
    inp = TargeterInput(
        query="What values of M were tested?", candidates=CANDIDATES, prior_messages=[]
    )
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(return_value=TargetedPaper(paper_ids=[])),
    ):
        got = await PaperTargeterAgent().run(inp)
    assert got == []


async def test_targeter_rejects_an_id_that_was_not_offered():
    """A hallucinated or mangled id would scope retrieval to nothing, or to
    another project's paper. Only ids from the candidate list are trusted."""
    inp = TargeterInput(query="q", candidates=CANDIDATES, prior_messages=[])
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(return_value=TargetedPaper(paper_ids=["p99"])),
    ):
        got = await PaperTargeterAgent().run(inp)
    assert got == []


async def test_targeter_fails_open_to_empty():
    """A flaky call must leave the pipeline no worse than not calling it —
    an empty list falls through to unscoped global retrieval, today's behaviour."""
    inp = TargeterInput(query="q", candidates=CANDIDATES, prior_messages=[])
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        got = await PaperTargeterAgent().run(inp)
    assert got == []


async def test_targeter_prompt_carries_titles_only():
    """THE load-bearing test. The targeter's cost must be O(1) in library
    size, which holds only while it receives titles and ids — never abstracts
    and never chunk text. The retrieval planner this replaces died of exactly
    that: its prompt carried every paper's title AND 300-char abstract, ~13k
    tokens at 100 papers, and could not emit a usable answer at all.
    """
    agent = PaperTargeterAgent()
    parse = AsyncMock(return_value=TargetedPaper(paper_ids=["p1"]))
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


async def test_targeter_fails_open_when_a_candidate_dict_is_malformed():
    """A candidate dict missing an expected key (e.g. "title") must fail open
    to an empty list like every other error path here, not raise KeyError out of
    run() and fail the whole turn. Prompt construction happens inside the
    try for exactly this reason."""
    malformed = [{"paper_id": "p1"}]  # missing "title"
    got = await PaperTargeterAgent().run(
        TargeterInput(query="q", candidates=malformed, prior_messages=[])
    )
    assert got == []


async def test_targeter_takes_its_budget_from_settings():
    """Provider-specific like the other agent budgets: the output is a list of
    ids, but a reasoning model spends tokens thinking and would truncate the
    call outright."""
    from app.core.config import settings

    parse = AsyncMock(return_value=TargetedPaper(paper_ids=["p1"]))
    with (
        patch("app.agents.paper_targeter.parse_structured", new=parse),
        patch.object(settings, "paper_targeter_max_tokens", 1234),
    ):
        await PaperTargeterAgent().run(
            TargeterInput(query="q", candidates=CANDIDATES, prior_messages=[])
        )

    assert parse.await_args.kwargs["max_tokens"] == 1234


async def test_targeter_returns_several_papers():
    inp = TargeterInput(
        query="compare a and b",
        candidates=[{"paper_id": "a", "title": "A"}, {"paper_id": "b", "title": "B"}],
        prior_messages=[],
    )
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(return_value=TargetedPaper(paper_ids=["a", "b"])),
    ):
        assert await PaperTargeterAgent().run(inp) == ["a", "b"]


async def test_targeter_drops_ids_that_were_not_offered():
    inp = TargeterInput(query="q", candidates=[{"paper_id": "a", "title": "A"}], prior_messages=[])
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(return_value=TargetedPaper(paper_ids=["a", "ghost"])),
    ):
        assert await PaperTargeterAgent().run(inp) == ["a"]


async def test_targeter_dedupes_and_preserves_order():
    inp = TargeterInput(
        query="q",
        candidates=[{"paper_id": "a", "title": "A"}, {"paper_id": "b", "title": "B"}],
        prior_messages=[],
    )
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(return_value=TargetedPaper(paper_ids=["b", "a", "b"])),
    ):
        assert await PaperTargeterAgent().run(inp) == ["b", "a"]


async def test_targeter_does_not_cap_the_list():
    """Capping is scope policy and lives in chat_service. The agent's only
    jobs are validation and fail-open."""
    candidates = [{"paper_id": f"p{i}", "title": f"T{i}"} for i in range(8)]
    inp = TargeterInput(query="q", candidates=candidates, prior_messages=[])
    with patch(
        "app.agents.paper_targeter.parse_structured",
        new=AsyncMock(return_value=TargetedPaper(paper_ids=[f"p{i}" for i in range(8)])),
    ):
        assert await PaperTargeterAgent().run(inp) == [f"p{i}" for i in range(8)]
