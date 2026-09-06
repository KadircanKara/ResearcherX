"""app/local_rag/rag.py — the wiring between the store, the search and the
answer prompt."""

import pytest_asyncio

from app.agents.chat_agent import ChatAgent
from app.core.config import settings
from app.local_rag.ingest import ingest
from app.local_rag.rag import answer, build_reranker, to_chunk_contexts
from app.local_rag.rerank import CohereReranker
from app.local_rag.search import Hit
from app.local_rag.store import Chunk


def _hit(n: int, paper_id: str, index: int, text: str) -> Hit:
    return Hit(
        n=n,
        chunk=Chunk(
            paper_id=paper_id, paper_title=f"Title of {paper_id}", chunk_index=index, text=text
        ),
        dense_rank=1,
        sparse_rank=None,
        rerank_score=0.9,
    )


def test_no_cohere_key_means_no_reranker(monkeypatch):
    """Running without a key is a normal local state: the pipeline degrades
    to fused RRF order rather than refusing to run."""
    monkeypatch.setattr(settings, "cohere_api_key", "")
    assert build_reranker() is None


def test_a_cohere_key_builds_a_reranker(monkeypatch):
    monkeypatch.setattr(settings, "cohere_api_key", "key-abc")
    reranker = build_reranker()
    assert isinstance(reranker, CohereReranker)


def test_hits_become_the_excerpt_catalog_in_order():
    hits = [_hit(1, "p1", 4, "first excerpt"), _hit(2, "p2", 0, "second excerpt")]

    contexts = to_chunk_contexts(hits)

    assert [c.n for c in contexts] == [1, 2]
    assert [c.paper_id for c in contexts] == ["p1", "p2"]
    assert [c.chunk_index for c in contexts] == [4, 0]
    assert [c.title for c in contexts] == ["Title of p1", "Title of p2"]
    assert contexts[0].text == "first excerpt"


def test_an_empty_hit_list_makes_an_empty_catalog():
    """What drives the prompt's refusal branch."""
    assert to_chunk_contexts([]) == []


# ── answer() ─────────────────────────────────────────────────────────────


# Titles carry four or more words on purpose: strip_misattributed_citations
# only opens an attribution span on a >=4-word title match, and fails toward
# NOT enforcing. A one-word title would make the strip test vacuous.
_TITLE_A = "Swarm Coverage Path Planning"
_TITLE_B = "Battery Chemistry For Small Drones"


@pytest_asyncio.fixture
async def built_store(tmp_path):
    """A two-paper store on disk, so answer() can load it."""

    async def _embed(texts):
        return [[0.1] * 4 for _ in texts]

    (tmp_path / f"{_TITLE_A}.txt").write_text("content about swarm coverage")
    (tmp_path / f"{_TITLE_B}.txt").write_text("content about battery chemistry")
    root = tmp_path / "index"
    await ingest(
        sources=[tmp_path / f"{_TITLE_A}.txt", tmp_path / f"{_TITLE_B}.txt"],
        root=root,
        embed=_embed,
    )
    return root


def _stub_stream(text: str):
    async def _stream(self, inp):
        yield text

    return _stream


def _store_hits() -> list[Hit]:
    return [
        _hit(1, _TITLE_A, 0, "content about swarm coverage"),
        _hit(2, _TITLE_B, 0, "content about battery chemistry"),
    ]


async def test_answer_renumbers_catalog_positions_to_one_to_n(monkeypatch, built_store):
    """The model cites by catalog position; the reader sees 1..N."""
    monkeypatch.setattr(ChatAgent, "stream", _stub_stream("Swarms are studied [2]."))

    out = await answer(root=built_store, query="q", hits=_store_hits())

    assert out == "Swarms are studied [1]."


async def test_answer_strips_a_marker_pointing_at_another_paper(monkeypatch, built_store):
    """The deterministic attribution guard runs here too, and it must run
    BEFORE renumbering or the stripped marker would still burn a number."""
    # The list item is attributed to paper B by title, but cites excerpt 1,
    # which belongs to paper A.
    monkeypatch.setattr(ChatAgent, "stream", _stub_stream(f"- {_TITLE_B} reports a finding [1].\n"))

    out = await answer(root=built_store, query="q", hits=_store_hits())

    assert "[1]" not in out
    assert f"{_TITLE_B} reports a finding" in out


async def test_answer_expands_a_grouped_citation_before_renumbering(monkeypatch, built_store):
    monkeypatch.setattr(ChatAgent, "stream", _stub_stream("Both agree [1, 2]."))

    out = await answer(root=built_store, query="q", hits=_store_hits())

    assert out == "Both agree [1], [2]."
