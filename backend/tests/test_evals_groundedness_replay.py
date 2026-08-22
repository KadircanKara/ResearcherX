"""The generation cache. Pure — no model calls, no DB."""

import json

import pytest

from app.agents.chat_agent import ChunkContext
from evals.groundedness.generate import Generated
from evals.groundedness.replay import CACHE_VERSION, load_generations, save_generations


def a_generation():
    return Generated(
        question="What does the planner optimise?",
        answer="It minimises revisit time [1].",
        chunks=(
            ChunkContext(n=1, paper_id="p1", title="Joint Optimization", chunk_index=3, text="…"),
        ),
        citations=({"n": 1, "paper_id": "p1", "title": "Joint Optimization", "chunk_index": 3},),
        scope_source="none",
        scoped_titles=(),
        widened=False,
        stripped_markers=0,
    )


def test_a_saved_generation_round_trips_including_the_full_chunk_text(tmp_path):
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
