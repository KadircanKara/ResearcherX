"""The generation cache. Pure — no model calls, no DB."""

import json

import pytest

from app.agents.chat_agent import ChunkContext
from evals.groundedness.generate import Generated, renumber_catalog
from evals.groundedness.replay import CACHE_VERSION, load_generations, save_generations


def a_generation():
    return Generated(
        question="What does the planner optimise?",
        answer="It minimises revisit time [1].",
        chunks=(
            ChunkContext(
                n=1,
                paper_id="p1",
                title="Joint Optimization",
                chunk_index=3,
                text="…",
                section=("4 Method", "4.2 Objective"),
                page=7,
            ),
        ),
        citations=({"n": 1, "paper_id": "p1", "title": "Joint Optimization", "chunk_index": 3},),
        scope_source="none",
        scoped_titles=(),
        widened=False,
        stripped_markers=0,
    )


def test_a_saved_generation_round_trips_including_chunk_text_section_and_page(tmp_path):
    """The catalog must survive whole: a replay judging against truncated
    excerpts would turn supported claims into unsupported ones."""
    path = tmp_path / "gen.json"
    save_generations(path, {"case": a_generation()}, answering_model="gpt-4.1-mini", project_id="p")
    loaded, model = load_generations(path)
    assert model == "gpt-4.1-mini"
    assert loaded["case"] == a_generation()


def test_a_cache_of_a_different_shape_is_refused(tmp_path):
    """Best-effort parsing would judge answers against a catalog that is not
    the one the model saw, and nothing would report it."""
    path = tmp_path / "gen.json"
    path.write_text(json.dumps({"version": CACHE_VERSION + 1, "generations": {}}))
    with pytest.raises(ValueError, match="version"):
        load_generations(path)


def test_a_catalog_repeating_a_number_is_refused():
    """A repeated number resolves to whichever chunk came last, so a marker
    silently names the wrong paper."""
    chunk = a_generation().chunks[0]
    with pytest.raises(ValueError, match="unique"):
        Generated(
            question="q",
            answer="a",
            chunks=(chunk, chunk),
            citations=(),
            scope_source="none",
            scoped_titles=(),
            widened=False,
            stripped_markers=0,
        )


def _chunk(n: int, paper: str) -> ChunkContext:
    return ChunkContext(
        n=n, paper_id=paper, title=paper, chunk_index=n, text="…", section=("Intro",), page=n
    )


def test_cited_chunks_take_their_marker_and_uncited_ones_take_a_free_number():
    """Original numbers 1 and 2 are both taken once 3 and 4 are renumbered to
    them, which is the collision this function exists to prevent."""
    chunks = [_chunk(1, "a"), _chunk(2, "b"), _chunk(3, "c"), _chunk(4, "d")]
    out = renumber_catalog(chunks, {3: 1, 4: 2})
    assert {c.paper_id: c.n for c in out} == {"c": 1, "d": 2, "a": 3, "b": 4}


def test_renumbering_keeps_catalog_order_and_every_field():
    chunks = [_chunk(1, "a"), _chunk(2, "b")]
    out = renumber_catalog(chunks, {2: 1})
    assert [c.paper_id for c in out] == ["a", "b"]
    assert out[1].section == ("Intro",)
    assert out[1].page == 2


def test_a_catalog_with_no_citations_keeps_unique_numbers():
    out = renumber_catalog([_chunk(5, "a"), _chunk(9, "b")], {})
    assert [c.n for c in out] == [1, 2]
