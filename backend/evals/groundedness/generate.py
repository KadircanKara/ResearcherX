"""Produce one real answer per golden-set case, WITHOUT writing to the database.

WHY NOT `ChatService.respond`. `respond` is the shipped path and using it would
guarantee zero drift, but it persists an assistant message (and spawns a
message-embedding task) and needs a conversation row to exist first. Every
other harness in `evals/` reads and never writes; a groundedness run that
leaves 42 throwaway conversations in the dev project -- or half of them, on a
crash -- is a worse trade than the drift this module accepts. It also cannot
give the judge what it needs: the SSE `done` event carries citations with a
200-character snippet, only for CITED chunks, where the judge must see the
whole catalog the model was shown.

WHAT IS IMPORTED VS. WHAT IS REPEATED. Every DECISION is imported from
production and never reproduced here: retrieval (`_retrieve_paper_chunks`,
`_retrieve_mentioned_chunks`), scope resolution (`resolve_papers_with_evidence`),
the widener, `needs_paper_metadata`, the system prompt and streaming
(`ChatAgent`), and the whole citation post-pass (`expand_grouped_citations`,
`strip_misattributed_citations`, `renumber_citations`) in production's own
load-bearing order. What is repeated is the WIRING -- which of those to call
and when. That is a real drift surface: if `respond` grows a step, this module
will not have it, and the harness will quietly measure a system that no longer
exists. `tests/test_evals_groundedness_parity.py` pins the specific thing most
likely to rot (the citation post-pass order).

WHAT IS DELIBERATELY NOT REPRODUCED, because the golden set is single-turn:

- conversation history retrieval (`_retrieve_history`) -- there is none
- query reformulation -- production skips it on a first turn for the same
  reason `evals/retrieval/README.md` documents
- persistence and SSE emission -- the two things that would make this write
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.agents.chat_agent import ChatAgent, ChatAgentInput, ChunkContext, PaperMetaContext
from app.agents.scope_widener import ScopeWidenerAgent, WidenerInput
from app.core.config import settings
from app.db.models import Paper
from app.db.session import SessionLocal
from app.services.chat_service import (
    ChatService,
    PaperInfo,
    needs_paper_metadata,
    renumber_citations,
)
from app.services.citation_attribution import (
    expand_grouped_citations,
    strip_misattributed_citations,
)
from app.services.embedding_service import EmbeddingService
from app.services.paper_resolver import ResolvablePaper, resolve_papers_with_evidence


@dataclass(frozen=True)
class Generated:
    """One case's answer plus everything needed to judge it.

    `chunks` is the catalog as the model saw it -- ORIGINAL numbering, before
    renumbering, because that is the numbering the model cited in. `answer` is
    post-renumbering, so `citations` maps its markers back to papers.
    """

    question: str
    answer: str
    chunks: tuple[ChunkContext, ...]
    citations: tuple[dict, ...]
    scope_source: str
    scoped_titles: tuple[str, ...]
    widened: bool
    stripped_markers: int


class AnswerGenerator:
    def __init__(self) -> None:
        self._embeddings = EmbeddingService()
        self._widener = ScopeWidenerAgent()
        self._agent = ChatAgent()
        # Retrieval lives on ChatService and is imported, never re-implemented:
        # the per-paper floor shipped as a no-op precisely because a harness
        # mirrored the SQL instead of calling it.
        self._chat = ChatService()

    async def _papers(self, project_id: str) -> list[Paper]:
        async with SessionLocal() as db:
            rows = await db.execute(select(Paper).where(Paper.project_id == project_id))
            return list(rows.scalars().all())

    async def generate(self, *, project_id: str, question: str) -> Generated:
        paper_rows = await self._papers(project_id)
        paper_infos = [PaperInfo(paper_id=p.id, title=p.title) for p in paper_rows]
        resolvables = [
            ResolvablePaper(
                paper_id=p.id,
                title=p.title,
                authors=tuple(p.authors or []),
                year=p.year,
            )
            for p in paper_rows
        ]
        wants_metadata = needs_paper_metadata(question, [])
        paper_metas = [
            PaperMetaContext(
                title=p.title,
                authors=list(p.authors or []) if wants_metadata else [],
                year=p.year if wants_metadata else None,
                venue=p.venue if wants_metadata else None,
            )
            for p in paper_rows
        ]

        embedding = await self._embeddings.embed(question, task_type="RETRIEVAL_QUERY")

        # The scope ladder, minus the rung that needs a click: no "@" mention
        # can exist for a golden-set question, so this is resolver-then-global,
        # exactly what a first turn typed into an empty conversation gets.
        resolution = resolve_papers_with_evidence(
            question, resolvables, max_papers=settings.max_resolved_papers
        )
        by_id = {p.paper_id: p for p in paper_infos}
        scope_infos = [by_id[pid] for pid in resolution.paper_ids if pid in by_id]
        scope_source = "resolved" if scope_infos else "none"

        widened = False
        if scope_infos:
            widened = await self._widener.run(
                WidenerInput(query=question, mentioned_titles=[p.title for p in scope_infos])
            )
            chunks, widened = await self._chat._retrieve_mentioned_chunks(
                scope_infos, paper_infos, embedding, question, widened
            )
        else:
            async with SessionLocal() as db:
                chunks = await self._chat._retrieve_paper_chunks(
                    db, paper_infos, embedding, question
                )

        contributing = {c.paper_id for c in chunks}
        empty_titles = [p.title for p in scope_infos if p.paper_id not in contributing]

        agent_input = ChatAgentInput(
            query=question,
            prior_messages=[],
            paper_chunks=chunks,
            papers=paper_metas,
            scope_titles=[p.title for p in scope_infos],
            scope_widened=widened,
            scope_empty_titles=empty_titles,
            scope_source=scope_source if scope_infos else "mention",
        )

        parts: list[str] = []
        async for token in self._agent.stream(agent_input):
            parts.append(token)
        raw = "".join(parts)

        # Production's post-pass, in production's order. Expand first (grouped
        # markers are one-per-bracket to everything downstream), strip before
        # renumbering (a marker removed afterwards leaves a chip pointing at a
        # claim that no longer cites it and burns a number in the sequence).
        max_n = len(chunks)
        expanded = expand_grouped_citations(raw, max_n)
        attributed, misattributed = strip_misattributed_citations(
            expanded,
            chunk_papers={c.n: c.paper_id for c in chunks},
            paper_titles={p.paper_id: p.title for p in paper_infos},
        )
        clean, renumbered = renumber_citations(attributed, max_n)

        by_old = {c.n: c for c in chunks}
        citations = [
            {
                "n": new_n,
                "paper_id": by_old[old_n].paper_id,
                "title": by_old[old_n].title,
                "chunk_index": by_old[old_n].chunk_index,
            }
            for old_n, new_n in sorted(renumbered.items(), key=lambda kv: kv[1])
            if old_n in by_old
        ]

        # The judge is shown the catalog in the numbering the ANSWER uses, so
        # it can be handed the answer's markers verbatim. Chunks that survived
        # into no citation keep their original number -- they were still in
        # front of the model and can still support a claim the model forgot to
        # cite, which is exactly what `citation_coverage` measures.
        renumbered_chunks = tuple(
            ChunkContext(
                n=renumbered.get(c.n, c.n),
                paper_id=c.paper_id,
                title=c.title,
                chunk_index=c.chunk_index,
                text=c.text,
            )
            for c in chunks
        )

        return Generated(
            question=question,
            answer=clean,
            chunks=renumbered_chunks,
            citations=tuple(citations),
            scope_source=scope_source,
            scoped_titles=tuple(p.title for p in scope_infos),
            widened=widened,
            stripped_markers=len(misattributed),
        )
