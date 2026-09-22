"""Wiring: store + index + query -> hits -> an answer.

Retrieval is `search.py`'s and the prompt contract is `chat_agent.py`'s;
this module only connects them. In particular the answer path reuses the
production system prompt and the production citation post-pass, in
production's own order — `expand_grouped_citations` then
`strip_misattributed_citations` then `renumber_citations`. That order is
load-bearing: renumbering assigns 1..N by first appearance, so a marker
stripped afterwards would leave a citation pointing at a claim that no
longer cites it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.agents.chat_agent import ChatAgent, ChatAgentInput, ChunkContext, PaperMetaContext
from app.core.config import settings
from app.local_rag.index import load_bm25
from app.local_rag.rerank import CohereReranker
from app.local_rag.search import Hit, SearchParams, search
from app.local_rag.store import LocalStore
from app.services.chat_service import renumber_citations
from app.services.citation_attribution import (
    expand_grouped_citations,
    strip_misattributed_citations,
)
from app.services.embedding_service import EmbeddingService


def build_reranker() -> CohereReranker | None:
    """None when no key is configured — the pipeline then keeps its fused
    RRF order, which is a degraded ranking and not a failure."""
    if not settings.cohere_api_key:
        return None
    return CohereReranker(api_key=settings.cohere_api_key, model=settings.cohere_rerank_model)


def to_chunk_contexts(hits: list[Hit]) -> list[ChunkContext]:
    """The excerpt catalog the model is shown, in retrieval order.

    Carries `section`/`page` from the store's `Chunk` so the excerpt header
    (`chunk_header.excerpt_text`, called by `ChatAgent`) renders the same
    `[Title | Section | Page]` locator the database path does — before this,
    `local_rag.store.Chunk` had no such fields and every excerpt here
    degraded to a bare `[Title: X]`, losing information the main path
    already carries and gaining nothing in return.
    """
    return [
        ChunkContext(
            n=hit.n,
            paper_id=hit.chunk.paper_id,
            title=hit.chunk.paper_title,
            chunk_index=hit.chunk.chunk_index,
            text=hit.chunk.text,
            section=hit.chunk.section,
            page=hit.chunk.page,
        )
        for hit in hits
    ]


async def retrieve(
    *,
    root: Path,
    query: str,
    params: SearchParams | None = None,
    embeddings: EmbeddingService | None = None,
) -> list[Hit]:
    store = LocalStore.load(root)
    if store.embedding_model != settings.embedding_model:
        # The same failure the `model` column guards in the database path:
        # comparing a query vector against another model's index returns
        # confident nonsense, with nothing in the output saying so.
        raise ValueError(
            f"index was built with {store.embedding_model!r} but EMBEDDING_MODEL is "
            f"{settings.embedding_model!r} — re-ingest, or point EMBEDDING_MODEL back"
        )
    embeddings = embeddings or EmbeddingService()
    query_vector = np.array(
        await embeddings.embed(query, task_type="RETRIEVAL_QUERY"), dtype=np.float32
    )
    return await search(
        store=store,
        bm25=load_bm25(store.bm25_dir),
        query=query,
        query_vector=query_vector,
        reranker=build_reranker(),
        params=params or SearchParams(),
    )


async def answer(*, root: Path, query: str, hits: list[Hit]) -> str:
    """Generate an answer over `hits` using the production prompt contract."""
    store = LocalStore.load(root)
    contexts = to_chunk_contexts(hits)
    agent_input = ChatAgentInput(
        query=query,
        prior_messages=[],
        paper_chunks=contexts,
        papers=[
            PaperMetaContext(title=p.title, authors=list(p.authors), year=p.year, venue=p.venue)
            for p in store.papers
        ],
    )

    parts: list[str] = []
    async for token in ChatAgent().stream(agent_input):
        parts.append(token)
    text = "".join(parts)

    max_n = len(contexts)
    text = expand_grouped_citations(text, max_n)
    text, _stripped = strip_misattributed_citations(
        text,
        chunk_papers={c.n: c.paper_id for c in contexts},
        paper_titles={p.paper_id: p.title for p in store.papers},
    )
    text, _renumbered = renumber_citations(text, max_n)
    return text
