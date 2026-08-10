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
    paper = PaperInfo(paper_id="p1", title="Test Paper")

    with patch.object(settings, "similarity_threshold", 0.42):
        await svc._retrieve_paper_chunks(mock_db, [paper], [0.0] * 768)

    _, params = mock_db.execute.call_args.args
    assert params["threshold"] == 0.42


def _mock_db_returning(n_rows: int) -> MagicMock:
    """Fake AsyncSession returning `n_rows` chunk rows, nearest first.

    Deliberately ignores any LIMIT in the SQL — that is the point: it proves
    the service bounds its own output rather than trusting the database to.
    """
    rows = [
        MagicMock(
            id=f"c{i}",
            paper_id=f"p{i % 100}",
            chunk_index=i,
            text=f"chunk {i}",
            distance=0.1 + i * 0.0001,
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

    await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768)

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
        chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768)

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

    await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768)

    _, params = mock_db.execute.call_args.args
    assert json.loads(params["ids"]) == ["p0", "p1", "p2"]


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
        chunks = await svc._retrieve_paper_chunks(mock_db, papers, [0.0] * 768)

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
    title, so it must be dropped rather than surfaced with an empty one."""
    from app.services.chat_service import ChatService, PaperInfo

    svc = ChatService()
    mock_db = _mock_db_returning_shortlist([("ghost", 0.20, 5), ("p0", 0.30, 10)])
    papers = [PaperInfo(paper_id="p0", title="Paper 0")]

    candidates, total = await svc._shortlist_papers(mock_db, papers, [0.0] * 768, 10)

    assert [c.paper_id for c in candidates] == ["p0"]
    assert total == 15
