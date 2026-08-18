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
from app.agents.query_reformulator import QueryReformulatorAgent, ReformulatorInput
from app.agents.scope_widener import ScopeWidenerAgent, WidenerInput
from app.core.config import settings
from app.core.logging import log
from app.db.models import Paper
from app.db.session import SessionLocal
from app.services.citation_attribution import (
    split_prose_segments,
    strip_misattributed_citations,
)
from app.services.conversation_service import ConversationService
from app.services.embedding_service import EmbeddingService
from app.services.hybrid_ranker import fuse_rrf, keep_within_rank_window
from app.services.intra_paper_ranker import keep_within_paper
from app.services.mention_ranker import apply_per_paper_floor

_HISTORY_TOP_K = 5

_CITATION_RE = re.compile(r"\[(\d+)\]")

# Words that make a question possibly about a paper's authors, year or venue.
# Deliberately over-broad: "who" and "when" fire on plenty of ordinary content
# questions, and that is the correct bias. A false positive costs 3,158 tokens
# — what every turn paid before this routing existed. A false negative makes
# the model report that a paper does not state its authors, because the block
# it was given had none.
#
# "cit" and "dat" are truncated stems, not typos. The whole words "cite" and
# "date" miss real inflections in exactly the harmful direction: "citing" and
# "dating" diverge from them one letter after the stem (a vowel change, not a
# suffix), so no boundary fix closes the gap — only a shorter stem does.
# Truncating costs more over-firing ("cit" also matches "city"/"citizen",
# "dat" also matches "data"/"database" — the latter common in these papers
# anyway) which is the harmless direction under this module's own rule.
#
# "newest"/"latest"/"oldest"/"recent"/"earliest" ask for a paper by its
# relative year without naming a year, or any word above ("which paper is
# the newest?") -- the same false-negative risk, so the same word-start-only
# anchoring applies and "recent" also catches "recently".
#
# "citation" was removed: "cit" above already matches it as a prefix, so
# keeping both listed the same signal twice, not two signals.
_METADATA_KEYWORDS = (
    "author",
    "wrote",
    "written",
    "who",
    "year",
    "when",
    "dat",
    "publish",
    "publication",
    "venue",
    "journal",
    "conference",
    "proceeding",
    "cit",
    "newest",
    "latest",
    "oldest",
    "recent",
    "earliest",
)
# Anchored at word START only, with no trailing boundary. A trailing \b would
# stop "author" matching "authors", "cite" matching "citations" and "publish"
# matching "published" — each a real metadata question falling silently into
# the false-negative case. The cost is over-firing on words that merely begin
# the same way ("whole" fires "who"), which is harmless.
_METADATA_RE = re.compile(r"\b(?:" + "|".join(_METADATA_KEYWORDS) + ")", re.IGNORECASE)

# A bare year ("Summarize the 2023 paper") names a paper by a field with no
# English synonym -- there is no word that means "year" the way "who" means
# "author", so no keyword could ever catch this. Anchored at BOTH ends,
# unlike every pattern above: a 4-digit run embedded in a longer number (an
# arXiv id, a page range, a version string) is not a year and carries no
# metadata intent, so the deliberate over-firing the rest of this module
# trades on does not apply to bare digits -- this pattern is precise on
# purpose, not broad on purpose.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _matches_keyword_or_year(text: str) -> bool:
    return bool(_METADATA_RE.search(text) or _YEAR_RE.search(text))


def needs_paper_metadata(question: str, prior_messages: list[dict]) -> bool:
    """Whether this turn should carry paper authors, year and venue.

    Those three fields are 57% of the PAPERS block's tokens (3,158 of 5,529 at
    100 papers) and exist solely to answer questions about them — the block is
    their only permitted source, and the chat prompt forbids reading them out
    of excerpts.

    Two ways a question can ask for them: an English keyword ("who wrote")
    or a bare year ("the 2023 paper"). The superlatives in
    _METADATA_KEYWORDS ("newest", "latest", ...) are a special case of the
    first: they name a paper by relative year without naming a year, or any
    other keyword, at all.

    KNOWN LIMITATION, accepted on purpose: a question that REFERENCES a
    paper's metadata without using any of these words -- "What does Kara's
    paper say...", "the ICRA paper" -- does not fire. A corpus-derived token
    check (author surnames, venue acronyms) was built and then reverted: it
    collided with ordinary language in three separate rounds -- first
    word-y venue tokens that turned out to be this domain's own subject
    vocabulary ("learning", "networks", "control"), then author surnames
    themselves (measured: 10 of 266 real surnames on the live corpus collide
    with a common English word -- "how", "park", "chen", "wang", among
    others -- and "how" alone opens a large share of all questions). The
    list of collisions grows with every paper added, silently, with nothing
    to flag when a new author erodes the saving again. It also bought less
    than it cost: the paper TARGETER, which decides retrieval scoping, only
    ever receives paper TITLES, so the system already cannot resolve
    "Kara's paper" to a paper at the retrieval layer -- naming the author in
    the answer-time block never fixed that; it only let the model repeat a
    name for chunks it had already been handed some other way. Do not
    reintroduce corpus tokens to "fix" this gap without solving that
    retrieval-layer problem first, or the same three collision classes
    return.

    A pronoun-only follow-up such as "and that one?" carries no keyword or
    year at all, so the previous USER message is consulted. One turn only:
    two turns later the intent has lapsed, and widening the window trades a
    growing number of false positives for a shrinking set of real cases.
    """
    if _matches_keyword_or_year(question):
        return True
    for message in reversed(prior_messages):
        # .get, not [] — a malformed entry (missing a key) must not raise and
        # fail the whole chat turn over a token optimisation. Not reachable
        # today (every construction site here supplies both keys), but this
        # runs on the hot path of every turn, so it degrades instead of
        # trusting the caller.
        if message.get("role") == "user":
            # Only the most recent user turn — assistant text does not carry
            # intent, and an answer that happens to mention authors must not
            # keep the full block alive.
            return _matches_keyword_or_year(message.get("content") or "")
    return False


def renumber_citations(text: str, max_n: int) -> tuple[str, dict[int, int]]:
    """Renumber an answer's citation markers to 1..N by first appearance.

    The model cites by position in the retrieval catalog it was handed, so an
    answer with four sources arrives reading "[8], [14], [21], [27]". Those
    positions are invisible to the reader and mean nothing — papers carry no
    number of their own. Renumbering by order of appearance, rather than
    ascending, is what stops the prose reading "as shown in [2] ... and [1]".

    Markers inside code are left completely alone. This is load-bearing, not
    tidiness: renumbering rewrites every in-range marker, so an unguarded pass
    would turn `arr[8]` in a snippet into `arr[3]`. The same guard fixes a
    pre-existing bug where an out-of-range `arr[99]` became
    `arr[source unavailable]` inside a fenced block.

    What counts as code — ``` and ~~~ fences, unterminated fences, inline
    spans, and the deliberate four-space-indented gap — is decided by
    `citation_attribution.segment_offsets`, which owns that guard for every
    pass that rewrites markers. Its docstring records why fences and inline
    spans are located in two passes rather than one alternation.

    Returns the rewritten text and the old→new map, ordered by new number.
    """
    # The code guard is shared with strip_misattributed_citations, never
    # duplicated: it is subtle enough that two copies will diverge, and a
    # divergence corrupts exactly the bytes the backend and frontend must
    # agree are code.
    segments = split_prose_segments(text)

    # First pass: assign numbers in order of appearance across prose only, so
    # a marker buried in a code block cannot claim a number.
    mapping: dict[int, int] = {}
    for body, is_code in segments:
        if is_code:
            continue
        for match in _CITATION_RE.finditer(body):
            n = int(match.group(1))
            if 0 < n <= max_n and n not in mapping:
                mapping[n] = len(mapping) + 1

    def _rewrite(match: re.Match[str]) -> str:
        n = int(match.group(1))
        # An out-of-range marker points at nothing, so it never earns a number.
        return f"[{mapping[n]}]" if n in mapping else "[source unavailable]"

    rewritten = "".join(
        body if is_code else _CITATION_RE.sub(_rewrite, body) for body, is_code in segments
    )
    return rewritten, mapping


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
        self._widener = ScopeWidenerAgent()
        self._chat_agent = ChatAgent()
        self._conv_svc = ConversationService()

    async def respond(
        self,
        conversation_id: str,
        user_content: str,
        mentioned_paper_ids: list[str] | None = None,
    ) -> AsyncGenerator[dict, None]:
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

                # Format prior messages (all except the user's current message)
                prior_messages = [
                    {"role": m.role, "content": m.content}
                    for m in conv.messages[:-1]  # exclude the last (just-saved user msg)
                ]

                # Authors, year and venue are 57% of the PAPERS block's tokens
                # and are only ever needed to answer a question about them.
                # Titles always ship: they are what lets the model say what is
                # in the library, and what disambiguation lists.
                wants_metadata = needs_paper_metadata(user_content, prior_messages)
                # Built inside the session: the attributes are loaded, but
                # building it here keeps it independent of session lifetime.
                paper_metas = [
                    PaperMetaContext(
                        title=p.title,
                        authors=list(p.authors or []) if wants_metadata else [],
                        year=p.year if wants_metadata else None,
                        venue=p.venue if wants_metadata else None,
                    )
                    for p in paper_rows
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

            mentioned = list(mentioned_paper_ids or [])
            scope = paper_infos
            widened = False
            mentioned_infos = []

            if query_embedding is not None:
                async with SessionLocal() as db:
                    history_hits = await self._retrieve_history(
                        db, conversation_id, query_embedding
                    )

                if paper_infos:
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

                    by_id = {p.paper_id: p for p in paper_infos}
                    mentioned_infos = [by_id[pid] for pid in mentioned if pid in by_id]

                    if mentioned_infos:
                        # The widener may only ADD the library. It has no vote
                        # on whether the named papers are retrieved.
                        widened = await self._widener.run(
                            WidenerInput(
                                query=user_content,
                                mentioned_titles=[p.title for p in mentioned_infos],
                            )
                        )
                        scope = mentioned_infos
                        async with SessionLocal() as db:
                            paper_chunks = await self._retrieve_paper_chunks(
                                db, mentioned_infos, retrieval_embedding, retrieval_query
                            )
                        paper_chunks = apply_per_paper_floor(
                            paper_chunks,
                            paper_of=lambda c: c.paper_id,
                            scope=[p.paper_id for p in mentioned_infos],
                            floor=settings.mention_per_paper_floor,
                            budget=settings.max_context_chunks,
                        )
                        if widened or not paper_chunks:
                            # Not widening on an empty scope would answer from
                            # nothing: a paper can be mentioned before its
                            # ingest finished. Answering wider than asked beats
                            # answering ungrounded.
                            widened = True
                            async with SessionLocal() as db:
                                fill = await self._retrieve_paper_chunks(
                                    db, paper_infos, retrieval_embedding, retrieval_query
                                )
                            pinned_keys = {(c.paper_id, c.chunk_index) for c in paper_chunks}
                            budget = settings.max_context_chunks - len(paper_chunks)
                            extra = [
                                c for c in fill if (c.paper_id, c.chunk_index) not in pinned_keys
                            ][:budget]
                            paper_chunks = self._renumber_chunks(paper_chunks + extra)
                            scope = paper_infos
                        else:
                            paper_chunks = self._renumber_chunks(paper_chunks)
                    else:
                        async with SessionLocal() as db:
                            paper_chunks = await self._retrieve_paper_chunks(
                                db, paper_infos, retrieval_embedding, retrieval_query
                            )

            yield {
                "event": "retrieving",
                "data": json.dumps(
                    {
                        "paper_count": len(scope),
                        "history_hits": len(history_hits),
                        "scoped": bool(mentioned),
                        # How many papers the USER named — not len(scope),
                        # which becomes the whole project once widening fires.
                        "scoped_count": len(mentioned_infos) if mentioned else 0,
                        "widened": widened,
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
                # The papers the USER named, always — never `scope`, which is
                # the whole project once widening fires. A SCOPE block listing
                # 100 titles would say nothing and cost ~2k tokens.
                scope_titles=[p.title for p in mentioned_infos],
                scope_widened=widened,
            )

            # Stream response
            full_response = []
            async for token in self._chat_agent.stream(agent_input):
                full_response.append(token)
                yield {"event": "delta", "data": json.dumps({"text": token})}

            response_text = "".join(full_response)

            # Renumber the answer's citations to 1..N by order of appearance.
            # The model cites by catalog position, which the reader never
            # sees. This also replaces out-of-range markers, so it subsumes
            # the validation pass that used to live here.
            max_n = len(paper_chunks)

            # Strip BEFORE renumbering — the order is load-bearing.
            # renumber_citations assigns 1..N by first appearance and the chip
            # list is built from `renumbered`, so a marker stripped afterwards
            # would leave a chip pointing at a claim that no longer cites it,
            # and would consume a number in the visible sequence.
            attributed_response, misattributed = strip_misattributed_citations(
                response_text,
                chunk_papers={c.n: c.paper_id for c in paper_chunks},
                paper_titles={p.paper_id: p.title for p in paper_infos},
            )
            if misattributed:
                log.info(
                    "citation_misattributed",
                    conversation_id=conversation_id,
                    stripped=len(misattributed),
                )

            clean_response, renumbered = renumber_citations(attributed_response, max_n)

            # Ordered by the NEW number so the chip row reads 1, 2, 3 left to
            # right. `renumbered` maps catalog position → new number, which is
            # what still resolves each citation back to its chunk.
            by_old = {c.n: c for c in paper_chunks}
            citations = [
                {
                    "n": new_n,
                    "paper_id": by_old[old_n].paper_id,
                    "title": by_old[old_n].title,
                    "chunk_index": by_old[old_n].chunk_index,
                    "snippet": by_old[old_n].text[:200],
                }
                for old_n, new_n in sorted(renumbered.items(), key=lambda kv: kv[1])
                if old_n in by_old
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
        query_embedding: list[float],
        query_text: str,
    ) -> list[ChunkContext]:
        """Retrieve the nearest chunks from `paper_infos`, ranked by distance.

        `paper_infos` is the retrieval SCOPE the caller decided on -- the
        whole project's papers by default, or the single paper the targeter
        picked. This method has no opinion on which; it just ranks whatever
        scope it is given.

        ONE query, and no per-paper ceiling. Cosine similarity spreads the
        result across papers by itself when scope is the whole library:
        measured on a 100-paper library, a global top-40 spanned 14-25
        distinct papers and no paper ever took more than 11 of the 40 slots
        — and that case was the correct paper going deep on a question about
        its own contents.

        A ceiling could only ever REDUCE the right paper's share, never raise
        it. On the EA-operators question the target paper earns 6 chunks by
        distance, where the old unallocated fallback would have capped it at 2.

        The budget must stay a function of the CONTEXT WINDOW, never of
        library size. Per-paper allocation made it the latter, and a 100-paper
        project pulled 191 chunks (~118.5k tokens) until every turn died on
        `context_length_exceeded`.

        SINGLE-PAPER SCOPE IS DIFFERENT, and it is detected here rather than
        passed in: one paper in `paper_infos` means the user already named the
        paper (the targeter picked it, or the project holds only that one), so
        an absolute cosine cutoff tuned for beating OTHER papers is the wrong
        instrument. Two changes apply, and only together:
          - the SQL cutoff becomes `intra_paper_ceiling` (looser, a noise
            floor), because `similarity_threshold` silently drops answering
            chunks -- measured, one sat at 0.7293 against 0.75, and the drop
            is invisible because 13 other chunks still return, so respond()'s
            empty-result fallback never fires;
          - a relative cut keeps only chunks within `intra_paper_delta` of
            this paper's own nearest chunk, because the looser ceiling alone
            keeps far more of a targeted paper than a question needs -- at
            delta 0.20, four cases (marl-security-attacks, deadly-triad,
            lazy-agents-reward, hnpfl-fair-comparison) keep the entire
            60-chunk max_context_chunks budget, budget-bound before the delta
            even bites (evals/retrieval/README.md, "Measured — 2026-08-12").
        A relative cut alone would be no guard at all: an off-topic question's
        distances are compressed, so the delta alone -- measured at delta
        0.25, the then-current value -- kept 24/24, 44/44 and 64/64 chunks of
        the nearest paper (see the CEILING measurement on intra_paper_ceiling
        in app/core/config.py).

        HYBRID (settings.hybrid_retrieval, default on): the query grows a
        second, LEXICAL arm and the two are fused by weighted RRF. Admission
        is DISJUNCTIVE -- a chunk qualifies by passing the dense distance gate
        OR by landing in the sparse arm's top-N -- because the failure this
        fixes is a chunk the dense gate rejects. Gating on dense distance
        before fusing would make the sparse arm decorative.

        The cut changes with it. `intra_paper_delta` is a distance-space rule
        (`best + delta`) and has no meaning against a fused rank, so under
        hybrid the single-paper cut is `intra_paper_rank_window`. The delta
        survives for `hybrid_retrieval=False`, which reproduces the
        pre-2026-08-15 path exactly and is both the eval harness's control arm
        and production's kill switch.

        A stopword-only question degrades for free: websearch_to_tsquery
        returns an empty query, `@@` matches nothing, the sparse arm is empty,
        and RRF collapses to the dense ordering.
        """
        if not paper_infos:
            return []
        # See the docstring: scope size, not a flag, decides the policy.
        single_paper = len(paper_infos) == 1
        threshold = settings.intra_paper_ceiling if single_paper else settings.similarity_threshold
        qvec = _vec_str(query_embedding)
        paper_title_map = {p.paper_id: p.title for p in paper_infos}
        ids = json.dumps([p.paper_id for p in paper_infos])

        if not settings.hybrid_retrieval:
            rows = await self._dense_only_rows(db, ids, qvec, threshold)
            # Re-applied in Python on purpose. LIMIT bounds what crosses the wire;
            # this bounds what reaches the model. The budget is the single
            # invariant that keeps chat working at any library size, so it must
            # not depend on a SQL clause surviving a future edit to the query.
            rows = rows[: settings.max_context_chunks]
            if single_paper:
                rows = rows[
                    : keep_within_paper(
                        [row.distance for row in rows], delta=settings.intra_paper_delta
                    )
                ]
            return self._to_chunk_contexts(rows, paper_title_map)

        rows = await self._hybrid_rows(db, ids, qvec, query_text, threshold)
        by_id = {row.id: row for row in rows}
        dense_ranked = [
            row.id
            for row in sorted((r for r in rows if r.d_rank is not None), key=lambda r: r.d_rank)
        ]
        sparse_ranked = [
            row.id
            for row in sorted((r for r in rows if r.s_rank is not None), key=lambda r: r.s_rank)
        ]
        fused = fuse_rrf(
            dense_ranked,
            sparse_ranked,
            w_dense=settings.hybrid_dense_weight,
            w_sparse=settings.hybrid_sparse_weight,
            k=settings.hybrid_rrf_k,
        )
        # Budget FIRST, cut second -- same ordering the dense path documents
        # above. LIMIT bounds what crosses the wire; this bounds what reaches
        # the model, and the cut may only shrink what the budget bounded.
        fused = fused[: settings.max_context_chunks]
        if single_paper:
            fused = fused[
                : keep_within_rank_window(
                    [score for _, score in fused], window=settings.intra_paper_rank_window
                )
            ]
        return self._to_chunk_contexts([by_id[key] for key, _ in fused], paper_title_map)

    def _renumber_chunks(self, chunks: list[ChunkContext]) -> list[ChunkContext]:
        """Re-number after the floor and the fill have reordered the list.

        `n` is the marker the model cites, so it must follow the order the
        model is shown — same invariant `_to_chunk_contexts` documents.
        """
        return [chunk.model_copy(update={"n": i}) for i, chunk in enumerate(chunks, start=1)]

    def _to_chunk_contexts(self, rows, paper_title_map: dict[str, str]) -> list[ChunkContext]:
        """Number citations contiguously over the FINAL order.

        `n` is the marker the model cites, so it must follow the order the
        model sees -- after fusion and after every cut, never the row order.
        """
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

    async def _dense_only_rows(self, db: AsyncSession, ids: str, qvec: str, threshold: float):
        # `paper_chunk_embeddings` is global, so this query MUST be scoped to
        # the project. The ids ride in as one jsonb param rather than an IN
        # list: 100 papers would otherwise need 100 bind params, and asyncpg
        # array binding through text() needs casts that differ per driver.
        sql = text("""
            WITH scope AS (
                SELECT value AS paper_id
                FROM jsonb_array_elements_text(CAST(:ids AS jsonb))
            )
            SELECT c.id, c.paper_id, c.chunk_index, c.text,
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
                "ids": ids,
                "model": settings.embedding_model,
                "threshold": threshold,
                "max_chunks": settings.max_context_chunks,
            },
        )
        return result.fetchall()

    async def _hybrid_rows(
        self, db: AsyncSession, ids: str, qvec: str, qtext: str, threshold: float
    ):
        """Both arms in ONE round trip, each carrying its own rank.

        The dense arm keeps the absolute distance gate exactly as tuned. The
        sparse arm's admission is its top-N (`hybrid_sparse_pool`), because
        `ts_rank_cd` has no cross-query-comparable magnitude to threshold on.
        The FULL OUTER JOIN is what makes admission disjunctive: a chunk in
        either arm reaches Python, with a NULL rank for the arm that missed
        it.

        Ranking stops at the ranks. Fusion, the budget and the cut are Python,
        so `evals/retrieval/run_eval.py --hybrid` can import and measure the
        exact policy that ships.
        """
        sql = text("""
            WITH scope AS (
                SELECT value AS paper_id
                FROM jsonb_array_elements_text(CAST(:ids AS jsonb))
            ),
            q AS (
                SELECT websearch_to_tsquery('english', :qtext) AS tsq
            ),
            dense AS (
                SELECT c.id, c.paper_id, c.chunk_index, c.text,
                       (c.embedding <=> CAST(:qvec AS vector)) AS distance,
                       ROW_NUMBER() OVER (
                           ORDER BY c.embedding <=> CAST(:qvec AS vector)
                       ) AS d_rank
                FROM paper_chunk_embeddings c
                JOIN scope s ON s.paper_id = c.paper_id
                WHERE c.model = :model
                  AND (c.embedding <=> CAST(:qvec AS vector)) < :threshold
                ORDER BY distance ASC
                LIMIT :dense_pool
            ),
            sparse AS (
                SELECT c.id, c.paper_id, c.chunk_index, c.text,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank_cd(c.tsv, q.tsq) DESC, c.id
                       ) AS s_rank
                FROM paper_chunk_embeddings c
                JOIN scope s ON s.paper_id = c.paper_id
                CROSS JOIN q
                WHERE c.model = :model
                  AND c.tsv @@ q.tsq
                ORDER BY ts_rank_cd(c.tsv, q.tsq) DESC, c.id
                LIMIT :sparse_pool
            )
            SELECT COALESCE(d.id, sp.id)                   AS id,
                   COALESCE(d.paper_id, sp.paper_id)       AS paper_id,
                   COALESCE(d.chunk_index, sp.chunk_index) AS chunk_index,
                   COALESCE(d.text, sp.text)               AS text,
                   d.distance                              AS distance,
                   d.d_rank                                AS d_rank,
                   sp.s_rank                                AS s_rank
            FROM dense d
            FULL OUTER JOIN sparse sp ON sp.id = d.id
        """)
        result = await db.execute(
            sql,
            {
                "qvec": qvec,
                "qtext": qtext,
                "ids": ids,
                "model": settings.embedding_model,
                "threshold": threshold,
                "dense_pool": settings.hybrid_dense_pool,
                "sparse_pool": settings.hybrid_sparse_pool,
            },
        )
        return result.fetchall()
