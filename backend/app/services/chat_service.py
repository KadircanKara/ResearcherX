"""ChatService — orchestrates the RAG chat pipeline for one user message.

Yields SSE event dicts: {event, data} where data is a JSON string.
All DB sessions are created internally (called from a streaming HTTP handler
whose FastAPI session closes immediately after yielding the EventSourceResponse).
"""

import json
import re
from collections.abc import AsyncGenerator

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat_agent import ChatAgent, ChatAgentInput, ChunkContext, PaperMetaContext
from app.agents.paper_targeter import PaperTargeterAgent, TargeterInput
from app.agents.query_reformulator import QueryReformulatorAgent, ReformulatorInput
from app.core.config import settings
from app.core.logging import log
from app.db.models import Paper
from app.db.session import SessionLocal
from app.services.conversation_service import ConversationService
from app.services.embedding_service import EmbeddingService

_HISTORY_TOP_K = 5

# Papers offered to the targeter. Sized by measurement, not by prompt budget:
# on the question this feature exists to fix, the correct paper ranked 6th by
# nearest-chunk distance. Questions whose target ranks below this generally
# name no paper at all (measured at 17th and 51st), where the honest answer is
# "none" anyway.
_TARGETER_CANDIDATES = 10

_CITATION_RE = re.compile(r"\[(\d+)\]")


class PaperInfo(BaseModel):
    """Just enough about a paper to scope retrieval and label a citation."""

    paper_id: str
    title: str


def _vec_str(embedding: list[float]) -> str:
    """Format Python list as pgvector string: [0.1, 0.2, ...]"""
    return "[" + ",".join(str(x) for x in embedding) + "]"


class ChatService:
    def __init__(self) -> None:
        self._embedding_svc = EmbeddingService()
        self._reformulator = QueryReformulatorAgent()
        self._targeter = PaperTargeterAgent()
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

            # This project's paper ids + titles: scopes the global top-k chunk
            # query below to this project and labels citations by title.
            paper_infos = [PaperInfo(paper_id=p.id, title=p.title) for p in paper_rows]

            if query_embedding is not None:
                # Retrieve relevant history
                async with SessionLocal() as db:
                    history_hits = await self._retrieve_history(
                        db, conversation_id, query_embedding
                    )

                if paper_infos:
                    # Reformulate only when there IS a conversation to resolve
                    # against. A first turn is already standalone, so the call
                    # would buy nothing and is skipped.
                    #
                    # Gate on prior_messages ALONE -- never on history_hits or
                    # on reformulation_context below. conversation_service's
                    # save_message() fires asyncio.create_task(_embed_message
                    # (...)) for the user's own message before respond() runs,
                    # so by the time _retrieve_history executes above, that
                    # row is frequently already embedded and sitting in
                    # conversation_message_embeddings. It then self-matches
                    # its own query embedding at distance ~= 0 (comfortably
                    # under similarity_threshold) and comes back as a
                    # "history hit" -- even on a genuine first turn. Gating on
                    # `prior_messages + history_hits` therefore raced that
                    # background embedding task: whether the reformulator ran
                    # on a first turn depended on embedding latency, not on
                    # whether a prior turn actually existed. prior_messages is
                    # immune to that race (it's read from conv.messages,
                    # loaded before this turn's message was embedded), and
                    # hits can only ever come from THIS conversation, so an
                    # empty prior_messages means any hit IS the current
                    # message. Do not "simplify" this back to
                    # `if reformulation_context:`.
                    reformulation_context = prior_messages + history_hits
                    retrieval_query = user_content
                    if prior_messages:
                        retrieval_query = await self._reformulator.run(
                            ReformulatorInput(
                                query=user_content,
                                prior_messages=reformulation_context,
                            )
                        )

                    retrieval_embedding = (
                        await self._embedding_svc.embed(
                            retrieval_query, task_type="RETRIEVAL_QUERY"
                        )
                        if retrieval_query != user_content
                        else query_embedding
                    )

                    async with SessionLocal() as db:
                        candidates, total_chunks = await self._shortlist_papers(
                            db, paper_infos, retrieval_embedding, _TARGETER_CANDIDATES
                        )

                    # Scope retrieval to one paper when the question is about
                    # one paper. Skipped when the whole library already fits
                    # the budget: scoping cannot change what is retrieved
                    # then, so the call would be pure cost and latency.
                    scope = paper_infos
                    if total_chunks > settings.max_context_chunks and candidates:
                        target_id = await self._targeter.run(
                            TargeterInput(
                                query=retrieval_query,
                                candidates=[
                                    {"paper_id": c.paper_id, "title": c.title} for c in candidates
                                ],
                                prior_messages=reformulation_context,
                            )
                        )
                        if target_id is not None:
                            scope = [c for c in candidates if c.paper_id == target_id]

                    async with SessionLocal() as db:
                        paper_chunks = await self._retrieve_paper_chunks(
                            db, scope, retrieval_embedding
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

    async def _shortlist_papers(
        self,
        db: AsyncSession,
        paper_infos: list[PaperInfo],
        query_embedding: list[float],
        limit: int,
    ) -> tuple[list[PaperInfo], int]:
        """Rank this project's papers by their nearest chunk.

        Returns at most `limit` papers, nearest first, plus the project's
        TOTAL chunk count across every paper — two answers from one query.

        No SQL LIMIT, on purpose. The GROUP BY already scans the project's
        chunks either way, so a LIMIT would save only the transfer of a few
        dozen rows while costing a second round trip to learn the total. The
        total is what decides whether targeting is worth an LLM call at all.

        MIN distance rather than a mean of the nearest few: measured across
        seven questions with known target papers the two ranked about equally
        well, and MIN is simpler. No similarity_threshold filter — the
        threshold belongs at retrieval time, and applying it here could empty
        the shortlist on a vaguely worded question.
        """
        if not paper_infos:
            return [], 0
        sql = text("""
            WITH scope AS (
                SELECT value AS paper_id
                FROM jsonb_array_elements_text(CAST(:ids AS jsonb))
            )
            SELECT c.paper_id,
                   MIN(c.embedding <=> CAST(:qvec AS vector)) AS best,
                   COUNT(*) AS n_chunks
            FROM paper_chunk_embeddings c
            JOIN scope s ON s.paper_id = c.paper_id
            WHERE c.model = :model
            GROUP BY c.paper_id
            ORDER BY best ASC
        """)
        result = await db.execute(
            sql,
            {
                "qvec": _vec_str(query_embedding),
                "ids": json.dumps([p.paper_id for p in paper_infos]),
                "model": settings.embedding_model,
            },
        )
        rows = result.fetchall()
        by_id = {p.paper_id: p for p in paper_infos}
        total_chunks = sum(r.n_chunks for r in rows)
        candidates = [by_id[r.paper_id] for r in rows if r.paper_id in by_id][:limit]
        return candidates, total_chunks

    async def _retrieve_paper_chunks(
        self,
        db: AsyncSession,
        paper_infos: list[PaperInfo],
        query_embedding: list[float],
    ) -> list[ChunkContext]:
        """Retrieve the globally nearest chunks across the whole library.

        ONE query, and no per-paper ceiling. Cosine similarity spreads the
        result across papers by itself: measured on a 100-paper library, a
        global top-40 spanned 14-25 distinct papers and no paper ever took
        more than 11 of the 40 slots — and that case was the correct paper
        going deep on a question about its own contents.

        A ceiling could only ever REDUCE the right paper's share, never raise
        it. On the EA-operators question the target paper earns 6 chunks by
        distance, where the old unallocated fallback would have capped it at 2.

        The budget must stay a function of the CONTEXT WINDOW, never of
        library size. Per-paper allocation made it the latter, and a 100-paper
        project pulled 191 chunks (~118.5k tokens) until every turn died on
        `context_length_exceeded`.
        """
        if not paper_infos:
            return []
        qvec = _vec_str(query_embedding)
        paper_title_map = {p.paper_id: p.title for p in paper_infos}
        # `paper_chunk_embeddings` is global, so this query MUST be scoped to
        # the project. The ids ride in as one jsonb param rather than an IN
        # list: 100 papers would otherwise need 100 bind params, and asyncpg
        # array binding through text() needs casts that differ per driver.
        sql = text("""
            WITH scope AS (
                SELECT value AS paper_id
                FROM jsonb_array_elements_text(CAST(:ids AS jsonb))
            )
            SELECT c.paper_id, c.chunk_index, c.text,
                   (c.embedding <=> CAST(:qvec AS vector)) AS distance
            FROM paper_chunk_embeddings c
            JOIN scope s ON s.paper_id = c.paper_id
            WHERE c.model = :model
              AND (c.embedding <=> CAST(:qvec AS vector)) < :threshold
            ORDER BY distance ASC
            LIMIT :max_chunks
        """)
        result = await db.execute(
            sql,
            {
                "qvec": qvec,
                "ids": json.dumps([p.paper_id for p in paper_infos]),
                "model": settings.embedding_model,
                "threshold": settings.similarity_threshold,
                "max_chunks": settings.max_context_chunks,
            },
        )
        # Re-applied in Python on purpose. LIMIT bounds what crosses the wire;
        # this bounds what reaches the model. The budget is the single
        # invariant that keeps chat working at any library size, so it must
        # not depend on a SQL clause surviving a future edit to the query.
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
