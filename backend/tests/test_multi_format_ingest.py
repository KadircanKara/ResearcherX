"""Uploading .docx/.md/.txt/.rtf papers through the one ingest route, getting
the file back, and re-chunking such a paper without ever parsing it as a
PDF."""

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Paper, PaperChunkEmbedding, PaperFile, Project, ProjectMember, User
from app.db.seed import seed_users
from app.services import text_extraction as te
from tests.docx_builder import build_docx


@pytest.fixture(autouse=True)
def no_network():
    """Embeddings return one zero vector per input; metadata never calls out."""

    async def embed(texts, task_type=None):
        return [[0.0] * 768 for _ in texts]

    with (
        patch(
            "app.services.paper_ingest_service.EmbeddingService.embed_batch",
            new=AsyncMock(side_effect=embed),
        ),
        patch(
            "app.services.paper_ingest_service.apply_metadata",
            new=AsyncMock(return_value="none"),
        ) as meta,
    ):
        yield meta


@pytest_asyncio.fixture
async def you(db_session: AsyncSession) -> User:
    await seed_users(db_session)
    await db_session.commit()
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Formats", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def _paper(client: AsyncClient, you: User, project: Project, title="Uploaded") -> str:
    created = await client.post(
        f"/v1/projects/{project.id}/papers",
        json={"title": title, "source": "upload"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert created.status_code == 201
    return created.json()["id"]


async def _ingest(client, you, project, paper_id, data: bytes, ext: str | None):
    params = {"ext": ext} if ext is not None else {}
    return await client.post(
        f"/v1/projects/{project.id}/papers/{paper_id}/ingest",
        params=params,
        content=data,
        headers={"X-Dev-User-Id": you.id, "Content-Type": "application/octet-stream"},
    )


async def _chunks(db: AsyncSession, paper_id: str) -> list[PaperChunkEmbedding]:
    db.expire_all()
    return list(
        (
            await db.execute(
                select(PaperChunkEmbedding)
                .where(PaperChunkEmbedding.paper_id == paper_id)
                .order_by(PaperChunkEmbedding.chunk_index)
            )
        )
        .scalars()
        .all()
    )


def _section(chunk: PaperChunkEmbedding) -> tuple[str, ...]:
    raw = chunk.section
    return tuple(json.loads(raw) if isinstance(raw, str) else raw)


DOCX = build_docx(
    [(1, "Introduction"), (None, "Swarm search."), (2, "Setup"), (None, "Drones fly.")]
)


async def test_a_docx_upload_is_sectioned_by_its_headings(
    client, db_session, you, project, no_network
):
    pid = await _paper(client, you, project)
    r = await _ingest(client, you, project, pid, DOCX, "docx")
    assert r.status_code == 200, r.text
    assert r.json() == {"chunks_stored": 2}

    chunks = await _chunks(db_session, pid)
    assert [(_section(c), c.text, c.page) for c in chunks] == [
        (("Introduction",), "Swarm search.", None),
        (("Introduction", "Setup"), "Drones fly.", None),
    ]
    paper = await db_session.get(Paper, pid)
    await db_session.refresh(paper)
    assert paper.extracted_text == "# Introduction\n\nSwarm search.\n\n## Setup\n\nDrones fly."
    assert paper.extracted_pages is None
    # Metadata runs last on the extracted text, as for a PDF.
    no_network.assert_awaited_once()
    assert no_network.await_args.args[2] == paper.extracted_text


@pytest.mark.parametrize(
    ("ext", "data", "section", "text"),
    [
        ("md", b"# Methods\n\nWe measure.", ("Methods",), "We measure."),
        ("markdown", b"# Methods\n\nWe measure.", ("Methods",), "We measure."),
        ("TXT", "Café notes.".encode("cp1252"), (), "Café notes."),
        ("txt", b"\xef\xbb\xbfBOM text.", (), "BOM text."),
        ("rtf", rb"{\rtf1\ansi Plain \b rtf\b0 .\par}", (), "Plain rtf."),
    ],
)
async def test_text_formats_ingest(client, db_session, you, project, ext, data, section, text):
    pid = await _paper(client, you, project)
    r = await _ingest(client, you, project, pid, data, ext)
    assert r.status_code == 200, r.text
    chunks = await _chunks(db_session, pid)
    assert [(_section(c), c.text, c.page) for c in chunks] == [(section, text, None)]


async def test_an_empty_text_file_stores_zero_chunks_not_an_error(client, db_session, you, project):
    pid = await _paper(client, you, project)
    r = await _ingest(client, you, project, pid, b"", "txt")
    assert r.status_code == 200
    assert r.json() == {"chunks_stored": 0}
    assert await _chunks(db_session, pid) == []


@pytest.mark.parametrize(
    ("ext", "data", "phrase"),
    [
        ("docx", b"%PDF-1.4 renamed", "don't match"),
        ("pdf", DOCX, "don't match"),
        ("txt", b"%PDF-1.4 renamed", "don't match"),
        ("rtf", b"plain", "don't match"),
        ("doc", b"anything", "Unsupported file type"),
        ("png", b"\x89PNG", "Unsupported file type"),
    ],
)
async def test_a_refused_file_is_a_422_and_stores_nothing(
    client, db_session, you, project, ext, data, phrase
):
    pid = await _paper(client, you, project)
    r = await _ingest(client, you, project, pid, data, ext)
    assert r.status_code == 422
    assert phrase in r.json()["detail"]
    assert "Upload a PDF, DOCX, Markdown, TXT or RTF file." in r.json()["detail"]
    assert await db_session.get(PaperFile, pid) is None


async def test_an_unsupported_extension_costs_no_upload_quota(
    client, db_session, you, project, monkeypatch
):
    from app.core.config import settings
    from app.db.models import UsageEvent

    monkeypatch.setattr(settings, "user_ingests_per_day", 5)
    pid = await _paper(client, you, project)
    r = await _ingest(client, you, project, pid, b"x", "exe")
    assert r.status_code == 422
    assert (await db_session.execute(select(UsageEvent))).scalars().all() == []


async def test_a_docx_zip_bomb_is_a_422_before_anything_is_stored(
    client, db_session, you, project, monkeypatch
):
    # A 1MB bound so the test needs no 100MB payload. check_docx binds its
    # default at definition, so the bound is passed in.
    original = te.check_docx
    monkeypatch.setattr(
        te,
        "check_docx",
        lambda data, **kw: original(data, max_uncompressed=1024 * 1024),
    )
    bomb = build_docx([(None, "x")], extra={"word/media/pad.bin": b"\0" * (4 * 1024 * 1024)})
    pid = await _paper(client, you, project)
    r = await _ingest(client, you, project, pid, bomb, "docx")
    assert r.status_code == 422
    assert r.json()["detail"] == te.UnreadableFile.message
    assert await db_session.get(PaperFile, pid) is None


async def test_no_extension_still_means_pdf_for_older_clients(client, you, project):
    pid = await _paper(client, you, project)
    with patch("app.services.paper_ingest_service.ingest", new=AsyncMock(return_value=3)) as ingest:
        r = await _ingest(client, you, project, pid, b"%PDF-1.4 x", None)
    assert r.status_code == 200
    assert ingest.await_args.kwargs["file_format"] == te.PDF


@pytest.mark.parametrize(
    ("ext", "data", "content_type", "suffix"),
    [
        ("docx", DOCX, te.CONTENT_TYPES[te.DOCX], ".docx"),
        ("md", b"# x\n\ny", "text/markdown", ".md"),
        ("txt", "café".encode("cp1252"), "text/plain", ".txt"),
        ("rtf", rb"{\rtf1 hi}", "application/rtf", ".rtf"),
        ("pdf", b"%PDF-1.4 stored", "application/pdf", ".pdf"),
    ],
)
async def test_the_download_serves_the_stored_file_with_its_real_type(
    client, you, project, ext, data, content_type, suffix
):
    pid = await _paper(client, you, project, title="Swarms — a survey")
    with patch("app.services.paper_ingest_service.ingest", new=AsyncMock(return_value=0)):
        await _ingest(client, you, project, pid, data, ext)

    r = await client.get(
        f"/v1/projects/{project.id}/papers/{pid}/pdf", headers={"X-Dev-User-Id": you.id}
    )
    assert r.status_code == 200
    assert r.content == data
    # Verbatim: no charset appended to a text type whose bytes may be cp1252.
    assert r.headers["content-type"] == content_type
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert f'filename="Swarms _ a survey{suffix}"' in disposition
    assert f"filename*=UTF-8''Swarms%20%E2%80%94%20a%20survey{suffix}" in disposition


# ── re-chunking a non-PDF paper ─────────────────────────────────────────────


async def test_reindex_rechunks_a_docx_paper_from_stored_text_never_as_a_pdf(
    client, db_session, you, project
):
    from scripts import reindex_papers as rp

    pid = await _paper(client, you, project)
    assert (await _ingest(client, you, project, pid, DOCX, "docx")).status_code == 200

    rows = (await db_session.execute(rp._ALL_SQL)).fetchall()
    (row,) = [r for r in rows if r.id == pid]
    assert not row.has_pdf_file
    assert rp.tier_for(row) == "markdown"

    with (
        patch.object(rp, "extract_document", side_effect=AssertionError("parsed as a PDF")),
        patch.object(rp, "fetch_pdf", new=AsyncMock(side_effect=AssertionError("network"))),
    ):
        n, tier, fetch_attempted = await rp._reindex_one(row, allow_fetch=True)
    assert (n, tier, fetch_attempted) == (2, "markdown", False)
    chunks = await _chunks(db_session, pid)
    assert [_section(c) for c in chunks] == [("Introduction",), ("Introduction", "Setup")]


async def test_reindex_still_reads_an_uploaded_pdf_blob(client, db_session, you, project):
    from scripts import reindex_papers as rp

    pid = await _paper(client, you, project)
    with patch("app.services.paper_ingest_service.ingest", new=AsyncMock(return_value=0)):
        await _ingest(client, you, project, pid, b"%PDF-1.4 blob", "pdf")

    rows = (await db_session.execute(rp._ALL_SQL)).fetchall()
    (row,) = [r for r in rows if r.id == pid]
    assert row.has_pdf_file
    assert await rp._blob_bytes(row) == b"%PDF-1.4 blob"
