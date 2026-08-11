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


# A fence opens with ``` or ~~~ — both are valid markdown fences, and remark
# (the frontend's markdown renderer) treats either as <pre><code>. The
# backreference (\1) means a fence can only be closed by the SAME delimiter
# it opened with — a ``` fence is never closed by ~~~ or vice versa. A fence
# with no matching closing delimiter runs to the end of the text rather than
# falling through to be reinterpreted as (part of) an inline span. Fences are
# located in a pass of their own, before inline spans are considered at all,
# so a stray or unpaired backtick elsewhere in the answer can never pair
# across a fence delimiter. See renumber_citations' docstring for why a
# single combined pattern got this wrong.
_FENCE_RE = re.compile(r"(```|~~~).*?(?:\1|\Z)", re.DOTALL)

# Inline spans are matched only within the prose _FENCE_RE leaves behind, so
# a backtick bordering a fence can no longer be mistaken for the other half
# of an inline span.
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# Known gap, left deliberately: a four-space indented run is markdown code
# too (remark renders it as <pre><code>, same as a fence), but this guard
# does not detect it. Whether an indented run is code or list-item
# continuation content depends on the enclosing list's nesting column, which
# needs list-context state this function does not track — and this chat's
# system prompt asks for "-" bullets and fenced code, not indented blocks, so
# nested-bullet content is the routine case and a genuine indented code block
# is not. Treating indentation as code by itself would risk the opposite,
# worse failure: a nested bullet's citation silently skipped from the numbered
# sequence ([1], [3], no [2]) rather than merely mis-renumbered. See
# test_an_indented_code_block_is_a_documented_gap_not_detected_as_code for the
# accepted current behaviour.


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

    Fences and inline spans are found in two separate passes rather than one
    combined pattern. A single alternation tried left to right lets an
    unterminated fence fall through to the inline alternative — consuming two
    of its three backticks as an empty span — and lets a stray backtick
    earlier in the answer pair with a fence's own opening backtick; both leak
    a fenced marker out into renumbering. Locating fences first, over the
    whole text, with "no closing ``` " meaning "runs to end of text" rather
    than "not a fence", removes both failure modes: fence boundaries never
    depend on where a stray backtick happens to sit, and an answer truncated
    mid-snippet — an observed occurrence, not a hypothetical: a chat reply
    hit `finish_reason=length` mid-sentence on 2026-08-10 — still treats
    everything after the opening fence as code.

    Markdown has more than one code form, and this guard has to agree with
    whichever ones the client also treats as code — a form the backend
    renumbers inside but the frontend renders as <pre><code> corrupts
    exactly the bytes the two sides agree are code. `_FENCE_RE` accordingly
    accepts ~~~ fences as well as ``` ones. Four-space indented blocks are
    the one markdown code form this guard still does not detect — see the
    comment above `_FENCE_RE` for why that gap is deliberate, not an
    oversight.

    Returns the rewritten text and the old→new map, ordered by new number.
    """
    # Stage 1: split the whole text on fenced blocks. An unterminated fence
    # consumes to the end of the text instead of un-matching.
    segments: list[tuple[str, bool]] = []  # (text, is_code)
    cursor = 0
    for match in _FENCE_RE.finditer(text):
        if match.start() > cursor:
            segments.append((text[cursor : match.start()], False))
        segments.append((match.group(0), True))
        cursor = match.end()
    segments.append((text[cursor:], False))

    # Stage 2: within what Stage 1 left as prose, split on inline spans. Code
    # segments pass through untouched — a fence is never re-examined here, so
    # a backtick bordering one cannot pair across the boundary.
    with_inline: list[tuple[str, bool]] = []
    for body, is_code in segments:
        if is_code:
            with_inline.append((body, True))
            continue
        inner_cursor = 0
        for match in _INLINE_CODE_RE.finditer(body):
            if match.start() > inner_cursor:
                with_inline.append((body[inner_cursor : match.start()], False))
            with_inline.append((match.group(0), True))
            inner_cursor = match.end()
        with_inline.append((body[inner_cursor:], False))
    segments = with_inline

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
            # Defaults to the whole project; narrowed below only when
            # targeting picks one paper. Initialised here, unconditionally,
            # so it is always defined at the 'retrieving' event below even
            # when embedding failed or the project has no papers.
            scope = paper_infos

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

                    # Scope retrieval to one paper when the question might be
                    # about one paper. Skipped for single-paper projects: the
                    # scope would be that one paper whether the targeter names
                    # it or answers None, so targeting there is a guaranteed
                    # no-op that still costs a full extra LLM call every turn
                    # -- and "upload one PDF and ask about it" is the common
                    # case. Also skipped when the whole library already fits
                    # the context budget; that second skip is a COST decision,
                    # not a correctness one -- with similarity_threshold in
                    # play, scoping to one paper can still change which chunks
                    # come back even under budget (a threshold-passing chunk
                    # from another paper is not available once scoped). Small
                    # projects deliberately keep that original misattribution
                    # risk in exchange for skipping this LLM call.
                    if (
                        len(paper_infos) > 1
                        and total_chunks > settings.max_context_chunks
                        and candidates
                    ):
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
                            log.info(
                                "paper_targeted",
                                conversation_id=conversation_id,
                                paper_id=target_id,
                                candidates=len(candidates),
                            )

                    async with SessionLocal() as db:
                        paper_chunks = await self._retrieve_paper_chunks(
                            db, scope, retrieval_embedding
                        )

                    if scope is not paper_infos and not paper_chunks:
                        # A targeted paper whose every chunk sits at or beyond
                        # similarity_threshold retrieves nothing, and
                        # chat_agent then answers ungrounded -- a worse
                        # failure than the misattribution this feature fixes.
                        # Re-querying the untargeted scope keeps the answer
                        # grounded, same as if targeting had never fired.
                        async with SessionLocal() as db:
                            paper_chunks = await self._retrieve_paper_chunks(
                                db, paper_infos, retrieval_embedding
                            )
                        scope = paper_infos

            yield {
                "event": "retrieving",
                "data": json.dumps(
                    {
                        "paper_count": len(scope),
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

            # Renumber the answer's citations to 1..N by order of appearance.
            # The model cites by catalog position, which the reader never
            # sees. This also replaces out-of-range markers, so it subsumes
            # the validation pass that used to live here.
            max_n = len(paper_chunks)
            clean_response, renumbered = renumber_citations(response_text, max_n)

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
        if len(paper_infos) <= 1:
            # A single-paper project has nothing to disambiguate: the
            # candidate list would be that one paper regardless of what this
            # query returns, so skip it and its SQL round trip entirely.
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
