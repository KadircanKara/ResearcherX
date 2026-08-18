"""ChatService integration test — all external calls mocked."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChatConversation, ChatMessage, Project, ProjectMember, User
from app.db.seed import seed_users


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


@pytest_asyncio.fixture
async def you(db_session: AsyncSession, seeded):
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Chat Svc Test", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest_asyncio.fixture
async def conversation_with_message(db_session: AsyncSession, project: Project, you: User):
    conv = ChatConversation(project_id=project.id, title="Test conv", created_by=you.id)
    db_session.add(conv)
    await db_session.flush()
    msg = ChatMessage(conversation_id=conv.id, role="user", content="Test question")
    db_session.add(msg)
    await db_session.commit()
    await db_session.refresh(conv)
    return conv


async def test_respond_yields_events(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    from app.services.chat_service import ChatService

    conv = conversation_with_message

    async def fake_stream(*args, **kwargs):
        yield "answer token "
        yield "two"

    svc = ChatService()

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._reformulator, "run", AsyncMock(return_value="test question expanded")),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[])),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(
            svc._conv_svc,
            "save_message",
            AsyncMock(
                return_value=ChatMessage(
                    conversation_id=conv.id,
                    role="assistant",
                    content="answer token two",
                    citations=[],
                )
            ),
        ),
    ):
        events = []
        async for event in svc.respond(conv.id, "Test question"):
            events.append(event)

    event_types = [e["event"] for e in events]
    assert "thinking" in event_types
    assert "retrieving" in event_types
    assert "delta" in event_types
    assert "done" in event_types


def _mock_db_returning_no_rows() -> MagicMock:
    """A fake AsyncSession whose execute() returns an empty result set.

    Real pgvector SQL (`<=>`, `CAST(... AS vector)`) can't run against the
    sqlite test DB, so these tests mock the session at the execute() level
    and inspect the params it was called with instead of the result.
    """
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


async def test_retrieve_history_uses_settings_similarity_threshold():
    """The threshold must come from settings (live-tunable), not be baked
    into the query — this is what makes it re-tunable per embedding model
    without a code change (see config.py)."""
    from app.services.chat_service import ChatService

    svc = ChatService()
    mock_db = _mock_db_returning_no_rows()

    with patch.object(settings, "similarity_threshold", 0.42):
        await svc._retrieve_history(mock_db, "conv-1", [0.0] * 768)

    _, params = mock_db.execute.call_args.args
    assert params["threshold"] == 0.42


async def test_retrieve_paper_chunks_uses_settings_similarity_threshold():
    """Same wiring check as above, for the per-paper chunk retrieval query."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning_no_rows()
    # TWO papers: with one paper in scope this query now binds
    # intra_paper_ceiling instead — see the single-paper tests below.
    papers = [PaperInfo(paper_id="p1", title="Test Paper"), PaperInfo(paper_id="p2", title="B")]

    with patch.object(settings, "similarity_threshold", 0.42):
        await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    _, params = mock_db.execute.call_args.args
    assert params["threshold"] == 0.42


def _mock_db_returning(n_rows: int) -> MagicMock:
    """Fake AsyncSession returning `n_rows` chunk rows, nearest first.

    Deliberately ignores any LIMIT in the SQL -- that is the point: it proves
    the service bounds its own output rather than trusting the database to.

    `d_rank`/`s_rank` are what the hybrid query's two arms emit. Here every
    row is dense-ranked and none is sparse-ranked, so fusion reproduces the
    distance order and these tests keep measuring what they measured before.
    """
    rows = [
        MagicMock(
            id=f"c{i}",
            paper_id=f"p{i % 100}",
            chunk_index=i,
            text=f"chunk {i}",
            distance=0.1 + i * 0.0001,
            d_rank=i + 1,
            s_rank=None,
        )
        for i in range(n_rows)
    ]
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


async def test_retrieve_paper_chunks_issues_one_query_for_the_whole_library():
    """One global query, not one per paper.

    The per-paper loop cost 100 sequential round trips on a 100-paper library
    and, worse, made the retrieved total scale with library size instead of
    with the context budget.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(0)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}") for i in range(100)]

    await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    assert mock_db.execute.await_count == 1


async def test_retrieve_paper_chunks_caps_total_at_settings_max_context_chunks():
    """The context budget is a hard ceiling, independent of library size.

    Without it, 100 papers × the k=2 fallback produced 191 chunks ≈ 118.5k
    tokens and every chat turn died on `context_length_exceeded`.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(500)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}") for i in range(100)]

    with patch.object(settings, "max_context_chunks", 40):
        chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    assert len(chunks) == 40


async def test_retrieve_paper_chunks_scopes_to_this_projects_papers():
    """paper_chunk_embeddings is global; the query must be scoped.

    The old per-paper `alloc` CTE did this implicitly by joining on the
    allocation map. With ceilings gone there is no alloc CTE, so scoping is
    now explicit and load-bearing — without it a project's chat would
    retrieve other projects' chunks.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(0)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}") for i in range(3)]

    await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    _, params = mock_db.execute.call_args.args
    assert json.loads(params["ids"]) == ["p0", "p1", "p2"]


def _hybrid_db(rows: list[dict]) -> MagicMock:
    """Fake AsyncSession returning explicitly-ranked hybrid rows."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [MagicMock(**row) for row in rows]
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


async def test_hybrid_query_binds_the_question_text_for_the_sparse_arm():
    """The sparse arm needs the words, not the vector. Passing only the
    embedding is what made this retrieval dense-only in the first place."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(0)
    papers = [PaperInfo(paper_id="p1", title="A"), PaperInfo(paper_id="p2", title="B")]

    await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "what reward function")

    _, params = mock_db.execute.call_args.args
    assert params["qtext"] == "what reward function"


async def test_hybrid_binds_both_pool_sizes():
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(0)
    papers = [PaperInfo(paper_id="p1", title="A"), PaperInfo(paper_id="p2", title="B")]

    with (
        patch.object(settings, "hybrid_dense_pool", 111),
        patch.object(settings, "hybrid_sparse_pool", 22),
    ):
        await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    _, params = mock_db.execute.call_args.args
    assert params["dense_pool"] == 111
    assert params["sparse_pool"] == 22


async def test_a_sparse_only_chunk_can_outrank_a_dense_chunk():
    """The wiring, not the arithmetic -- `fuse_rrf` itself is unit-tested in
    test_hybrid_ranker.py. Production only ever hands the fusion CONTIGUOUS
    per-arm ranks: the SQL's ROW_NUMBER() OVER (...) emits 1..N with no gaps,
    and the FULL OUTER JOIN carries every row of both arms back. This fixture
    mirrors that shape -- 100 contiguous dense rows (ranks 1..100) plus one
    sparse-only row (absent from the dense arm entirely, the distance gate
    rejected it) at sparse rank 1.

    At the default 70/30 weights and k=30 (the shipped default, not the
    textbook k=60), RRF(w=0.3, rank=1) = 0.3/31 ~= 0.00968 exceeds
    RRF(w=0.7, rank=d) once d > 42.33, so the sparse-only chunk outranks
    dense ranks 43-100.

    Asserted by POSITION, not membership: this test uses multi-paper scope
    (two papers) with `max_context_chunks` patched to 200, so with 101 rows
    total every row is returned regardless of fusion order and a bare
    membership check (`"lexical" in [...]`) would be a tautology that cannot
    fail for the reason the test name claims. Asserting that "lexical" sits
    ahead of the dense rank-100 row is what actually exercises fusion order.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    dense_rows = [
        {
            "id": f"d{i}",
            "paper_id": "p1",
            "chunk_index": i,
            "text": f"dense {i}",
            "distance": 0.1 + i * 0.001,
            "d_rank": i,
            "s_rank": None,
        }
        for i in range(1, 101)
    ]
    sparse_row = {
        "id": "sparse-only",
        "paper_id": "p1",
        "chunk_index": 999,
        "text": "lexical",
        "distance": None,
        "d_rank": None,
        "s_rank": 1,
    }
    mock_db = _hybrid_db([*dense_rows, sparse_row])
    papers = [PaperInfo(paper_id="p1", title="A"), PaperInfo(paper_id="p2", title="B")]

    with patch.object(settings, "max_context_chunks", 200):
        chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "reward table")

    texts = [c.text for c in chunks]
    assert texts.index("lexical") < texts.index("dense 100")


async def test_a_both_arms_chunk_outranks_a_dense_only_top_hit():
    """The join's central property: one row can carry BOTH ranks, and both
    RRF terms are summed. At the shipped k=30 (not the textbook k=60): 'c1'
    is dense rank 3 AND sparse rank 1 (score = 0.7/33 + 0.3/31 ~= 0.02121 +
    0.00968 = 0.03089); 'c2' is the dense arm's OWN rank-1 hit and
    sparse-absent (score = 0.7/31 ~= 0.02258). c1's combined score beats
    c2's dense-only score even though c2 outranks c1 in the dense arm alone
    -- proving both terms are actually summed, not just the higher one
    kept."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _hybrid_db(
        [
            {
                "id": "c1",
                "paper_id": "p1",
                "chunk_index": 0,
                "text": "both arms",
                "distance": 0.42,
                "d_rank": 3,
                "s_rank": 1,
            },
            {
                "id": "c2",
                "paper_id": "p1",
                "chunk_index": 1,
                "text": "dense only",
                "distance": 0.30,
                "d_rank": 1,
                "s_rank": None,
            },
        ]
    )
    papers = [PaperInfo(paper_id="p1", title="A"), PaperInfo(paper_id="p2", title="B")]

    chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    assert [c.text for c in chunks] == ["both arms", "dense only"]


async def test_citations_are_numbered_contiguously_after_fusion():
    """`n` is the citation marker the model cites. Fusion reorders rows, so
    the numbering has to follow the fused order, not the row order. Pinned
    to (n, text) pairs -- a bare `[c.n for c in chunks] == [1, 2]` is just
    `enumerate(rows, 1)` and would pass under any fusion order, including
    none (see test_single_paper_scope_applies_the_delta_cut for the same
    failure mode)."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _hybrid_db(
        [
            {
                "id": "c1",
                "paper_id": "p1",
                "chunk_index": 0,
                "text": "dense",
                "distance": 0.4,
                "d_rank": 1,
                "s_rank": None,
            },
            {
                "id": "c2",
                "paper_id": "p1",
                "chunk_index": 1,
                "text": "lexical",
                "distance": None,
                "d_rank": None,
                "s_rank": 1,
            },
        ]
    )
    papers = [PaperInfo(paper_id="p1", title="A"), PaperInfo(paper_id="p2", title="B")]

    chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    # Both at position 1 in their own arm: dense's 0.7 weight beats sparse's
    # 0.3 weight at the same rank, so "dense" (c1) fuses ahead of "lexical"
    # (c2). This pins WHICH row got WHICH number, not just the count.
    assert [(c.n, c.text) for c in chunks] == [(1, "dense"), (2, "lexical")]


async def test_single_paper_scope_applies_the_rank_window():
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(50)
    papers = [PaperInfo(paper_id="p1", title="Only Paper")]

    with patch.object(settings, "intra_paper_rank_window", 7):
        chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    assert len(chunks) == 7


async def test_multi_paper_scope_does_not_apply_the_rank_window():
    """The window is single-paper precision, not a global budget. Applying it
    to library-wide scope would cut the budget for every question."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(50)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"P{i}") for i in range(5)]

    with (
        patch.object(settings, "intra_paper_rank_window", 7),
        patch.object(settings, "max_context_chunks", 40),
    ):
        chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    assert len(chunks) == 40


async def test_the_budget_is_applied_before_the_rank_window():
    """NOT a proof of ordering. Both cuts are prefix slices, and prefix
    slicing is commutative -- `seq[:a][:b] == seq[:b][:a]` whenever both
    bounds are within range, which they are here. Swapping the two lines in
    `_retrieve_paper_chunks` would leave this test green. It exists only to
    pin the budget's own value (10, not 999) under a scope that also has a
    rank window in play; the ordering itself is structural (see the
    docstring and the comment above the budget slice in
    `_retrieve_paper_chunks`) and is not, and cannot be, verified by any
    test built from two prefix slices."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(500)
    papers = [PaperInfo(paper_id="p1", title="Only Paper")]

    with (
        patch.object(settings, "max_context_chunks", 10),
        patch.object(settings, "intra_paper_rank_window", 999),
    ):
        chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    assert len(chunks) == 10


async def test_hybrid_disabled_falls_back_to_the_dense_only_query():
    """The kill switch. With hybrid off the query must not mention tsv at
    all -- a deployment that has not run the migration still has to work."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(0)
    papers = [PaperInfo(paper_id="p1", title="A"), PaperInfo(paper_id="p2", title="B")]

    with patch.object(settings, "hybrid_retrieval", False):
        await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    sql, params = mock_db.execute.call_args.args
    assert "tsv" not in str(sql)
    assert "qtext" not in params


async def test_hybrid_disabled_still_applies_the_distance_delta_cut():
    """intra_paper_delta is not dead code: it governs the cut whenever the
    kill switch is on."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(50)
    papers = [PaperInfo(paper_id="p1", title="Only Paper")]

    with (
        patch.object(settings, "hybrid_retrieval", False),
        patch.object(settings, "intra_paper_delta", 0.0),
    ):
        chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    # _mock_db_returning spaces distances 0.0001 apart, so delta 0.0 keeps
    # only the nearest chunk.
    assert len(chunks) == 1


async def test_respond_skips_reformulation_on_a_first_turn(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """With no prior conversation there is nothing to resolve, so the call
    buys nothing. Asserting ZERO calls is the point: this is what keeps the
    common case at one LLM call per turn instead of two."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    db_session.add(Paper(project_id=project.id, title="Paper A", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    reformulate = AsyncMock(return_value="should not be called")

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._reformulator, "run", reformulate),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[])),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    reformulate.assert_not_awaited()


async def test_respond_skips_reformulation_on_a_first_turn_despite_a_history_hit(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """Guards the self-match race (see the gate's comment in chat_service.py):
    conversation_service.save_message() fires an asyncio.create_task to embed
    the user's own message BEFORE respond() runs, so on a genuine first turn
    _retrieve_history can legitimately come back with that same message as a
    "history hit" (it self-matches at distance ~0). If the gate were
    `if prior_messages + history_hits:` instead of `if prior_messages:`, this
    hit alone would trip it and run the reformulator on a first turn. Only
    prior_messages is a reliable first-turn signal, so asserting ZERO calls
    here -- with a non-empty history hit forced in -- is the point."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    db_session.add(Paper(project_id=project.id, title="Paper A", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    reformulate = AsyncMock(return_value="should not be called")
    # Simulates the self-match race: the just-saved current-turn user message
    # comes back from _retrieve_history as if it were a "history hit".
    self_match_hit = [{"role": "user", "content": "Test question"}]

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=self_match_hit)),
        patch.object(svc._reformulator, "run", reformulate),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[])),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    reformulate.assert_not_awaited()


async def test_respond_retrieves_with_the_reformulated_query(
    db_session: AsyncSession, project: Project, conversation_with_message, you: User
):
    """The rewritten query must be what gets embedded for retrieval —
    otherwise the reformulation call is paid for and thrown away."""
    from app.db.models import ChatMessage, Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    db_session.add(Paper(project_id=project.id, title="Paper A", source="manual"))
    # A prior turn, so reformulation runs at all.
    db_session.add(ChatMessage(conversation_id=conv.id, role="assistant", content="Earlier answer"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    embed = AsyncMock(return_value=[0.0] * 768)

    with (
        patch.object(svc._embedding_svc, "embed", embed),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(
            svc._reformulator, "run", AsyncMock(return_value="rewritten standalone query")
        ),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[])),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    embedded = [c.args[0] for c in embed.await_args_list]
    assert "rewritten standalone query" in embedded


async def test_retrieve_paper_chunks_numbers_citations_contiguously_after_capping():
    """Citation markers must be 1..N over the chunks actually sent.

    `chat_agent` cites by position, so a gap or a number past the end of the
    list would point at a source the model was never given.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning(500)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}") for i in range(100)]

    with patch.object(settings, "max_context_chunks", 40):
        chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    assert [c.n for c in chunks] == list(range(1, 41))


async def test_respond_persists_citations_renumbered_from_one(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The persisted answer and its citation payload must agree on the new
    numbering. The client refetches the conversation on `done` and renders
    what was saved, so a mismatch here is what the reader sees.
    """
    from app.agents.chat_agent import ChunkContext
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    db_session.add(Paper(project_id=project.id, title="Paper A", source="manual"))
    await db_session.commit()

    # Retrieval handed the model a catalog of three chunks; it cited the third
    # and then the first, in that order.
    chunks = [
        ChunkContext(n=1, paper_id="pA", title="Paper A", chunk_index=0, text="first"),
        ChunkContext(n=2, paper_id="pA", title="Paper A", chunk_index=1, text="second"),
        ChunkContext(n=3, paper_id="pA", title="Paper A", chunk_index=2, text="third"),
    ]

    async def fake_stream(*args, **kwargs):
        yield "Coverage [3] and then connectivity [1]."

    svc = ChatService()
    save = AsyncMock()

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=chunks)),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", save),
    ):
        events = []
        async for event in svc.respond(conv.id, "Test question"):
            events.append(event)

    _, _, _, content, citations = save.await_args.args
    assert content == "Coverage [1] and then connectivity [2]."
    # Renumbered, ordered by the new number, and each still resolving to the
    # chunk the model actually cited: [1] is catalog entry 3, [2] is entry 1.
    assert [c["n"] for c in citations] == [1, 2]
    assert [c["chunk_index"] for c in citations] == [2, 0]

    done = [e for e in events if e["event"] == "done"][-1]
    assert json.loads(done["data"])["citations"] == citations


async def test_respond_sends_paper_metadata_on_a_metadata_question(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """A routing decision that never reaches the agent is the failure that
    matters, so this asserts on ChatAgentInput rather than on the detector."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    db_session.add(
        Paper(
            project_id=project.id, title="Paper A", authors=["Jane Doe"], year=2024, source="manual"
        )
    )
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    stream = MagicMock(return_value=fake_stream())

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[])),
        patch.object(svc._chat_agent, "stream", stream),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Who wrote Paper A?"):
            pass

    sent = stream.call_args.args[0]
    assert sent.papers[0].authors == ["Jane Doe"]
    assert sent.papers[0].year == 2024


async def test_respond_sends_titles_only_on_a_content_question(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The 3,158-token saving. Titles still ship — they are what lets the model
    say what is in the library — but the three expensive fields do not."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    db_session.add(
        Paper(
            project_id=project.id, title="Paper A", authors=["Jane Doe"], year=2024, source="manual"
        )
    )
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    stream = MagicMock(return_value=fake_stream())

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[])),
        patch.object(svc._chat_agent, "stream", stream),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "What reward function does it use?"):
            pass

    sent = stream.call_args.args[0]
    assert sent.papers[0].title == "Paper A"
    assert sent.papers[0].authors == []
    assert sent.papers[0].year is None
    assert sent.papers[0].venue is None


async def test_single_paper_scope_binds_the_intra_paper_ceiling():
    """One paper in scope means the user already named it, so the global
    cutoff is the wrong instrument — the measured ground-control-station
    answer chunk sat at 0.7293 against a 0.75 threshold, 0.02 from being
    dropped, and the drop is silent because 13 other chunks still come back
    so the empty-result fallback never fires."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning_no_rows()
    paper = PaperInfo(paper_id="p1", title="Only Paper")

    with (
        patch.object(settings, "intra_paper_ceiling", 0.85),
        patch.object(settings, "similarity_threshold", 0.75),
    ):
        await svc._retrieve_paper_chunks(mock_db, [paper], [0.0] * 768, "q")

    _, params = mock_db.execute.call_args.args
    assert params["threshold"] == 0.85


async def test_single_paper_scope_applies_the_delta_cut():
    """Rows beyond best + delta are dropped even though SQL returned them —
    SQL only enforces the looser ceiling."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    rows = [
        MagicMock(paper_id="p1", chunk_index=i, text=f"chunk {i}", distance=d)
        for i, d in enumerate([0.50, 0.60, 0.74, 0.80])
    ]
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    paper = PaperInfo(paper_id="p1", title="Only Paper")

    with (
        patch.object(settings, "hybrid_retrieval", False),
        patch.object(settings, "intra_paper_delta", 0.25),
    ):
        chunks = await svc._retrieve_paper_chunks(mock_db, [paper], [0.0] * 768, "q")

    # best 0.50 + 0.25 = 0.75 -> the 0.80 row is cut, the 0.74 row survives
    assert len(chunks) == 3
    assert [c.n for c in chunks] == [1, 2, 3]
    # `n` alone is tautological here (it's just enumerate(rows, 1) over
    # whatever length survives), so it would pass even if the cut kept the
    # wrong end of the sorted list. chunk_index pins down WHICH rows —
    # 0, 1, 2 (distances 0.50, 0.60, 0.74) — survived, not just how many.
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


async def test_multi_paper_scope_applies_no_delta_cut():
    """Across papers a relative cut is meaningless: the 'best' chunk belongs
    to one paper and would gate every other paper's chunks by proximity to
    it."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    rows = [
        MagicMock(paper_id=f"p{i}", chunk_index=0, text=f"chunk {i}", distance=d)
        for i, d in enumerate([0.30, 0.60, 0.74])
    ]
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"P{i}") for i in range(3)]

    with (
        patch.object(settings, "hybrid_retrieval", False),
        patch.object(settings, "intra_paper_delta", 0.25),
    ):
        chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    assert len(chunks) == 3


async def test_single_paper_scope_with_empty_sql_result_returns_empty():
    """Guards the empty-SQL passthrough, not a cut-to-empty: for any
    non-negative delta (production's `intra_paper_delta` is 0.20),
    `keep_within_paper` always keeps `distances[0]` (see
    test_intra_paper_ranker.py), so a non-empty SQL result can never be cut
    to nothing by the delta. This proves only that zero SQL rows in means an
    empty list out — which is what triggers respond()'s existing fallback to
    global scope."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning_no_rows()
    paper = PaperInfo(paper_id="p1", title="Only Paper")

    chunks = await svc._retrieve_paper_chunks(mock_db, [paper], [0.0] * 768, "q")

    assert chunks == []


async def test_a_misattributed_marker_is_stripped_before_the_citation_array_is_built(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The strip-then-renumber ordering, asserted end to end.

    The evidence is one paper's chunk; the answer enumerates two papers and
    hangs that marker off both. The chip list is built from the renumbering
    map, so a marker stripped AFTER renumbering would leave a chip pointing at
    a claim that no longer cites it, and would consume a number in the visible
    sequence. Here the second item loses its marker and the persisted array
    carries exactly one entry, numbered [1].
    """
    from app.agents.chat_agent import ChunkContext
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    owner = Paper(
        project_id=project.id,
        title="Cooperative Multi-Target Search with UAV Swarms",
        source="manual",
    )
    other = Paper(
        project_id=project.id,
        title="Deep Reinforcement Learning for Trajectory Path Planning",
        source="manual",
    )
    db_session.add_all([owner, other])
    await db_session.commit()
    await db_session.refresh(owner)

    answer = (
        "1. Cooperative Multi-Target Search with UAV Swarms: the reward is shaped [1].\n"
        "2. Deep Reinforcement Learning for Trajectory Path Planning: penalties too [1].\n"
    )

    async def fake_stream(*args, **kwargs):
        yield answer

    svc = ChatService()
    save = AsyncMock()
    chunks = [ChunkContext(n=1, paper_id=owner.id, title=owner.title, chunk_index=0, text="reward")]

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._reformulator, "run", AsyncMock(return_value="Test question")),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=chunks)),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", save),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    persisted_text, citations = save.await_args.args[3], save.await_args.args[4]
    assert persisted_text.splitlines()[0].endswith("the reward is shaped [1].")
    assert persisted_text.splitlines()[1].endswith("penalties too.")
    assert [c["n"] for c in citations] == [1]
    assert citations[0]["paper_id"] == owner.id


async def test_mentions_scope_retrieval_to_the_named_papers(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    from app.agents.chat_agent import ChunkContext
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    papers = [Paper(project_id=project.id, title=f"Paper {i}", source="manual") for i in range(3)]
    db_session.add_all(papers)
    await db_session.commit()
    for p in papers:
        await db_session.refresh(p)

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    # A non-empty result, deliberately: an empty return here would also
    # trigger the widen-on-empty-scope fallback (see
    # test_mentions_with_no_chunks_at_all_fall_back_to_the_library), which
    # re-queries the FULL library and becomes the mock's last recorded call —
    # masking the very narrowing this test exists to check.
    retrieve = AsyncMock(
        return_value=[
            ChunkContext(n=1, paper_id=papers[1].id, title=papers[1].title, chunk_index=0, text="t")
        ]
    )

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "What does it say?", [papers[1].id]):
            pass

    scoped = retrieve.await_args.args[1]
    assert [p.paper_id for p in scoped] == [papers[1].id]


async def test_no_mentions_means_no_widener_call_and_global_scope(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """Nothing infers scope any more. An un-mentioned turn makes no scoping
    decision at all — and therefore cannot make a wrong one."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    for i in range(3):
        db_session.add(Paper(project_id=project.id, title=f"Paper {i}", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    retrieve = AsyncMock(return_value=[])
    widener = AsyncMock(return_value=False)

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", widener),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "What does it say?", []):
            pass

    widener.assert_not_awaited()
    assert len(retrieve.await_args.args[1]) == 3


async def test_widening_pins_the_mentioned_papers_and_fills_globally(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The widener adds the library; it never removes the named paper."""
    from app.agents.chat_agent import ChunkContext
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    papers = [Paper(project_id=project.id, title=f"Paper {i}", source="manual") for i in range(3)]
    db_session.add_all(papers)
    await db_session.commit()
    for p in papers:
        await db_session.refresh(p)
    named, other = papers[1], papers[2]

    async def fake_stream(*args, **kwargs):
        yield "answer"

    def chunk(n, paper):
        return ChunkContext(n=n, paper_id=paper.id, title=paper.title, chunk_index=n, text=f"c{n}")

    calls = []

    async def fake_retrieve(
        db, scope, embedding, query_text, pool_limit=None, guarantee_per_paper=0
    ):
        # `pool_limit` mirrors the real signature: the mention path asks for a
        # bigger CANDIDATE pool than the budget so `apply_per_paper_floor` is
        # what applies the ceiling. This double ignores it -- it returns a
        # fixed list -- but must accept it.
        calls.append([p.paper_id for p in scope])
        if len(calls) == 1:
            return [chunk(1, named)]
        return [chunk(1, other)]

    svc = ChatService()
    stream = MagicMock(return_value=fake_stream())

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=True)),
        patch.object(svc, "_retrieve_paper_chunks", fake_retrieve),
        patch.object(svc._chat_agent, "stream", stream),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [ev async for ev in svc.respond(conv.id, "and others?", [named.id])]

    assert calls[0] == [named.id]
    assert len(calls[1]) == 3
    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["scoped"] is True and retrieving["widened"] is True
    # The count follows what the USER named, not the widened scope.
    assert retrieving["scoped_count"] == 1

    # Both fake_retrieve calls return a chunk numbered n=1 (each is its own
    # catalog of one). Merging pinned + fill without renumbering would hand
    # the model two excerpts both claiming citation marker [1] -- only
    # _renumber_chunks makes `n` follow the FINAL merged order. Mutation
    # tested: deleting the _renumber_chunks calls in respond() leaves this
    # assertion failing ([1, 1] instead of [1, 2]) while every other test in
    # this file still passes.
    agent_input = stream.call_args.args[0]
    assert [c.n for c in agent_input.paper_chunks] == [1, 2]


async def test_mentions_with_no_chunks_at_all_fall_back_to_the_library(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """A paper mentioned before ingest finished has no chunks. Answering from
    an empty evidence set is worse than answering wider than asked."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    papers = [Paper(project_id=project.id, title=f"Paper {i}", source="manual") for i in range(2)]
    db_session.add_all(papers)
    await db_session.commit()
    for p in papers:
        await db_session.refresh(p)

    async def fake_stream(*args, **kwargs):
        yield "answer"

    scopes = []

    async def fake_retrieve(
        db, scope, embedding, query_text, pool_limit=None, guarantee_per_paper=0
    ):
        # `pool_limit` mirrors the real signature: the mention path asks for a
        # bigger CANDIDATE pool than the budget so `apply_per_paper_floor` is
        # what applies the ceiling. This double ignores it -- it returns a
        # fixed list -- but must accept it.
        scopes.append([p.paper_id for p in scope])
        return []

    svc = ChatService()

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_paper_chunks", fake_retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [ev async for ev in svc.respond(conv.id, "q", [papers[0].id])]

    assert scopes[0] == [papers[0].id]
    assert len(scopes[1]) == 2
    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["widened"] is True


async def test_the_scope_titles_reach_the_chat_agent(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    from app.agents.chat_agent import ChunkContext
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    paper = Paper(project_id=project.id, title="Named Paper", source="manual")
    other = Paper(project_id=project.id, title="Other Paper", source="manual")
    db_session.add_all([paper, other])
    await db_session.commit()
    await db_session.refresh(paper)

    async def fake_stream(*args, **kwargs):
        yield "answer"

    stream = MagicMock(return_value=fake_stream())
    svc = ChatService()

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(
            svc,
            "_retrieve_paper_chunks",
            AsyncMock(
                return_value=[
                    ChunkContext(n=1, paper_id=paper.id, title=paper.title, chunk_index=0, text="t")
                ]
            ),
        ),
        patch.object(svc._chat_agent, "stream", stream),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "q", [paper.id]):
            pass

    agent_input = stream.call_args.args[0]
    assert agent_input.scope_titles == ["Named Paper"]
    assert agent_input.scope_widened is False


async def test_widened_fill_is_capped_at_the_context_budget(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The budget is a hard ceiling across PINNED FLOOR + GLOBAL FILL
    together, not just over the fill alone.

    Mutation tested: deleting the `[:budget]` slice on the fill in
    `_retrieve_mentioned_chunks` leaves this failing (7 chunks instead of 3)
    while every other test in this file still passes -- the floor call
    alone never returns enough rows to trip `max_context_chunks` in any
    other test, so nothing else exercises this ceiling once pinned and
    filled chunks are combined.
    """
    from app.agents.chat_agent import ChunkContext
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    papers = [Paper(project_id=project.id, title=f"Paper {i}", source="manual") for i in range(3)]
    db_session.add_all(papers)
    await db_session.commit()
    for p in papers:
        await db_session.refresh(p)
    named = papers[0]

    async def fake_stream(*args, **kwargs):
        yield "answer"

    def pinned_chunk(i):
        return ChunkContext(
            n=1, paper_id=named.id, title=named.title, chunk_index=i, text=f"pinned{i}"
        )

    def fill_chunk(i):
        return ChunkContext(
            n=1, paper_id=papers[1].id, title=papers[1].title, chunk_index=100 + i, text=f"fill{i}"
        )

    calls = []

    async def fake_retrieve(
        db, scope, embedding, query_text, pool_limit=None, guarantee_per_paper=0
    ):
        # `pool_limit` mirrors the real signature: the mention path asks for a
        # bigger CANDIDATE pool than the budget so `apply_per_paper_floor` is
        # what applies the ceiling. This double ignores it -- it returns a
        # fixed list -- but must accept it.
        calls.append([p.paper_id for p in scope])
        if len(calls) == 1:
            # The floor call: 2 chunks pinned to the mentioned paper.
            return [pinned_chunk(0), pinned_chunk(1)]
        # The widened fill: 5 more chunks, all distinct from the pinned
        # ones, deliberately more than the remaining budget can hold.
        return [fill_chunk(i) for i in range(5)]

    svc = ChatService()
    stream = MagicMock(return_value=fake_stream())

    with (
        patch.object(settings, "max_context_chunks", 3),
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=True)),
        patch.object(svc, "_retrieve_paper_chunks", fake_retrieve),
        patch.object(svc._chat_agent, "stream", stream),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "and others?", [named.id]):
            pass

    agent_input = stream.call_args.args[0]
    assert len(agent_input.paper_chunks) == 3


class _FakeSessionLocal:
    """Stands in for `chat_service.SessionLocal` so `_retrieve_mentioned_chunks`
    runs its REAL `_retrieve_paper_chunks` against a mock row source.

    Every other mention test patches `_retrieve_paper_chunks` itself, which is
    exactly why the floor's no-op went unnoticed: the bug lived in the
    interaction between that method's own truncation and the floor's budget,
    and a mocked retriever cannot express it.
    """

    def __init__(self, db):
        self._db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _ranked_db(rows: list) -> MagicMock:
    """Fake AsyncSession returning `rows` in rank order, LIMIT ignored.

    Ignoring LIMIT is deliberate and matches `_mock_db_returning`: the service
    must bound its own output, and the pool it asks for has to be visible in
    Python rather than trusted to SQL.
    """
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


def _row(i: int, paper_id: str) -> MagicMock:
    return MagicMock(
        id=f"c{i}",
        paper_id=paper_id,
        chunk_index=i,
        text=f"chunk {i}",
        distance=0.1 + i * 0.0001,
        d_rank=i + 1,
        s_rank=None,
    )


async def test_the_per_paper_floor_fires_on_a_production_shaped_candidate_list():
    """The floor must survive `_retrieve_paper_chunks`'s OWN truncation.

    PRODUCTION SHAPE, and that is the whole point: the scoped query returns
    MORE candidates than the budget, and the second mentioned paper's chunks
    all rank below the cut. `_retrieve_paper_chunks` used to truncate to
    `max_context_chunks` before `apply_per_paper_floor` ever saw the list, so
    the floor was handed 60 chunks of A with a budget of 60 and could only
    reorder them -- B was already gone. "Compare @A and @B" then answered with
    nothing from B, the exact failure the floor exists to prevent.

    test_mention_ranker.py could not catch this: it calls the floor directly
    with len(ordered) > budget, a shape production could not produce.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    budget = settings.max_context_chunks
    floor = settings.mention_per_paper_floor
    # A owns the entire top of the ranking; B starts only after the budget.
    rows = [_row(i, "pA") for i in range(budget)] + [_row(budget + i, "pB") for i in range(10)]
    scope = [PaperInfo(paper_id="pA", title="A"), PaperInfo(paper_id="pB", title="B")]

    with patch("app.services.chat_service.SessionLocal", _FakeSessionLocal(_ranked_db(rows))):
        chunks, widened = await svc._retrieve_mentioned_chunks(
            scope, scope, [0.0] * 768, "compare them", False
        )

    assert widened is False
    # The ceiling is still exactly max_context_chunks, applied in Python.
    assert len(chunks) == budget
    # The guarantee: B is represented despite ranking entirely below the cut.
    assert sum(1 for c in chunks if c.paper_id == "pB") == floor
    assert sum(1 for c in chunks if c.paper_id == "pA") == budget - floor
    # `n` follows the FINAL order the model is shown.
    assert [c.n for c in chunks] == list(range(1, budget + 1))


def _sequenced_db(*batches: list) -> MagicMock:
    """Fake AsyncSession returning a DIFFERENT row list per execute() call.

    `_ranked_db` returns the same rows to every query, which cannot express
    the shape this file now has to test: the RANKED query and the per-paper
    GUARANTEE query are two different queries returning two different row
    sets, and the whole bug is that the second one is the only place a
    rank-dominated paper appears.
    """
    results = []
    for rows in batches:
        result = MagicMock()
        result.fetchall.return_value = rows
        results.append(result)
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=results)
    return mock_db


def _guaranteed_row(i: int, paper_id: str, p_rank: int) -> MagicMock:
    return MagicMock(
        id=f"g{i}",
        paper_id=paper_id,
        chunk_index=i,
        text=f"guaranteed {i}",
        distance=0.6 + i * 0.0001,
        p_rank=p_rank,
    )


async def test_a_mentioned_paper_below_the_pool_edge_still_reaches_the_model():
    """Rank domination must not be able to defeat the floor. FAILS pre-fix.

    THE MEASURED BUG (.superpowers/multi-mention-measurement.md, 2026-08-18,
    100-paper corpus): a bigger candidate pool is not a representation
    guarantee, because how far down the ranking a mentioned paper's best chunk
    lands is not a quantity anything bounds. `hybrid-split-federated`'s second
    mentioned paper first appears at fused rank 72 and `nemo-mobility`'s at
    rank 91, against a pool of 70 -- both INSIDE the 0.75 distance gate
    (0.6925 and 0.6837), both contributing zero chunks.
    `test_the_per_paper_floor_fires_on_a_production_shaped_candidate_list`
    above cannot catch this: it puts B at ranks 60-69, i.e. inside the old
    pool's headroom, which is exactly the case the headroom did rescue.

    Here B does not appear in the ranked query AT ALL. The only way it reaches
    the model is the per-paper guarantee query, so pre-fix this test sees 60
    chunks of A and zero of B.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    budget = settings.max_context_chunks
    floor = settings.mention_per_paper_floor
    # The RANKED query: A owns every candidate the budget can hold, and B is
    # nowhere in it — the rank-domination case, not the pool-edge case.
    ranked = [_row(i, "pA") for i in range(budget)]
    # The GUARANTEE query: each paper's OWN nearest `floor` chunks, round
    # robin by per-paper rank, exactly as the SQL orders them. A's are the
    # rows the ranked query already returned; B's are new.
    guaranteed = []
    for rank in range(1, floor + 1):
        guaranteed.append(MagicMock(id=f"c{rank - 1}", paper_id="pA", p_rank=rank))
        guaranteed.append(_guaranteed_row(rank, "pB", rank))
    scope = [PaperInfo(paper_id="pA", title="A"), PaperInfo(paper_id="pB", title="B")]

    db = _sequenced_db(ranked, guaranteed)
    with patch("app.services.chat_service.SessionLocal", _FakeSessionLocal(db)):
        chunks, widened = await svc._retrieve_mentioned_chunks(
            scope, scope, [0.0] * 768, "compare them", False
        )

    assert widened is False
    # The ceiling is still exactly max_context_chunks, applied in Python.
    assert len(chunks) == budget
    assert sum(1 for c in chunks if c.paper_id == "pB") == floor
    assert sum(1 for c in chunks if c.paper_id == "pA") == budget - floor
    # Citation numbering follows the FINAL order shown to the model.
    assert [c.n for c in chunks] == list(range(1, budget + 1))
    # It was the per-paper window query that rescued B, at the SAME distance
    # gate the ranked query used — the guarantee never reopens the gate.
    assert db.execute.await_count == 2
    sql, params = db.execute.await_args_list[1].args
    assert "PARTITION BY c.paper_id" in str(sql)
    assert params["guarantee"] == floor
    assert params["threshold"] == settings.similarity_threshold


@pytest.mark.parametrize("hybrid", [True, False])
async def test_the_global_path_issues_exactly_the_candidate_query_it_always_did(hybrid):
    """The non-mention path is the default for every un-mentioned turn, and
    the guarantee must be invisible to it.

    Not a style point: the guarantee query is a `ROW_NUMBER() OVER (PARTITION
    BY paper_id ...)` over the whole scope, and the whole scope on the global
    path is the entire library. Folding it into the shipped candidate query
    would make every ordinary turn rank-number thousands of chunks per
    partition for a guarantee nothing asked for. One query, no window over
    paper_id, no `guarantee` bind — on BOTH retrieval paths.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    papers = [PaperInfo(paper_id=f"p{i}", title=f"P{i}") for i in range(3)]
    mock_db = _mock_db_returning_no_rows()

    with patch.object(settings, "hybrid_retrieval", hybrid):
        await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768, "q")

    assert mock_db.execute.await_count == 1
    sql, params = mock_db.execute.call_args.args
    assert "PARTITION BY c.paper_id" not in str(sql)
    assert "guarantee" not in params


@pytest.mark.parametrize("hybrid", [True, False])
async def test_a_single_paper_scope_never_issues_the_guarantee_query(hybrid):
    """With one paper in scope nothing can starve it — its own top chunks ARE
    the top of the ranking — and the single-paper cut
    (`intra_paper_rank_window` / `intra_paper_delta`) is a deliberate policy
    a representation guarantee has no business overriding. So the guarantee
    is inert there even when a caller asks for it.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning_no_rows()

    with patch.object(settings, "hybrid_retrieval", hybrid):
        await svc._retrieve_paper_chunks(
            mock_db,
            [PaperInfo(paper_id="p1", title="Only")],
            [0.0] * 768,
            "q",
            guarantee_per_paper=settings.mention_per_paper_floor,
        )

    assert mock_db.execute.await_count == 1
    assert "PARTITION BY c.paper_id" not in str(mock_db.execute.call_args.args[0])


async def test_the_guarantee_query_runs_on_the_dense_only_kill_switch_too():
    """`hybrid_retrieval=False` is production's kill switch and the eval
    harness's control arm. A representation guarantee that only holds under
    hybrid would make flipping the switch silently drop a mentioned paper.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    budget = settings.max_context_chunks
    floor = settings.mention_per_paper_floor
    ranked = [_row(i, "pA") for i in range(budget)]
    guaranteed = [_guaranteed_row(r, "pB", r) for r in range(1, floor + 1)]
    scope = [PaperInfo(paper_id="pA", title="A"), PaperInfo(paper_id="pB", title="B")]

    db = _sequenced_db(ranked, guaranteed)
    with (
        patch.object(settings, "hybrid_retrieval", False),
        patch("app.services.chat_service.SessionLocal", _FakeSessionLocal(db)),
    ):
        chunks, _ = await svc._retrieve_mentioned_chunks(
            scope, scope, [0.0] * 768, "compare them", False
        )

    assert db.execute.await_count == 2
    assert "tsv" not in str(db.execute.await_args_list[0].args[0])
    assert sum(1 for c in chunks if c.paper_id == "pB") == floor


async def test_a_guaranteed_candidate_never_outranks_a_genuinely_better_chunk():
    """Presence, not promotion. A guaranteed row is appended to the TAIL of
    the ranked list, so the floor can pin it while the relevance fill still
    reaches every better-ranked chunk first.

    Mutation tested: putting the extras at the HEAD instead leaves this test
    failing (the guaranteed rows take the first `n`s and the model reads them
    as the most relevant excerpts) while every representation assertion above
    still passes.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    floor = settings.mention_per_paper_floor
    ranked = [_row(i, "pA") for i in range(3)]
    guaranteed = [_guaranteed_row(r, "pB", r) for r in range(1, floor + 1)]
    papers = [PaperInfo(paper_id="pA", title="A"), PaperInfo(paper_id="pB", title="B")]

    db = _sequenced_db(ranked, guaranteed)
    chunks = await svc._retrieve_paper_chunks(
        db, papers, [0.0] * 768, "q", guarantee_per_paper=floor
    )

    assert [c.paper_id for c in chunks] == ["pA"] * 3 + ["pB"] * floor
    assert [c.n for c in chunks] == list(range(1, 3 + floor + 1))


async def test_the_candidate_pool_never_scales_with_library_size():
    """The pool is a function of the BUDGET and of how many papers the USER
    named -- never of the library.

    Per-paper allocation keyed on library size is what once pulled 191 chunks
    and killed every turn on `context_length_exceeded`. Raising the candidate
    pool to make the floor real is the obvious place to reintroduce that by the
    back door, so the bound is pinned here: two mentions against a 300-paper
    project ask for exactly the same number of candidates as two mentions
    against a two-paper project.
    """
    from app.agents.chat_agent import ChunkContext
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    scope = [PaperInfo(paper_id="pA", title="A"), PaperInfo(paper_id="pB", title="B")]
    asked: list[tuple[int | None, int]] = []

    async def recording_retrieve(
        db, papers, embedding, query_text, pool_limit=None, guarantee_per_paper=0
    ):
        asked.append((pool_limit, guarantee_per_paper))
        return [ChunkContext(n=1, paper_id="pA", title="A", chunk_index=0, text="t")]

    for library in (
        scope,
        scope + [PaperInfo(paper_id=f"p{i}", title=f"P{i}") for i in range(300)],
    ):
        with patch.object(svc, "_retrieve_paper_chunks", recording_retrieve):
            await svc._retrieve_mentioned_chunks(scope, library, [0.0] * 768, "q", False)

    # The ranked pool is exactly the budget, and the representation guarantee
    # is per MENTIONED paper -- so the total candidate bound is
    # `budget + floor * len(scope)` either way, and `len(scope)` is capped at
    # 10 by the API. Neither number moves when the library grows 150x.
    expected = (settings.max_context_chunks, settings.mention_per_paper_floor)
    assert asked == [expected, expected]


async def test_a_widened_turn_that_lands_no_library_chunk_is_not_reported_widened(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The model must never be told it has library excerpts it does not have.

    The widener fires on comparative questions, and four golden-set questions
    keep the entire 60-chunk budget inside a single paper (evals/retrieval/
    README.md). Pinning the whole scoped result left `max_context_chunks -
    len(pinned)` == 0, so no library chunk could land -- while `widened` stayed
    True and `build_scope_block` told the model "Excerpts from other papers are
    also provided; use them for comparison."

    Pinning only the FLOOR is what leaves room, and the flag now reports what
    ACTUALLY happened: no chunk from outside the mention scope means not
    widened, whatever the widener asked for.
    """
    from app.agents.chat_agent import ChunkContext, build_scope_block
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    papers = [Paper(project_id=project.id, title=f"Paper {i}", source="manual") for i in range(3)]
    db_session.add_all(papers)
    await db_session.commit()
    for p in papers:
        await db_session.refresh(p)
    named = papers[0]

    async def fake_stream(*args, **kwargs):
        yield "answer"

    budget = settings.max_context_chunks

    def chunk(i):
        return ChunkContext(
            n=i + 1, paper_id=named.id, title=named.title, chunk_index=i, text=f"c{i}"
        )

    # The named paper fills the budget on its own, and it is ALSO the nearest
    # paper globally -- so the library fill returns nothing the mention scope
    # did not already hold. That is the reachable case: near-duplicate content
    # in one paper, no comparable chunk anywhere else in the project.
    owned = [chunk(i) for i in range(budget)]

    async def fake_retrieve(
        db, scope, embedding, query_text, pool_limit=None, guarantee_per_paper=0
    ):
        return list(owned)

    svc = ChatService()
    stream = MagicMock(return_value=fake_stream())

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._reformulator, "run", AsyncMock(return_value="q")),
        patch.object(svc._widener, "run", AsyncMock(return_value=True)),
        patch.object(svc, "_retrieve_paper_chunks", fake_retrieve),
        patch.object(svc._chat_agent, "stream", stream),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [ev async for ev in svc.respond(conv.id, "compare them", [named.id])]

    agent_input = stream.call_args.args[0]
    # What the PROMPT says, which is the thing that misled the model.
    assert agent_input.scope_widened is False
    assert "also provided" not in build_scope_block(
        agent_input.scope_titles, agent_input.scope_widened
    )
    # And the SSE, which is what the badge renders.
    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["widened"] is False
    # Pinning only the floor must not COST context: the global fill puts the
    # named paper's remaining chunks back by relevance.
    assert len(agent_input.paper_chunks) == budget


async def test_mentions_that_resolve_to_nothing_report_widened(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """Papers were named but none could be scoped to -- here because the
    embedding call failed, so retrieval never ran at all. Reporting
    `scoped: true, widened: false` claims a scope that was never applied; the
    UI rendered "scoped to 0 papers" while retrieval was global."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    paper = Paper(project_id=project.id, title="Named Paper", source="manual")
    db_session.add(paper)
    await db_session.commit()
    await db_session.refresh(paper)

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(side_effect=RuntimeError("down"))),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [ev async for ev in svc.respond(conv.id, "q", [paper.id])]

    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["scoped"] is True
    assert retrieving["scoped_count"] == 0
    assert retrieving["widened"] is True


async def test_a_grouped_citation_marker_survives_the_whole_pipeline(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """Pre-fix, "[8, 14]" reached the reader carrying raw CATALOG positions and
    produced no chips: the strip, renumbering and the frontend marker regex all
    read one number per bracket, so the group was invisible to every one of
    them. Expansion happens before any of them count or rewrite markers."""
    from app.agents.chat_agent import ChunkContext
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    paper = Paper(project_id=project.id, title="Grouped Marker Paper", source="manual")
    db_session.add(paper)
    await db_session.commit()
    await db_session.refresh(paper)

    async def fake_stream(*args, **kwargs):
        yield "Both agree [2, 3] but the third differs [1]."

    chunks = [
        ChunkContext(n=n, paper_id=paper.id, title=paper.title, chunk_index=n, text=f"c{n}")
        for n in (1, 2, 3)
    ]
    svc = ChatService()
    save = AsyncMock()

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._reformulator, "run", AsyncMock(return_value="q")),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=chunks)),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", save),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    persisted_text, citations = save.await_args.args[3], save.await_args.args[4]
    # Every grouped source is renumbered by first appearance and carries a chip.
    assert persisted_text == "Both agree [1], [2] but the third differs [3]."
    assert [c["n"] for c in citations] == [1, 2, 3]
    assert [c["chunk_index"] for c in citations] == [2, 3, 1]


async def test_a_mentioned_paper_that_returns_nothing_is_reported_not_hidden(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """ "Compare @A and @B" where B's chunks all fall outside the distance gate
    answers from A alone. Today that is invisible: the event says scoped to 2
    papers and the model is handed both titles with no hint that one produced
    nothing. Both the wire and the prompt now name the gap."""
    from app.agents.chat_agent import ChunkContext
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    a = Paper(project_id=project.id, title="Paper A", source="manual")
    b = Paper(project_id=project.id, title="Paper B", source="manual")
    db_session.add_all([a, b])
    await db_session.commit()
    await db_session.refresh(a)
    await db_session.refresh(b)

    async def fake_stream(*args, **kwargs):
        yield "answer"

    # Only A comes back — B's chunks were excluded by the gate.
    chunks = [ChunkContext(n=1, paper_id=a.id, title=a.title, chunk_index=0, text="t")]
    stream = MagicMock(return_value=fake_stream())
    svc = ChatService()

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_mentioned_chunks", AsyncMock(return_value=(chunks, False))),
        patch.object(svc._chat_agent, "stream", stream),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [ev async for ev in svc.respond(conv.id, "compare them", [a.id, b.id])]

    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["empty_mentions"] == ["Paper B"]
    assert retrieving["scoped_count"] == 2

    agent_input = stream.call_args.args[0]
    assert agent_input.scope_empty_titles == ["Paper B"]


async def test_no_empty_mentions_are_reported_when_every_named_paper_contributes(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    from app.agents.chat_agent import ChunkContext
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    paper = Paper(project_id=project.id, title="Paper A", source="manual")
    db_session.add(paper)
    await db_session.commit()
    await db_session.refresh(paper)

    async def fake_stream(*args, **kwargs):
        yield "answer"

    chunks = [ChunkContext(n=1, paper_id=paper.id, title=paper.title, chunk_index=0, text="t")]
    svc = ChatService()

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_mentioned_chunks", AsyncMock(return_value=(chunks, False))),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [ev async for ev in svc.respond(conv.id, "q", [paper.id])]

    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["empty_mentions"] == []


# --- Resolved scope: the QUESTION named the papers, the user picked nothing ---
#
# Everything below guards the precedence rule and the never-guess property.
# The resolver's own matching rules are pinned in tests/test_paper_resolver.py;
# these tests are about the WIRING.

_RESOLVER_LIBRARY = (
    "Breaking the Deadly Triad in Offline Reinforcement Learning",
    "Lazy Agents and Credit Assignment in Multi-Agent RL",
    "A Survey of Deep Reinforcement Learning for UAV Swarms",
)


async def _papers_for_resolver(db_session: AsyncSession, project: Project, titles=None):
    from app.db.models import Paper

    rows = [
        Paper(project_id=project.id, title=t, source="manual")
        for t in (titles if titles is not None else _RESOLVER_LIBRARY)
    ]
    db_session.add_all(rows)
    await db_session.commit()
    for r in rows:
        await db_session.refresh(r)
    return rows


async def test_a_question_naming_a_paper_by_title_scopes_retrieval_to_it(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The whole point of the lexical rung: the user typed the identifying
    words, so the turn is scoped WITHOUT anything guessing and without an
    "@" mention. The event reports how it happened and quotes the phrase."""
    from app.agents.chat_agent import ChunkContext
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    papers = await _papers_for_resolver(db_session, project)

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    chunks = [
        ChunkContext(n=1, paper_id=papers[0].id, title=papers[0].title, chunk_index=0, text="t")
    ]
    retrieve = AsyncMock(return_value=chunks)

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [
            ev
            async for ev in svc.respond(
                conv.id, "What does Breaking the Deadly Triad say about Q?", []
            )
        ]

    scoped = retrieve.await_args.args[1]
    assert [p.paper_id for p in scoped] == [papers[0].id]

    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["scoped"] is True
    assert retrieving["scoped_count"] == 1
    assert retrieving["scope_source"] == "resolved"
    # The reader's own words, not the title we picked -- that is what tells
    # them what to change to search wider.
    assert retrieving["scope_evidence"] == ["breaking the deadly triad"]


async def test_a_mention_wins_and_the_resolver_is_never_consulted(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """THE PRECEDENCE LOCK. The question names paper[0] by a full title span
    -- it would resolve on its own -- while the user "@"-mentioned paper[1].
    The mention takes the turn whole: the resolver is not called at all, so
    there is no path by which a resolved id could widen, narrow or merge into
    a scope the user picked."""
    from app.agents.chat_agent import ChunkContext
    from app.services import chat_service as chat_service_module
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    papers = await _papers_for_resolver(db_session, project)

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    chunks = [
        ChunkContext(n=1, paper_id=papers[1].id, title=papers[1].title, chunk_index=0, text="t")
    ]
    retrieve = AsyncMock(return_value=chunks)
    resolver = MagicMock(side_effect=AssertionError("the resolver must not run for a mention turn"))

    with (
        patch.object(chat_service_module, "resolve_papers_with_evidence", resolver),
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [
            ev
            async for ev in svc.respond(
                conv.id,
                "What does Breaking the Deadly Triad say about Q?",
                [papers[1].id],
            )
        ]

    resolver.assert_not_called()
    scoped = retrieve.await_args.args[1]
    assert [p.paper_id for p in scoped] == [papers[1].id]

    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["scope_source"] == "mention"
    assert retrieving["scope_evidence"] == []


async def test_an_ambiguous_question_falls_through_to_global_retrieval(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """One span, two titles. The rung declines ENTIRELY rather than picking a
    best candidate -- retrieval runs over the whole library, which is the
    correct fall-through and the property that separates this from the
    deleted LLM targeter."""
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    papers = await _papers_for_resolver(
        db_session,
        project,
        titles=(
            "Lazy Agents and Credit Assignment in Multi-Agent RL",
            "Lazy Agents and Credit Assignment Revisited",
            "A Survey of Deep Reinforcement Learning for UAV Swarms",
        ),
    )

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    retrieve = AsyncMock(return_value=[])
    widener = AsyncMock(return_value=False)

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", widener),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [
            ev
            async for ev in svc.respond(
                conv.id, "What about Lazy Agents and Credit Assignment?", []
            )
        ]

    assert {p.paper_id for p in retrieve.await_args.args[1]} == {p.id for p in papers}
    widener.assert_not_awaited()

    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["scoped"] is False
    assert retrieving["scope_source"] == "mention"
    assert retrieving["scope_evidence"] == []


async def test_a_resolved_scope_runs_the_widener_and_takes_the_mention_path(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The retrieval MECHANICS are identical to a mention scope -- same
    `_retrieve_mentioned_chunks`, so same per-paper floor, same candidate
    guarantee, same budget ceiling. The widener runs too: it can only ADD the
    library, and a scope the user never clicked is exactly where a too-narrow
    turn does damage."""
    from app.agents.chat_agent import ChunkContext
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    papers = await _papers_for_resolver(db_session, project)

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    chunks = [
        ChunkContext(n=1, paper_id=papers[0].id, title=papers[0].title, chunk_index=0, text="t")
    ]
    scoped_retrieve = AsyncMock(return_value=(chunks, True))
    widener = AsyncMock(return_value=True)

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", widener),
        patch.object(svc, "_retrieve_mentioned_chunks", scoped_retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [
            ev
            async for ev in svc.respond(
                conv.id, "What does Breaking the Deadly Triad say about Q?", []
            )
        ]

    widener.assert_awaited_once()
    assert widener.await_args.args[0].mentioned_titles == [papers[0].title]
    assert [p.paper_id for p in scoped_retrieve.await_args.args[0]] == [papers[0].id]

    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["scope_source"] == "resolved"
    assert retrieving["widened"] is True


async def test_the_resolved_scope_reaches_the_chat_agent_with_its_provenance(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The SCOPE block must not tell the model the user restricted a turn the
    user never restricted."""
    from app.agents.chat_agent import ChunkContext
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    papers = await _papers_for_resolver(db_session, project)

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    chunks = [
        ChunkContext(n=1, paper_id=papers[0].id, title=papers[0].title, chunk_index=0, text="t")
    ]
    stream = MagicMock(return_value=fake_stream())

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_mentioned_chunks", AsyncMock(return_value=(chunks, False))),
        patch.object(svc._chat_agent, "stream", stream),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "What does Breaking the Deadly Triad say?", []):
            pass

    agent_input = stream.call_args.args[0]
    assert agent_input.scope_titles == [papers[0].title]
    assert agent_input.scope_source == "resolved"


async def test_the_resolver_reads_the_users_words_not_the_reformulated_query(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """Every guard in the resolver reads what the USER typed. Resolving from
    the reformulation would put an LLM back in charge of scope through the
    side door -- here the reformulator invents a title span for a paper the
    user never named, and it must change nothing."""
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    await _papers_for_resolver(db_session, project)
    # The reformulator only runs on a turn with history, which is also the
    # only shape where it can invent words the user never typed.
    db_session.add(
        ChatMessage(conversation_id=conv.id, role="assistant", content="An earlier answer.")
    )
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    retrieve = AsyncMock(return_value=[])
    reformulated = "What does Breaking the Deadly Triad say about offline data?"

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._reformulator, "run", AsyncMock(return_value=reformulated)),
        patch.object(svc._widener, "run", AsyncMock(return_value=False)),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = [ev async for ev in svc.respond(conv.id, "and what about that one?", [])]

    assert len(retrieve.await_args.args[1]) == 3
    retrieving = json.loads(next(e for e in events if e["event"] == "retrieving")["data"])
    assert retrieving["scoped"] is False
