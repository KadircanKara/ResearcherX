"""`chunk_count` on the paper list: what retrieval can actually search.

Counted under the configured embedding model only, in one grouped query for
the whole list -- the Papers page reads it instead of probing each paper.
"""

from datetime import datetime, timedelta, timezone

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Bm25IndexState,
    Paper,
    PaperSummary,
    PaperChunkEmbedding,
    Project,
    ProjectMember,
    User,
)
from app.db.seed import seed_users
from app.db.session import engine


@pytest_asyncio.fixture
async def you(db_session: AsyncSession) -> User:
    await seed_users(db_session)
    await db_session.commit()
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Chunk Count Test", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def _add_paper(
    db: AsyncSession, project: Project, title: str, chunks: int, stale: int = 0
) -> Paper:
    paper = Paper(project_id=project.id, title=title, source="upload", status="ready")
    db.add(paper)
    await db.flush()
    for i in range(chunks + stale):
        db.add(
            PaperChunkEmbedding(
                paper_id=paper.id,
                project_id=paper.project_id,
                chunk_index=i,
                text=f"chunk {i}",
                embedding="[0.0]",
                model=settings.embedding_model if i < chunks else "some-other-model",
            )
        )
    await db.commit()
    return paper


async def _counts(client: AsyncClient, you: User, project: Project) -> dict[str, int]:
    r = await client.get(f"/v1/projects/{project.id}/papers", headers={"X-Dev-User-Id": you.id})
    assert r.status_code == 200
    return {p["title"]: p["chunk_count"] for p in r.json()}


async def test_counts_indexed_and_unindexed_papers(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project
):
    await _add_paper(db_session, project, "Indexed", chunks=3)
    await _add_paper(db_session, project, "Scanned", chunks=0)
    await _add_paper(db_session, project, "Other", chunks=1)

    assert await _counts(client, you, project) == {"Indexed": 3, "Scanned": 0, "Other": 1}


async def test_rows_under_another_embedding_model_are_not_counted(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project
):
    """A stale-model row is invisible to retrieval, so it must not make a
    paper read as searchable."""
    await _add_paper(db_session, project, "Mixed", chunks=2, stale=4)
    await _add_paper(db_session, project, "Only stale", chunks=0, stale=3)

    assert await _counts(client, you, project) == {"Mixed": 2, "Only stale": 0}


async def test_query_count_does_not_grow_with_paper_count(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project
):
    statements: list[str] = []

    def _record(conn, cursor, statement, *args):
        statements.append(statement)

    async def _list_query_count() -> int:
        statements.clear()
        event.listen(engine.sync_engine, "before_cursor_execute", _record)
        try:
            await _counts(client, you, project)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _record)
        return len(statements)

    await _add_paper(db_session, project, "One", chunks=1)
    with_one = await _list_query_count()
    for i in range(5):
        await _add_paper(db_session, project, f"More {i}", chunks=i)
    with_six = await _list_query_count()

    assert with_six == with_one
    assert sum("paper_chunk_embeddings" in s for s in statements) == 1


async def _statuses(client: AsyncClient, you: User, project: Project) -> dict[str, tuple]:
    r = await client.get(f"/v1/projects/{project.id}/papers", headers={"X-Dev-User-Id": you.id})
    assert r.status_code == 200
    return {p["title"]: (p["dense_status"], p["keyword_status"]) for p in r.json()}


async def test_list_reports_dense_and_keyword_status(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project, monkeypatch
):
    monkeypatch.setattr(settings, "sparse_backend", "postgres")
    await _add_paper(db_session, project, "Indexed", chunks=2)
    await _add_paper(db_session, project, "Scanned", chunks=0)
    pending = await _add_paper(db_session, project, "Pending", chunks=0)
    pending.status = "pending"
    await db_session.commit()

    assert await _statuses(client, you, project) == {
        "Indexed": ("embedded", "indexed"),
        "Scanned": ("none", "none"),
        "Pending": ("pending", "pending"),
    }


async def test_bm25s_keyword_status_waits_for_the_index_build(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project, monkeypatch
):
    monkeypatch.setattr(settings, "sparse_backend", "bm25s")
    await _add_paper(db_session, project, "New", chunks=2)

    # No index built yet: the paper is embedded but waits for keyword search.
    assert await _statuses(client, you, project) == {"New": ("embedded", "queued")}

    db_session.add(
        Bm25IndexState(
            project_id=project.id,
            current_version=1,
            built_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        )
    )
    await db_session.commit()
    assert await _statuses(client, you, project) == {"New": ("embedded", "indexed")}


async def test_list_reports_summary_status_and_the_summary_endpoint_serves_it(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project
):
    done = await _add_paper(db_session, project, "Summarized", chunks=1)
    await _add_paper(db_session, project, "Not yet", chunks=1)
    db_session.add(
        PaperSummary(
            paper_id=done.id,
            summary="A paper about swarms.",
            keywords=["UAV", "swarm"],
            source="llm",
            status="ready",
            summary_model="deepseek/deepseek-v4.1-flash",
        )
    )
    await db_session.commit()
    headers = {"X-Dev-User-Id": you.id}

    r = await client.get(f"/v1/projects/{project.id}/papers", headers=headers)
    assert {p["title"]: p["summary_status"] for p in r.json()} == {
        "Summarized": "ready",
        "Not yet": "none",
    }

    papers = {p["title"]: p["id"] for p in r.json()}
    r = await client.get(
        f"/v1/projects/{project.id}/papers/{papers['Summarized']}/summary", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == "A paper about swarms."
    assert body["keywords"] == ["UAV", "swarm"]
    assert body["source"] == "llm"
    assert "doc_embedding" not in body

    r = await client.get(
        f"/v1/projects/{project.id}/papers/{papers['Not yet']}/summary", headers=headers
    )
    assert r.status_code == 404
