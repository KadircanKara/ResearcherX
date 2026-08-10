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
    from app.agents.retrieval_planner import RetrievalPlan

    conv = conversation_with_message

    fake_plan = RetrievalPlan(
        mode="broad",
        reformulated_query="test question expanded",
        per_paper=[],
    )

    async def fake_stream(*args, **kwargs):
        yield "answer token "
        yield "two"

    svc = ChatService()

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(svc._planner, "run", AsyncMock(return_value=fake_plan)),
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
    from app.agents.retrieval_planner import PaperInfo
    from app.services.chat_service import ChatService

    svc = ChatService()
    mock_db = _mock_db_returning_no_rows()
    paper = PaperInfo(paper_id="p1", title="Test Paper", abstract="")

    with patch.object(settings, "similarity_threshold", 0.42):
        await svc._retrieve_paper_chunks(mock_db, [paper], {"p1": 5}, [0.0] * 768)

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
    from app.agents.retrieval_planner import PaperInfo
    from app.services.chat_service import ChatService

    svc = ChatService()
    mock_db = _mock_db_returning(0)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}", abstract="") for i in range(100)]

    await svc._retrieve_paper_chunks(mock_db, papers, {p.paper_id: 5 for p in papers}, [0.0] * 768)

    assert mock_db.execute.await_count == 1


async def test_retrieve_paper_chunks_caps_total_at_settings_max_context_chunks():
    """The context budget is a hard ceiling, independent of library size.

    Without it, 100 papers × the k=2 fallback produced 191 chunks ≈ 118.5k
    tokens and every chat turn died on `context_length_exceeded`.
    """
    from app.agents.retrieval_planner import PaperInfo
    from app.services.chat_service import ChatService

    svc = ChatService()
    mock_db = _mock_db_returning(500)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}", abstract="") for i in range(100)]

    with patch.object(settings, "max_context_chunks", 40):
        chunks = await svc._retrieve_paper_chunks(
            mock_db, papers, {p.paper_id: 5 for p in papers}, [0.0] * 768
        )

    assert len(chunks) == 40


async def test_retrieve_paper_chunks_applies_no_ceiling_when_allocation_is_absent():
    """`per_paper_map=None` means "the planner produced no allocation".

    A ceiling equal to the whole context budget is the same as no ceiling —
    the global LIMIT already bounds any single paper to that many chunks — so
    selection falls through to pure global top-k. The allocation still has to
    list every paper, because the alloc CTE is also what scopes the search to
    this project's chunks.
    """
    from app.agents.retrieval_planner import PaperInfo
    from app.services.chat_service import ChatService

    svc = ChatService()
    mock_db = _mock_db_returning(0)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}", abstract="") for i in range(100)]

    with patch.object(settings, "max_context_chunks", 40):
        await svc._retrieve_paper_chunks(mock_db, papers, None, [0.0] * 768)

    _, params = mock_db.execute.call_args.args
    alloc = json.loads(params["alloc"])
    assert len(alloc) == 100
    assert set(alloc.values()) == {40}


async def test_retrieve_paper_chunks_honours_planner_allocations():
    """A real plan is still a per-paper ceiling — that is what stops one
    well-matching paper monopolising a comparative answer. Papers the planner
    ran but never named keep the explicit unallocated fallback."""
    from app.agents.retrieval_planner import PaperInfo
    from app.services.chat_service import ChatService, _UNALLOCATED_PAPER_K

    svc = ChatService()
    mock_db = _mock_db_returning(0)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}", abstract="") for i in range(3)]

    with patch.object(settings, "max_context_chunks", 40):
        await svc._retrieve_paper_chunks(mock_db, papers, {"p0": 5, "p1": 1}, [0.0] * 768)

    _, params = mock_db.execute.call_args.args
    assert json.loads(params["alloc"]) == {"p0": 5, "p1": 1, "p2": _UNALLOCATED_PAPER_K}


async def test_respond_drops_per_paper_ceiling_when_plan_is_degraded(
    db_session: AsyncSession, project: Project, conversation_with_message
):
    """A degraded plan must reach retrieval as "no allocation", not as an
    allocation that happens to be empty.

    Without this the fail-open path is indistinguishable from a genuine
    `broad` plan, and every paper silently collects the unallocated fallback.
    """
    from app.agents.retrieval_planner import RetrievalPlan
    from app.db.models import Paper
    from app.services.chat_service import ChatService

    conv = conversation_with_message
    # _PLANNER_MIN_PAPERS: the planner only runs from 3 papers up.
    for i in range(3):
        db_session.add(Paper(project_id=project.id, title=f"Paper {i}", source="manual"))
    await db_session.commit()

    async def fake_stream(*args, **kwargs):
        yield "answer"

    svc = ChatService()
    retrieve = AsyncMock(return_value=[])

    with (
        patch.object(svc._embedding_svc, "embed", AsyncMock(return_value=[0.0] * 768)),
        patch.object(svc, "_retrieve_history", AsyncMock(return_value=[])),
        patch.object(
            svc._planner,
            "run",
            AsyncMock(
                return_value=RetrievalPlan(
                    mode="broad",
                    reformulated_query="Test question",
                    per_paper=[],
                    degraded=True,
                )
            ),
        ),
        patch.object(svc, "_retrieve_paper_chunks", retrieve),
        patch.object(svc._chat_agent, "stream", return_value=fake_stream()),
        patch.object(svc._conv_svc, "save_message", AsyncMock()),
    ):
        async for _ in svc.respond(conv.id, "Test question"):
            pass

    assert retrieve.await_args.args[2] is None


async def test_retrieve_paper_chunks_numbers_citations_contiguously_after_capping():
    """Citation markers must be 1..N over the chunks actually sent.

    `chat_agent` cites by position, so a gap or a number past the end of the
    list would point at a source the model was never given.
    """
    from app.agents.retrieval_planner import PaperInfo
    from app.services.chat_service import ChatService

    svc = ChatService()
    mock_db = _mock_db_returning(500)
    papers = [PaperInfo(paper_id=f"p{i}", title=f"Paper {i}", abstract="") for i in range(100)]

    with patch.object(settings, "max_context_chunks", 40):
        chunks = await svc._retrieve_paper_chunks(
            mock_db, papers, {p.paper_id: 5 for p in papers}, [0.0] * 768
        )

    assert [c.n for c in chunks] == list(range(1, 41))
