"""ChatService integration test — all external calls mocked."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

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


def _mock_db_returning_shortlist(rows: list[tuple[str, float, int]]) -> MagicMock:
    """Fake AsyncSession returning (paper_id, best, n_chunks) rows.

    Deliberately returns them in the order given, ignoring any ORDER BY, so
    the tests prove what the service does with the rows rather than what
    Postgres would have done for it.
    """
    mock_rows = [MagicMock(paper_id=p, best=b, n_chunks=n) for p, b, n in rows]
    mock_result = MagicMock()
    mock_result.fetchall.return_value = mock_rows
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


async def test_shortlist_papers_returns_nearest_first_capped_at_limit():
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning_shortlist([("p0", 0.30, 10), ("p1", 0.31, 20), ("p2", 0.32, 30)])
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}") for i in range(3)]

    candidates, _ = await svc._shortlist_papers(mock_db, papers, [0.0] * 768, 2)

    assert [c.paper_id for c in candidates] == ["p0", "p1"]
    assert [c.title for c in candidates] == ["Paper 0", "Paper 1"]


async def test_shortlist_papers_counts_chunks_across_the_whole_project():
    """The total must cover EVERY paper, not just the returned candidates.

    It is what decides whether targeting is worth an LLM call at all: if the
    whole library already fits the context budget, scoping cannot change what
    is retrieved. Counting only the top few would under-report and skip
    targeting on projects that need it.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning_shortlist([("p0", 0.30, 10), ("p1", 0.31, 20), ("p2", 0.32, 30)])
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}") for i in range(3)]

    _, total = await svc._shortlist_papers(mock_db, papers, [0.0] * 768, 2)

    assert total == 60


async def test_shortlist_papers_issues_one_scoped_query():
    """One query, scoped to this project's papers and this embedding model.

    paper_chunk_embeddings is global; an unscoped ranking would rank another
    project's papers as candidates for this project's question.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning_shortlist([])
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}") for i in range(3)]

    with patch.object(settings, "embedding_model", "test-embed-model"):
        await svc._shortlist_papers(mock_db, papers, [0.0] * 768, 10)

    assert mock_db.execute.await_count == 1
    _, params = mock_db.execute.call_args.args
    assert json.loads(params["ids"]) == ["p0", "p1", "p2"]
    assert params["model"] == "test-embed-model"


async def test_shortlist_papers_skips_rows_for_unknown_papers():
    """A row whose paper_id is not in paper_infos cannot be labelled with a
    title, so it must be dropped rather than surfaced with an empty one.

    Two known papers, not one: a single-paper project now short-circuits
    before this filtering logic even runs (see
    test_shortlist_papers_returns_early_for_a_single_paper), so this needs
    more than one paper to actually exercise it.
    """
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning_shortlist([("ghost", 0.20, 5), ("p0", 0.30, 10)])
    papers = [PaperInfo(paper_id="p0", title="Paper 0"), PaperInfo(paper_id="p1", title="Paper 1")]

    candidates, total = await svc._shortlist_papers(mock_db, papers, [0.0] * 768, 10)

    assert [c.paper_id for c in candidates] == ["p0"]
    assert total == 15


async def test_respond_scopes_retrieval_to_the_targeted_paper(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The whole point: when one paper is identified, retrieval sees only it.

    Attribution errors become structurally impossible rather than
    discouraged — another paper's chunks are never fetched, so the model
    cannot answer from them however similar they look.

    The mocked retrieval returns a real chunk, not []: an empty result now
    triggers the full-library fallback (see
    test_respond_falls_back_to_full_library_when_scoped_retrieval_is_empty),
    which would make `retrieve.await_args` below point at that second,
    unscoped call instead of the scoped one this test exists to check.
    """
    from app.db.models import Paper
    from app.services.chat_service import ChatService, PaperInfo

    conv = conversation_with_message
    for i in range(3):
        db_session.add(Paper(project_id=project.id, title=f"Paper {i}", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    retrieve = AsyncMock(return_value=[MagicMock(n=1)])
    candidates = [
        PaperInfo(paper_id="pA", title="Paper A"),
        PaperInfo(paper_id="pB", title="Paper B"),
    ]

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=(candidates, 500))),
        patch.object(svc._targeter, "run", AsyncMock(return_value="pB")),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    scoped = retrieve.await_args.args[1]
    assert [p.paper_id for p in scoped] == ["pB"]


async def test_respond_retrieves_across_all_papers_when_no_target(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """None is a normal answer. It must fall through to the unscoped global
    top-k — the behaviour that existed before targeting."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService, PaperInfo

    conv = conversation_with_message
    for i in range(3):
        db_session.add(Paper(project_id=project.id, title=f"Paper {i}", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    retrieve = AsyncMock(return_value=[])
    candidates = [PaperInfo(paper_id="pA", title="Paper A")]

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=(candidates, 500))),
        patch.object(svc._targeter, "run", AsyncMock(return_value=None)),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    scoped = retrieve.await_args.args[1]
    assert len(scoped) == 3


async def test_respond_skips_targeting_when_the_library_fits_the_budget(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """If every chunk in the project already fits the context budget, scoping
    cannot change what is retrieved, so the LLM call is pure cost. Asserting
    ZERO calls is the point — this is what keeps small projects free."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService, PaperInfo

    conv = conversation_with_message
    db_session.add(Paper(project_id=project.id, title="Paper A", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    target = AsyncMock(return_value="pA")
    candidates = [PaperInfo(paper_id="pA", title="Paper A")]

    with (
        patch.object(settings, "max_context_chunks", 40),
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=(candidates, 12))),
        patch.object(svc._targeter, "run", target),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[])),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    target.assert_not_awaited()


async def test_respond_sends_the_targeter_titles_only(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The service must not hand the targeter anything but ids and titles —
    the O(1)-in-library-size property is a property of the CALLER too."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService, PaperInfo

    conv = conversation_with_message
    for i in range(3):
        db_session.add(Paper(project_id=project.id, title=f"Paper {i}", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    target = AsyncMock(return_value=None)
    candidates = [PaperInfo(paper_id="pA", title="Paper A")]

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=(candidates, 500))),
        patch.object(svc._targeter, "run", target),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[])),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    sent = target.await_args.args[0]
    assert sent.candidates == [{"paper_id": "pA", "title": "Paper A"}]


async def test_shortlist_papers_returns_early_for_a_single_paper():
    """A single-paper project has nothing to disambiguate: the candidate
    list would be that one paper regardless of what this query returns, so
    it must skip the SQL round trip entirely rather than rank a list of
    one."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    papers = [PaperInfo(paper_id="p0", title="Paper 0")]

    result = await svc._shortlist_papers(mock_db, papers, [0.0] * 768, 10)

    assert result == ([], 0)
    mock_db.execute.assert_not_awaited()


async def test_respond_skips_targeting_for_a_single_paper_project_even_over_budget(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """A one-paper project has nothing to disambiguate: `scope` would be that
    same paper whether the targeter names it or answers None, so targeting
    there is a guaranteed no-op that still costs a full extra LLM call every
    turn. Forcing total_chunks OVER budget isolates this from the separate
    'library fits the budget' skip -- both must independently hold for
    "upload one PDF and ask about it", the common case, to stay at one LLM
    call per turn."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService, PaperInfo

    conv = conversation_with_message
    db_session.add(Paper(project_id=project.id, title="Paper A", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    target = AsyncMock(return_value="pA")
    candidates = [PaperInfo(paper_id="pA", title="Paper A")]

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=(candidates, 500))),
        patch.object(svc._targeter, "run", target),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[])),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    target.assert_not_awaited()


async def test_respond_falls_back_to_full_library_when_scoped_retrieval_is_empty(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """Scoped retrieval can legitimately return zero chunks when every chunk
    of the targeted paper sits at or beyond similarity_threshold. Falling
    back to the full paper list keeps the answer grounded -- the alternative
    is chat_agent silently answering from general knowledge, a worse failure
    than the misattribution this branch fixes."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService, PaperInfo

    conv = conversation_with_message
    for i in range(3):
        db_session.add(Paper(project_id=project.id, title=f"Paper {i}", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    candidates = [
        PaperInfo(paper_id="pA", title="Paper A"),
        PaperInfo(paper_id="pB", title="Paper B"),
    ]
    retrieve = AsyncMock(return_value=[])

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=(candidates, 500))),
        patch.object(svc._targeter, "run", AsyncMock(return_value="pB")),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = []
        async for event in svc.respond(conv.id, "Test question"):
            events.append(event)

    assert retrieve.await_count == 2
    first_scope, second_scope = (c.args[1] for c in retrieve.await_args_list)
    assert [p.paper_id for p in first_scope] == ["pB"]
    assert len(second_scope) == 3  # falls back to the full project paper list

    # The 'retrieving' event's paper_count must follow the fallback, not the
    # abandoned scoped attempt (see test_respond_reports_the_scoped_paper_
    # count_in_the_retrieving_event for the non-fallback case).
    retrieving = next(e for e in events if e["event"] == "retrieving")
    assert json.loads(retrieving["data"])["paper_count"] == 3


async def test_respond_does_not_fall_back_when_scoped_retrieval_returns_chunks(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The fallback must fire ONLY on an empty result -- a real chunk from
    the targeted paper is exactly the case scoping exists for, so a second
    query here would silently reintroduce the cross-paper misattribution."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService, PaperInfo

    conv = conversation_with_message
    for i in range(3):
        db_session.add(Paper(project_id=project.id, title=f"Paper {i}", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    candidates = [
        PaperInfo(paper_id="pA", title="Paper A"),
        PaperInfo(paper_id="pB", title="Paper B"),
    ]
    retrieve = AsyncMock(return_value=[MagicMock(n=1)])

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=(candidates, 500))),
        patch.object(svc._targeter, "run", AsyncMock(return_value="pB")),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    assert retrieve.await_count == 1


async def test_respond_reports_the_scoped_paper_count_in_the_retrieving_event(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """The SSE 'retrieving' event must report how many papers retrieval
    actually used, not the whole project -- otherwise the UI says "Retrieving
    from 3 papers..." right after retrieval was scoped to 1."""
    from app.db.models import Paper
    from app.services.chat_service import ChatService, PaperInfo

    conv = conversation_with_message
    for i in range(3):
        db_session.add(Paper(project_id=project.id, title=f"Paper {i}", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    candidates = [
        PaperInfo(paper_id="pA", title="Paper A"),
        PaperInfo(paper_id="pB", title="Paper B"),
    ]

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=(candidates, 500))),
        patch.object(svc._targeter, "run", AsyncMock(return_value="pB")),
        patch.object(svc, "_retrieve_paper_chunks", AsyncMock(return_value=[MagicMock(n=1)])),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        events = []
        async for event in svc.respond(conv.id, "Test question"):
            events.append(event)

    retrieving = next(e for e in events if e["event"] == "retrieving")
    assert json.loads(retrieving["data"])["paper_count"] == 1


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
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=([], 0))),
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
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=([], 0))),
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
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=([], 0))),
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
        patch.object(svc, "_shortlist_papers", AsyncMock(return_value=([], 0))),
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

