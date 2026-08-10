"""ChatService — orchestrates the RAG chat pipeline for one user message.

Yields SSE event dicts: {event, data} where data is a JSON string.
All DB sessions are created internally (called from a streaming HTTP handler
whose FastAPI session closes immediately after yielding the EventSourceResponse).
"""

import json
import re
from collections.abc import AsyncGenerator

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat_agent import ChatAgent, ChatAgentInput, ChunkContext, PaperMetaContext
from app.agents.retrieval_planner import PaperInfo, PlannerInput, RetrievalPlannerAgent
from app.core.config import settings
from app.core.logging import log
from app.db.models import Paper
from app.db.session import SessionLocal
from app.services.conversation_service import ConversationService
from app.services.embedding_service import EmbeddingService

_HISTORY_TOP_K = 5
_PLANNER_MIN_PAPERS = 3  # skip planner for ≤2 papers; use broad default
_SMALL_LIBRARY_K = 5  # chunks per paper when planner is skipped (few papers → go deeper)
# Ceiling for a paper the planner ran but never allocated. The planner cannot
# enumerate a large library inside its max_tokens budget, so on a 100-paper
# project this silently covers most of them — evals/retrieval/run_eval.py
# mirrors this constant when simulating production retrieval.
_UNALLOCATED_PAPER_K = 2

_CITATION_RE = re.compile(r"\[(\d+)\]")


def _vec_str(embedding: list[float]) -> str:
    """Format Python list as pgvector string: [0.1, 0.2, ...]"""
    return "[" + ",".join(str(x) for x in embedding) + "]"


class ChatService:
    def __init__(self) -> None:
        self._embedding_svc = EmbeddingService()
        self._planner = RetrievalPlannerAgent()
        self._chat_agent = ChatAgent()
        self._conv_svc = ConversationService()

    async def respond(self, conversation_id: str, user_content: str) -> AsyncGenerator[dict, None]:
        """Yield SSE event dicts for one user message."""
        try:
            yield {"event": "thinking", "data": "{}"}

            async with SessionLocal() as db:
                conv = await self._conv_svc.get_conversation(db, conversation_id)
                if conv is None:
                    yield {
                        "event": "error",
                        "data": json.dumps({"message": "Conversation not found"}),
                    }
                    return

                # Load papers assigned to this project
                paper_rows = (
                    (await db.execute(select(Paper).where(Paper.project_id == conv.project_id)))
                    .scalars()
                    .all()
                )

                # Built inside the session: the attributes are loaded, but
                # building it here keeps it independent of session lifetime.
                paper_metas = [
                    PaperMetaContext(
                        title=p.title,
                        authors=list(p.authors or []),
                        year=p.year,
                        venue=p.venue,
                    )
                    for p in paper_rows
                ]

                # Format prior messages (all except the user's current message)
                prior_messages = [
                    {"role": m.role, "content": m.content}
                    for m in conv.messages[:-1]  # exclude the last (just-saved user msg)
                ]

            # Embed the query — fail-open: if embedding unavailable, skip retrieval
            history_hits: list[dict] = []
            paper_chunks: list = []
            query_embedding: list[float] | None = None

            try:
                query_embedding = await self._embedding_svc.embed(
                    user_content, task_type="RETRIEVAL_QUERY"
                )
            except Exception:
                log.warning("chat_embedding_unavailable_fallback", conversation_id=conversation_id)

            # Retrieval plan
            paper_infos = [
                PaperInfo(paper_id=p.id, title=p.title, abstract=p.abstract or "")
                for p in paper_rows
            ]

            if query_embedding is not None:
                # Retrieve relevant history
                async with SessionLocal() as db:
                    history_hits = await self._retrieve_history(
                        db, conversation_id, query_embedding
                    )

                if paper_infos:
                    if len(paper_infos) >= _PLANNER_MIN_PAPERS:
                        plan = await self._planner.run(
                            PlannerInput(
                                query=user_content,
                                paper_list=paper_infos,
                                prior_messages=prior_messages + history_hits,
                            )
                        )
                        retrieval_query = plan.reformulated_query or user_content
                        # A degraded plan carries no allocation signal, so don't
                        # manufacture one — None means "no per-paper ceiling",
                        # and the global top-k decides alone.
                        per_paper_map = (
                            None
                            if plan.degraded
                            else {alloc.paper_id: alloc.chunks for alloc in plan.per_paper}
                        )
                    else:
                        retrieval_query = user_content
                        per_paper_map = {p.id: _SMALL_LIBRARY_K for p in paper_rows}

                    retrieval_embedding = (
                        await self._embedding_svc.embed(
                            retrieval_query, task_type="RETRIEVAL_QUERY"
                        )
                        if retrieval_query != user_content
                        else query_embedding
                    )

                    async with SessionLocal() as db:
                        paper_chunks = await self._retrieve_paper_chunks(
                            db, paper_infos, per_paper_map, retrieval_embedding
                        )

            yield {
                "event": "retrieving",
                "data": json.dumps(
                    {
                        "paper_count": len(paper_rows),
                        "history_hits": len(history_hits),
                    }
                ),
            }

            # Build context for ChatAgent
            all_prior = prior_messages + history_hits  # conversation context
            agent_input = ChatAgentInput(
                query=user_content,
                prior_messages=all_prior,
                paper_chunks=paper_chunks,
                papers=paper_metas,
            )

            # Stream response
            full_response = []
            async for token in self._chat_agent.stream(agent_input):
                full_response.append(token)
                yield {"event": "delta", "data": json.dumps({"text": token})}

            response_text = "".join(full_response)

            # Validate citations: keep only [n] where n ≤ len(paper_chunks)
            max_n = len(paper_chunks)

            def _clean_citations(text_str: str) -> str:
                return _CITATION_RE.sub(
                    lambda m: (
                        m.group(0) if 0 < int(m.group(1)) <= max_n else "[source unavailable]"
                    ),
                    text_str,
                )

            clean_response = _clean_citations(response_text)

            # Build citation objects for 'done' event
            cited_ns = {int(m) for m in _CITATION_RE.findall(clean_response) if 0 < int(m) <= max_n}
            citations = [
                {
                    "n": c.n,
                    "paper_id": c.paper_id,
                    "title": c.title,
                    "chunk_index": c.chunk_index,
                    "snippet": c.text[:200],
                }
                for c in paper_chunks
                if c.n in cited_ns
            ]

            # Persist assistant message
            async with SessionLocal() as db:
                await self._conv_svc.save_message(
                    db, conversation_id, "assistant", clean_response, citations
                )

            yield {"event": "done", "data": json.dumps({"citations": citations})}

        except Exception:
            log.exception("chat_service_error", conversation_id=conversation_id)
            yield {
                "event": "error",
                "data": json.dumps({"message": "Chat failed. Please try again."}),
            }

    async def _retrieve_history(
        self,
        db: AsyncSession,
        conversation_id: str,
        query_embedding: list[float],
    ) -> list[dict]:
        """Semantic search over conversation_message_embeddings."""
        qvec = _vec_str(query_embedding)
        sql = text("""
            SELECT cm.role, cm.content,
                   (cme.embedding <=> CAST(:qvec AS vector)) AS distance
            FROM conversation_message_embeddings cme
            JOIN chat_messages cm ON cm.id = cme.message_id
            WHERE cm.conversation_id = :conv_id
              AND cme.model = :model
              AND (cme.embedding <=> CAST(:qvec AS vector)) < :threshold
            ORDER BY distance ASC
            LIMIT :top_k
        """)
        result = await db.execute(
            sql,
            {
                "qvec": qvec,
                "conv_id": conversation_id,
                "model": settings.embedding_model,
                "threshold": settings.similarity_threshold,
                "top_k": _HISTORY_TOP_K,
            },
        )
        rows = result.fetchall()
        return [{"role": r.role, "content": r.content} for r in rows]

    async def _retrieve_paper_chunks(
        self,
        db: AsyncSession,
        paper_infos: list[PaperInfo],
        per_paper_map: dict[str, int] | None,
        query_embedding: list[float],
    ) -> list[ChunkContext]:
        """Retrieve the globally nearest chunks across the whole library.

        ONE query, not one per paper. Each paper keeps its planner-allocated
        ceiling — so a single well-matching paper can't monopolise the context
        and comparative questions still see both sides — but the final
        selection is a global top-k under a hard budget.

        The distinction matters: per-paper allocation made the retrieved total
        a function of LIBRARY SIZE, so a 100-paper project pulled 191 chunks
        (~118.5k tokens) and every turn failed with `context_length_exceeded`.
        The budget now depends only on the context window.

        `per_paper_map=None` means the planner produced no allocation at all
        (it failed open). There is then nothing to enforce, so every paper is
        given the whole budget as its ceiling — equivalent to no ceiling, since
        the global LIMIT already bounds any single paper to that many chunks.
        """
        if not paper_infos:
            return []
        qvec = _vec_str(query_embedding)
        paper_title_map = {p.paper_id: p.title for p in paper_infos}
        # Every paper gets an EXPLICIT ceiling. The old implicit
        # `.get(paper_id, 2)` applied to whichever papers the planner didn't
        # name — 87 of 100 in practice, since 100 allocations don't fit in the
        # planner's max_tokens — and 2 is twice what 'targeted' mode intends
        # for a non-target paper. Making it explicit keeps the SQL honest and
        # the fallback greppable.
        # Every paper appears either way: the alloc CTE is also what scopes the
        # search to this project's chunks, so it can't be dropped even when
        # there is no ceiling to apply.
        if per_paper_map is None:
            alloc = {p.paper_id: settings.max_context_chunks for p in paper_infos}
        else:
            alloc = {
                p.paper_id: per_paper_map.get(p.paper_id, _UNALLOCATED_PAPER_K) for p in paper_infos
            }

        # The allocation rides in as one jsonb param rather than a VALUES list
        # or a pair of arrays: a 100-paper library would otherwise need 200
        # bind params, and asyncpg array binding through text() needs explicit
        # casts that differ per driver.
        sql = text("""
            WITH alloc AS (
                SELECT key AS paper_id, value::int AS k
                FROM jsonb_each_text(CAST(:alloc AS jsonb))
            ),
            ranked AS (
                SELECT c.paper_id, c.chunk_index, c.text,
                       (c.embedding <=> CAST(:qvec AS vector)) AS distance,
                       ROW_NUMBER() OVER (
                           PARTITION BY c.paper_id
                           ORDER BY c.embedding <=> CAST(:qvec AS vector)
                       ) AS rn
                FROM paper_chunk_embeddings c
                JOIN alloc a ON a.paper_id = c.paper_id
                WHERE c.model = :model
                  AND (c.embedding <=> CAST(:qvec AS vector)) < :threshold
            )
            SELECT r.paper_id, r.chunk_index, r.text, r.distance
            FROM ranked r
            JOIN alloc a ON a.paper_id = r.paper_id
            WHERE r.rn <= a.k
            ORDER BY r.distance ASC
            LIMIT :max_chunks
        """)
        result = await db.execute(
            sql,
            {
                "qvec": qvec,
                "alloc": json.dumps(alloc),
                "model": settings.embedding_model,
                "threshold": settings.similarity_threshold,
                "max_chunks": settings.max_context_chunks,
            },
        )
        # Re-applied in Python on purpose. LIMIT bounds what crosses the wire;
        # this bounds what reaches the model. The budget is the single
        # invariant that keeps chat working at any library size, so it must not
        # depend on a SQL clause surviving a future edit to the query.
        rows = result.fetchall()[: settings.max_context_chunks]
        return [
            ChunkContext(
                n=i,
                paper_id=row.paper_id,
                title=paper_title_map.get(row.paper_id, ""),
                chunk_index=row.chunk_index,
                text=row.text,
            )
            for i, row in enumerate(rows, 1)
        ]
