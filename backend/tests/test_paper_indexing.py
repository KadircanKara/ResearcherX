"""Service-level tests for chunk building and indexing."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Paper, PaperChunkEmbedding, Project, ProjectMember, User
from app.db.seed import seed_users
from app.services import paper_ingest_service as svc
from app.services.structured_chunker import ChunkRecord


@pytest.fixture(autouse=True)
def no_metadata_extraction():
    """ingest() now makes an LLM call for metadata. It fails open, so tests
    pass either way — but the SDK retries the unroutable endpoint with
    backoff first, adding seconds per test for nothing."""
    with patch(
        "app.services.paper_ingest_service.apply_metadata",
        new=AsyncMock(return_value="none"),
    ):
        yield


@pytest_asyncio.fixture
async def paper(db_session: AsyncSession) -> Paper:
    await seed_users(db_session)
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    p = Project(owner_id=you.id, title="Indexing Project", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    paper = Paper(project_id=p.id, title="T", source="manual")
    db_session.add(paper)
    await db_session.commit()
    await db_session.refresh(paper)
    return paper


async def _chunks(db_session: AsyncSession, paper_id: str) -> list[str]:
    rows = await db_session.execute(
        select(PaperChunkEmbedding)
        .where(PaperChunkEmbedding.paper_id == paper_id)
        .order_by(PaperChunkEmbedding.chunk_index)
    )
    return [r.text for r in rows.scalars().all()]


def _fake_embed(n_dims: int = 768):
    async def _embed(texts, task_type="RETRIEVAL_DOCUMENT"):
        return [[0.0] * n_dims for _ in texts]

    return AsyncMock(side_effect=_embed)


def _rec(text: str, section=(), page=None) -> ChunkRecord:
    return ChunkRecord(text=text, section=tuple(section), page=page)


async def _rows(db_session: AsyncSession, paper_id: str):
    rows = await db_session.execute(
        select(PaperChunkEmbedding)
        .where(PaperChunkEmbedding.paper_id == paper_id)
        .order_by(PaperChunkEmbedding.chunk_index)
    )
    return rows.scalars().all()


async def test_index_manual_abstract_only(db_session: AsyncSession, paper: Paper):
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        n = await svc.index_manual(db_session, paper.id, "A standalone abstract.", None)
    assert n == 1
    assert await _chunks(db_session, paper.id) == ["A standalone abstract."]


async def test_index_manual_skips_abstract_already_in_body(db_session: AsyncSession, paper: Paper):
    abstract = "We study swarms."
    body = f"{abstract} Then we go into detail about the method."
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_manual(db_session, paper.id, abstract, body)
    chunks = await _chunks(db_session, paper.id)
    assert len(chunks) == 1
    assert chunks[0] == body


async def test_index_manual_dedupe_ignores_whitespace_differences(
    db_session: AsyncSession, paper: Paper
):
    abstract = "We  study\n swarms."
    body = "We study swarms. Then detail."
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_manual(db_session, paper.id, abstract, body)
    assert len(await _chunks(db_session, paper.id)) == 1


async def test_index_manual_keeps_unique_abstract_at_index_zero(
    db_session: AsyncSession, paper: Paper
):
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_manual(db_session, paper.id, "Unique summary.", "Totally different body.")
    chunks = await _chunks(db_session, paper.id)
    assert chunks[0] == "Unique summary."
    assert len(chunks) == 2


async def test_index_chunks_is_idempotent(db_session: AsyncSession, paper: Paper):
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_chunks(db_session, paper.id, [_rec("one"), _rec("two")], title="T")
        await svc.index_chunks(db_session, paper.id, [_rec("one"), _rec("two")], title="T")
    assert await _chunks(db_session, paper.id) == ["one", "two"]


async def test_index_chunks_empty_clears_existing(db_session: AsyncSession, paper: Paper):
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_chunks(db_session, paper.id, [_rec("one")], title="T")
        n = await svc.index_chunks(db_session, paper.id, [], title="T")
    assert n == 0
    # A session sees its own uncommitted writes, so reading straight through
    # db_session would pass whether or not index_chunks committed the delete.
    # Roll back first: anything index_chunks did NOT commit is discarded, so
    # this assertion fails if the commit-on-empty behavior ever regresses.
    await db_session.rollback()
    assert await _chunks(db_session, paper.id) == []


async def test_index_chunks_records_configured_model(db_session: AsyncSession, paper: Paper):
    """Every row must carry the model that produced it, or a later provider
    switch silently mixes vector spaces in one index."""
    with (
        patch.object(svc._embedding_svc, "embed_batch", _fake_embed()),
        patch.object(settings, "embedding_model", "nomic-embed-text"),
    ):
        await svc.index_chunks(
            db_session, paper.id, [_rec("chunk one"), _rec("chunk two")], title="T"
        )

    rows = (
        (
            await db_session.execute(
                select(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == paper.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {r.model for r in rows} == {"nomic-embed-text"}


_MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 72 720 Td (Hello World) Tj ET\n"
    b"endstream\nendobj\nxref\n0 5\n"
    b"0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n"
    b"0000000115 00000 n \n0000000206 00000 n \n"
    b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n300\n%%EOF"
)


async def test_ingest_persists_extracted_text(db_session: AsyncSession, paper: Paper):
    """Without stored text, changing embedding models forces a PDF re-fetch."""
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.ingest(db_session, paper.id, _MINIMAL_PDF)

    await db_session.refresh(paper)
    assert paper.extracted_text is not None
    assert "Hello World" in paper.extracted_text


async def test_chunk_rows_carry_section_and_page_columns(db_session: AsyncSession, paper: Paper):
    """The columns exist and default to 'no section, no page' so rows
    written before the re-index keep working."""
    row = PaperChunkEmbedding(paper_id=paper.id, chunk_index=0, text="t", embedding="[0]")
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.section == []
    assert row.section_text == ""
    assert row.page is None


async def test_index_chunks_stores_section_path_page_and_the_raw_text(
    db_session: AsyncSession, paper: Paper
):
    recs = [_rec("The reward is a weighted sum.", ("IV. RL", "B. Reward"), 3)]
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_chunks(db_session, paper.id, recs, title="T")
    (row,) = await _rows(db_session, paper.id)
    assert row.text == "The reward is a weighted sum."  # header is NOT in the stored text
    assert row.section == ["IV. RL", "B. Reward"]
    assert row.section_text == "IV. RL > B. Reward"
    assert row.page == 3


async def test_index_chunks_embeds_the_header_plus_text(db_session: AsyncSession, paper: Paper):
    """What reaches the embedding provider is the composed string, so a
    chunk can be found by the name of its section."""
    fake = _fake_embed()
    recs = [_rec("The reward is a weighted sum.", ("IV. RL", "B. Reward"), 3)]
    with patch.object(svc._embedding_svc, "embed_batch", fake):
        await svc.index_chunks(db_session, paper.id, recs, title="Coop Search")
    sent = fake.call_args.args[0][0]
    assert sent.startswith("[Title: Coop Search | Section: IV. RL > B. Reward]\n\n")
    assert "Page" not in sent
    assert sent.endswith("The reward is a weighted sum.")


async def test_index_manual_builds_records_with_no_section(db_session: AsyncSession, paper: Paper):
    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        await svc.index_manual(db_session, paper.id, "A standalone abstract.", None)
    (row,) = await _rows(db_session, paper.id)
    assert row.text == "A standalone abstract." and row.section == [] and row.page is None


async def test_ingest_stores_pages_and_outline_and_chunks_with_sections(
    db_session: AsyncSession, paper: Paper
):
    import pymupdf

    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "II. SYSTEM MODEL", fontsize=12)
    p.insert_text((72, 130), "We model a swarm of UAVs.", fontsize=10)
    # `insert_text`'s point is top-down (origin top-left, matching
    # `get_text("blocks")`), but a TOC destination's `to` point is PDF user
    # space (origin BOTTOM-left) — `pdf_extraction._outline` converts with
    # `height - y`. The brief's literal `Point(72, 90)` skips that inversion
    # and lands the anchor ~750pt down an 842pt-tall page, nowhere near the
    # heading text at y≈87 from the top, so no block attaches and the outline
    # entry is silently dropped (found=0). `height - 85` inverts correctly
    # and lands just above the heading block (y0≈87.1), which is what the
    # test's "chunks with sections" assertion actually needs.
    doc.set_toc(
        [
            [
                1,
                "System Model",
                1,
                {"kind": pymupdf.LINK_GOTO, "to": pymupdf.Point(72, p.rect.height - 85)},
            ]
        ]
    )
    pdf = doc.tobytes()
    doc.close()

    with patch.object(svc._embedding_svc, "embed_batch", _fake_embed()):
        n = await svc.ingest(db_session, paper.id, pdf)
    assert n >= 1
    await db_session.refresh(paper)
    assert paper.extracted_pages and paper.extracted_pages[0]["page"] == 1
    assert paper.outline and paper.outline[0]["title"] == "System Model"
    assert "<!--section" not in (paper.extracted_text or "")
    rows = await _rows(db_session, paper.id)
    assert any(r.section == ["System Model"] and r.page == 1 for r in rows)
