"""Paper ingest: file -> text -> section-aware chunks -> embeddings.

Chunking is `structured_chunker.py` (pure) fed by `pdf_extraction.py`
(PyMuPDF) for PDFs and `text_extraction.py` (pure) for every other upload
format. This module owns persistence and the tier decision:

    PDF bytes            -> extract_document -> tier 1 (outline) or tier 2
    DOCX/MD/TXT/RTF bytes-> extract_markdown -> tier 3 (no pages)
    stored extracted_text-> chunk_markdown   -> tier 3 (no pages)

Every row stores the author's text; the string that is EMBEDDED is
`chunk_header.embed_text(title, section, text)`, built here and nowhere
else at index time.
"""

import asyncio
import hashlib
import json
import re
import uuid

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core import observability
from app.core.logging import log
from app.db.models import Paper, PaperChunkEmbedding, PaperSource, PaperStatus, _now
from app.db.session import SessionLocal
from app.services.bm25_state import mark_dirty
from app.services.chunk_header import embed_text, section_path_text
from app.services import paper_summary_service
from app.services.embedding_service import EmbeddingService, vector_literal
from app.services.paper_metadata_service import apply_metadata
from app.services.pdf_extraction import extract_document, outline_to_json, pages_to_json
from app.services.structured_chunker import ChunkRecord, chunk_markdown, chunk_pages
from app.services.text_extraction import PDF, extract_markdown

# Matches figure captions: "Figure 3." / "Fig. 3:" / "FIGURE 3 —" etc.
_FIGURE_CAPTION_RE = re.compile(
    r"(?:Figure|Fig\.?)\s+\d+[\.:\—\-]?\s+\S[^\n]{4,300}",
    re.IGNORECASE,
)

_embedding_svc = EmbeddingService()


# `ingest_error` keeps this much of the exception text. Internal only: the
# client still gets the route's generic error.
_INGEST_ERROR_MAX = 500


class DuplicateSource(Exception):
    """These bytes are already another paper of the same project.

    Carries that paper's id and title so the route can name it in its 409.
    """

    def __init__(self, paper_id: str, title: str):
        super().__init__(f"duplicate of paper {paper_id}")
        self.paper_id = paper_id
        self.title = title


def source_hash(data: bytes) -> str:
    """SHA-256 hex of a paper's source bytes: the ingest idempotency key."""
    return hashlib.sha256(data).hexdigest()


async def _find_holder(
    db: AsyncSession, project_id: str, digest: str, paper_id: str
) -> tuple[str, str] | None:
    """(id, title) of ANOTHER paper in the project holding `digest`, if any.

    Any status counts, not only `ready`: the partial unique index allows one
    row per hash, so a pending or failed holder blocks the hash just as
    surely, and the user's fix is to retry THAT paper.
    """
    row = (
        await db.execute(
            select(Paper.id, Paper.title).where(
                Paper.project_id == project_id,
                Paper.source_hash == digest,
                Paper.id != paper_id,
            )
        )
    ).first()
    return (row[0], row[1]) if row is not None else None


async def _unchanged_count(db: AsyncSession, paper: Paper, digest: str) -> int | None:
    """The stored chunk count when `paper` is already `ready` under `digest`
    AND searchable under the configured embedding model; None when the work
    must run. Only a ready row qualifies, so a failed or crashed ingest is
    retried in full, and a ready paper with no chunks at the configured model
    (an embedding-model change) is re-indexed rather than reported as 0."""
    if paper.status != PaperStatus.READY or paper.source_hash != digest:
        return None
    n = await stored_chunk_count(db, paper.id)
    return n if n > 0 else None


async def precheck_source(db: AsyncSession, paper_id: str, data: bytes) -> int | None:
    """What `ingest` would decide about `data` before doing any work.

    Raises `DuplicateSource` when the bytes are already ANOTHER paper of
    this paper's project; returns the stored chunk count when they are this
    paper's own unchanged, searchable source; None when ingest must run. For
    the routes, so a refused or no-op upload is answered before the file is
    stored or the daily ingest meter is charged. `ingest` decides again
    itself; this is a peek, not a reservation.
    """
    paper = (await db.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()
    digest = source_hash(data)
    holder = await _find_holder(db, paper.project_id, digest, paper_id)
    if holder is not None:
        raise DuplicateSource(*holder)
    return await _unchanged_count(db, paper, digest)


async def stored_chunk_count(db: AsyncSession, paper_id: str) -> int:
    """Chunks this paper holds under the CONFIGURED embedding model -- the
    ones retrieval can search."""
    return (
        await db.execute(
            select(func.count()).where(
                PaperChunkEmbedding.paper_id == paper_id,
                PaperChunkEmbedding.model == settings.embedding_model,
            )
        )
    ).scalar_one()


async def _claim_hash(db: AsyncSession, paper: Paper, digest: str) -> None:
    """Mark the paper `pending` under `digest`, drop its old chunks, and
    COMMIT, before any parse.

    The chunks go in the same commit as `pending`. A re-upload's old chunks
    can never be served again -- the route has already overwritten the
    stored file, and a pending or failed paper is invisible to chat -- but
    left in place they sit in the HNSW index, where the ready filter drops
    them from a scan's top candidates and forces the whole-project exact
    fallback on every turn that meets them, for as long as a failed or
    cancelled re-upload leaves them there.

    The existence check is the detection mechanism. The unique index is only
    the backstop for two requests that both passed it; that race is mapped to
    the same `DuplicateSource` rather than surfacing as a 500.
    """
    paper_id, project_id = paper.id, paper.project_id
    holder = await _find_holder(db, project_id, digest, paper_id)
    if holder is not None:
        raise DuplicateSource(*holder)
    await db.execute(delete(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == paper_id))
    await mark_dirty(db, project_id)
    paper.status = PaperStatus.PENDING
    paper.source_hash = digest
    paper.ingest_error = None
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        holder = await _find_holder(db, project_id, digest, paper_id)
        if holder is None:
            raise
        raise DuplicateSource(*holder) from None


async def _mark_failed(db: AsyncSession, paper_id: str, exc: Exception) -> None:
    """Record a failed ingest in a FRESH session, after rolling back the one
    the work ran in: that session may hold half-written chunks, or be broken
    outright. Never raises -- the caller re-raises the original error."""
    reason = f"{type(exc).__name__}: {str(exc)[:_INGEST_ERROR_MAX]}"
    try:
        await db.rollback()
        async with SessionLocal() as fresh:
            failed = await fresh.get(Paper, paper_id)
            if failed is not None:
                failed.status = PaperStatus.FAILED
                failed.ingest_error = reason
                project_id = failed.project_id  # read before commit expires it
                await fresh.commit()
                # A failure after the chunks committed leaves them on disk but
                # out of scope: rebuild the bm25 index so it stops spending
                # sparse slots on them. Separate commit, so a failure here can
                # never lose the FAILED status written above.
                try:
                    await mark_dirty(fresh, project_id)
                    await fresh.commit()
                except Exception:  # noqa: BLE001 -- only costs a stale index
                    await fresh.rollback()
                    log.warning("paper_mark_failed_dirty_failed", paper_id=paper_id, exc_info=True)
    except Exception:  # noqa: BLE001 -- the original error is what matters
        log.exception("paper_mark_failed_failed", paper_id=paper_id)
    log.warning("paper_ingest_failed", paper_id=paper_id, error=reason)


def chunk_records_for_markdown(md: str) -> list[ChunkRecord]:
    """Tier 3: chunk stored markdown. Sections from headings, no pages."""
    return chunk_markdown(md, caption_re=_FIGURE_CAPTION_RE)


def _norm(s: str) -> str:
    """Collapse whitespace so containment comparison ignores wrapping."""
    return " ".join(s.split())


async def index_chunks(
    db: AsyncSession, paper_id: str, chunks: list[ChunkRecord], *, title: str
) -> int:
    """Embed and persist chunk records. Idempotent — replaces existing chunks.

    An empty `chunks` list clears the paper's index and commits that removal.
    `title` is what the embedded header carries; it is NOT stored per row.

    The paper is marked `ready` in the SAME commit as its chunks, so no
    reader sees a ready paper without chunks or chunks under a pending one.
    Every caller gets that for free, the scripts included.
    """
    # Looked up BEFORE the paid embedding call, so a deleted paper fails
    # without spending anything. project_id is the PAPER's, read here rather
    # than taken from the caller, so the denormalised copy on each chunk
    # cannot disagree with papers.
    paper = (await db.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()
    project_id = paper.project_id

    await db.execute(delete(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == paper_id))

    if not chunks:
        await mark_dirty(db, project_id)
        paper.status = PaperStatus.READY
        paper.ingest_error = None
        await db.commit()
        log.warning("paper_index_no_text", paper_id=paper_id)
        return 0

    embeddings = await _embedding_svc.embed_batch(
        [embed_text(title, c.section, c.text) for c in chunks], task_type="RETRIEVAL_DOCUMENT"
    )

    # ORM insert sends embedding as varchar; asyncpg rejects it against
    # halfvec(768). Raw INSERT with explicit CAST so Postgres receives the
    # right type.
    now = _now()
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):
        vec_str = vector_literal(emb)
        await db.execute(
            text("""
            INSERT INTO paper_chunk_embeddings
                (id, paper_id, project_id, chunk_index, text, section, section_text,
                 page, embedding, model, created_at)
            VALUES
                (:id, :paper_id, :project_id, :chunk_index, :text, :section,
                 :section_text, :page, CAST(:emb AS halfvec(768)), :model, :now)
            ON CONFLICT (paper_id, chunk_index) DO UPDATE
                SET text = EXCLUDED.text,
                    section = EXCLUDED.section,
                    section_text = EXCLUDED.section_text,
                    page = EXCLUDED.page,
                    embedding = EXCLUDED.embedding,
                    model = EXCLUDED.model
        """),
            {
                "id": str(uuid.uuid4()),
                "paper_id": paper_id,
                "project_id": project_id,
                "chunk_index": i,
                "text": chunk.text,
                "section": json.dumps(list(chunk.section)),
                "section_text": section_path_text(chunk.section),
                "page": chunk.page,
                "emb": vec_str,
                "model": settings.embedding_model,
                "now": now,
            },
        )
    # Marked after the paid embedding call, not before, so the state row's lock
    # is not held across it; same transaction as the chunks, so the dirty flag
    # and the change never disagree.
    await mark_dirty(db, project_id)
    paper.status = PaperStatus.READY
    paper.ingest_error = None
    await db.commit()
    log.info("paper_index_done", paper_id=paper_id, chunks=len(chunks))
    return len(chunks)


async def ingest(
    db: AsyncSession,
    paper_id: str,
    file_bytes: bytes,
    source_url: str | None = None,
    *,
    file_format: str = PDF,
) -> int:
    """Extract, chunk, embed, and persist one paper file. Returns the number
    of chunks stored.

    `file_format` is a `text_extraction` format, already confirmed against
    the bytes by `detect_format` (the upload route does that before storing
    the file). PDFs store `extracted_text` (plain markdown),
    `extracted_pages` and `outline`, so a re-chunk replays from the stored
    pages and never needs the PDF again; every other format stores
    `extracted_text` only, because it has no pages, and that stored markdown
    is all a later re-chunk needs (`reindex_papers`, markdown tier). Either
    way the text lands BEFORE index_chunks, in one transaction with the
    chunks.

    Idempotent by content hash. The SAME bytes on a paper that is already
    `ready` under them return its stored chunk count and do no work (a retry
    after a timeout). The same bytes held by ANOTHER paper of the project
    raise `DuplicateSource`. Otherwise the paper is committed `pending` with
    the new hash, and with its old chunks deleted, before anything is
    parsed; `index_chunks` flips it `ready` with its new chunks, and any
    exception marks it `failed` and re-raises. A failed or cancelled
    re-upload therefore leaves the paper with no chunks rather than the
    previous file's: the stored file is already the new one. Only
    a `ready` row short-circuits, so a failed or crashed ingest is retried
    in full, and so is a ready paper with no chunks under the configured
    embedding model (a re-upload after a model change re-indexes).

    A CANCELLED ingest (client disconnect: `CancelledError` is not an
    `Exception`) is not caught and stays `pending` -- excluded from
    retrieval, listed in the Papers tab, retryable. After a failure the
    caller's session has been ROLLED BACK, so every ORM object it loaded is
    expired: read ids before calling, never attributes after a raise.

    One trace per ingest: extraction, every embedding batch and the metadata
    call nest under it.
    """
    with observability.span(
        "paper.ingest",
        attributes={
            "langfuse.trace.name": "paper.ingest",
            "langfuse.trace.metadata.paper_id": paper_id,
            "langfuse.trace.metadata.file_format": file_format,
            "langfuse.trace.metadata.file_bytes": len(file_bytes),
        },
    ) as span:
        digest = source_hash(file_bytes)
        paper = (await db.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()
        n = await _unchanged_count(db, paper, digest)
        if n is not None:
            log.info("paper_ingest_unchanged", paper_id=paper_id, chunks=n)
            # A retry of the same file still gets a summary it never got (a
            # shutdown mid-task) or one from an older prompt version.
            try:
                if await paper_summary_service.needs_summary(db, paper_id):
                    paper_summary_service.schedule_summary(paper_id)
            except Exception:  # noqa: BLE001 -- a summary check never fails an ingest
                await db.rollback()
                log.warning("paper_summary_check_failed", paper_id=paper_id, exc_info=True)
            observability.set_attributes(
                span, {"langfuse.trace.metadata.chunks": n, "langfuse.trace.metadata.skipped": True}
            )
            return n
        title = paper.title
        await _claim_hash(db, paper, digest)

        try:
            if file_format == PDF:
                n, markdown = await _ingest(db, paper_id, title, file_bytes)
            else:
                n, markdown = await _ingest_text(db, paper_id, title, file_bytes, file_format)
        except Exception as exc:
            await _mark_failed(db, paper_id, exc)
            raise

        # After indexing, in its own transaction, and outside the failure
        # handler: indexing is load-bearing and must not be delayed or
        # endangered by an LLM call, and a paper whose chunks committed is
        # ready whatever metadata extraction does.
        await apply_metadata(db, paper_id, markdown, source_url)
        # Last, unawaited: the paper is ready and its metadata stored; the
        # summary runs in the background and never fails the ingest.
        paper_summary_service.schedule_summary(paper_id)
        observability.set_attributes(span, {"langfuse.trace.metadata.chunks": n})
        return n


async def _ingest(db: AsyncSession, paper_id: str, title: str, pdf_bytes: bytes) -> tuple[int, str]:
    # PyMuPDF is CPU-bound and its cost grows with page count, not bytes; on
    # the app's single event loop a long PDF would stall every other request.
    with observability.span("paper.extract"):
        ex = await asyncio.to_thread(extract_document, pdf_bytes)
    paper = await db.get(Paper, paper_id)
    if paper is not None:
        paper.extracted_text = ex.markdown
        paper.extracted_pages = pages_to_json(ex.pages)
        paper.outline = outline_to_json(ex.outline)

    records = chunk_pages(ex.pages, use_markers=ex.has_outline, caption_re=_FIGURE_CAPTION_RE)
    log.info(
        "paper_ingest_chunks",
        paper_id=paper_id,
        tier=1 if ex.has_outline else 2,
        body_chunks=sum(1 for r in records if r.kind == "body"),
        figure_chunks=sum(1 for r in records if r.kind == "caption"),
        markers_found=ex.injection.found,
        markers_by_paragraph=ex.injection.by_paragraph,
        markers_dropped=ex.injection.dropped,
    )
    return await index_chunks(db, paper_id, records, title=title), ex.markdown


async def _ingest_text(
    db: AsyncSession, paper_id: str, title: str, data: bytes, file_format: str
) -> tuple[int, str]:
    """The non-PDF twin of `_ingest`: markdown -> the tier-3 chunker, the
    same one `index_manual` and a re-chunk from stored text use.

    Extraction runs in a worker thread: mammoth and striprtf are CPU-bound
    and the app has one event loop in one worker. An empty document stores
    "" and indexes zero chunks; it is not an error.
    """
    with observability.span("paper.extract"):
        md = await asyncio.to_thread(extract_markdown, file_format, data)
    paper = await db.get(Paper, paper_id)
    if paper is not None:
        paper.extracted_text = md
        # Pages left by an earlier PDF ingest of this paper would make
        # `reindex_papers` replay the OLD document: its pages tier wins.
        paper.extracted_pages = None
        paper.outline = None

    records = chunk_records_for_markdown(md)
    log.info(
        "paper_ingest_chunks",
        paper_id=paper_id,
        tier=3,
        file_format=file_format,
        body_chunks=sum(1 for r in records if r.kind == "body"),
        figure_chunks=sum(1 for r in records if r.kind == "caption"),
    )
    return await index_chunks(db, paper_id, records, title=title), md


def manual_source_hash(abstract: str | None, body: str | None) -> str | None:
    """A manual paper's idempotency key: the hash of its hand-entered text,
    or None when there is none -- every textless paper would otherwise share
    the hash of "\\n" and collide on the unique index."""
    if not ((abstract or "").strip() or (body or "").strip()):
        return None
    return source_hash(((abstract or "") + "\n" + (body or "")).encode("utf-8"))


async def index_manual(
    db: AsyncSession, paper_id: str, abstract: str | None, body: str | None
) -> int:
    """Index hand-entered text: abstract as its own chunk, then body chunks.

    The abstract chunk is skipped when its text already appears in the body.
    Manual text has no PDF and no pages; headings in the body still cut
    sections (tier 3).

    A MANUAL paper's `source_hash` becomes the hash of its text, checked for
    a duplicate in the project before anything is embedded (raises
    `DuplicateSource`). Other sources keep theirs: `reindex_papers` also
    reaches here for an uploaded or linked paper with hand-entered text, and
    must not overwrite its file hash. `index_chunks` marks the paper ready.
    """
    body = body or ""
    paper = (await db.execute(select(Paper).where(Paper.id == paper_id))).scalar_one()
    title = paper.title
    project_id = paper.project_id
    digest: str | None = None
    if paper.source == PaperSource.MANUAL:
        digest = manual_source_hash(abstract, body)
        if digest is not None:
            holder = await _find_holder(db, paper.project_id, digest, paper_id)
            if holder is not None:
                raise DuplicateSource(*holder)
        paper.source_hash = digest
    records: list[ChunkRecord] = []
    if abstract and abstract.strip() and _norm(abstract) not in _norm(body):
        records.append(ChunkRecord(text=abstract.strip(), section=(), page=None, kind="abstract"))
    records += await asyncio.to_thread(chunk_records_for_markdown, body)
    try:
        return await index_chunks(db, paper_id, records, title=title)
    except IntegrityError:
        # Two identical manual entries that both passed the check above: the
        # unique index is the backstop, and the loser gets the same
        # DuplicateSource as the check would have raised, not a 500.
        await db.rollback()
        holder = (
            await _find_holder(db, project_id, digest, paper_id) if digest is not None else None
        )
        if holder is None:
            raise
        raise DuplicateSource(*holder) from None
