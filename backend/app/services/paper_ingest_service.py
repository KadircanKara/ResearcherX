"""Paper ingest: PDF -> pages + outline -> section-aware chunks -> embeddings.

Chunking is `structured_chunker.py` (pure) fed by `pdf_extraction.py`
(PyMuPDF). This module owns persistence and the tier decision:

    PDF bytes            -> extract_document -> tier 1 (outline) or tier 2
    stored extracted_text-> chunk_markdown   -> tier 3 (no pages)

Every row stores the author's text; the string that is EMBEDDED is
`chunk_header.embed_text(title, section, text)`, built here and nowhere
else at index time.
"""

import json
import re
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import log
from app.db.models import Paper, PaperChunkEmbedding, _now
from app.services.chunk_header import embed_text, section_path_text
from app.services.embedding_service import EmbeddingService
from app.services.paper_metadata_service import apply_metadata
from app.services.pdf_extraction import extract_document, outline_to_json, pages_to_json
from app.services.structured_chunker import ChunkRecord, chunk_markdown, chunk_pages

# Matches figure captions: "Figure 3." / "Fig. 3:" / "FIGURE 3 —" etc.
_FIGURE_CAPTION_RE = re.compile(
    r"(?:Figure|Fig\.?)\s+\d+[\.:\—\-]?\s+\S[^\n]{4,300}",
    re.IGNORECASE,
)

_embedding_svc = EmbeddingService()


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
    """
    from sqlalchemy import delete

    await db.execute(delete(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == paper_id))

    if not chunks:
        await db.commit()
        log.warning("paper_index_no_text", paper_id=paper_id)
        return 0

    embeddings = await _embedding_svc.embed_batch(
        [embed_text(title, c.section, c.text) for c in chunks], task_type="RETRIEVAL_DOCUMENT"
    )

    # ORM insert sends embedding as varchar; asyncpg rejects it against vector(768).
    # Raw INSERT with explicit CAST so Postgres receives the right type.
    now = _now()
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):
        vec_str = "[" + ",".join(str(x) for x in emb) + "]"
        await db.execute(
            text("""
            INSERT INTO paper_chunk_embeddings
                (id, paper_id, chunk_index, text, section, section_text, page,
                 embedding, model, created_at)
            VALUES
                (:id, :paper_id, :chunk_index, :text, :section, :section_text, :page,
                 CAST(:emb AS vector), :model, :now)
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
    await db.commit()
    log.info("paper_index_done", paper_id=paper_id, chunks=len(chunks))
    return len(chunks)


async def ingest(
    db: AsyncSession, paper_id: str, pdf_bytes: bytes, source_url: str | None = None
) -> int:
    """Extract, chunk, embed, and persist a PDF. Returns number of chunks stored.

    Stores `extracted_text` (plain markdown), `extracted_pages` and `outline`
    BEFORE index_chunks so text and chunks land in one transaction; a later
    re-chunk replays from the stored pages and never needs the PDF again.
    """
    ex = extract_document(pdf_bytes)
    paper = await db.get(Paper, paper_id)
    title = paper.title if paper is not None else ""
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
    n = await index_chunks(db, paper_id, records, title=title)

    # After indexing, in its own transaction: indexing is load-bearing and
    # must not be delayed or endangered by an LLM call.
    await apply_metadata(db, paper_id, ex.markdown, source_url)
    return n


async def index_manual(
    db: AsyncSession, paper_id: str, abstract: str | None, body: str | None
) -> int:
    """Index hand-entered text: abstract as its own chunk, then body chunks.

    The abstract chunk is skipped when its text already appears in the body.
    Manual text has no PDF and no pages; headings in the body still cut
    sections (tier 3).
    """
    body = body or ""
    paper = await db.get(Paper, paper_id)
    title = paper.title if paper is not None else ""
    records: list[ChunkRecord] = []
    if abstract and abstract.strip() and _norm(abstract) not in _norm(body):
        records.append(ChunkRecord(text=abstract.strip(), section=(), page=None, kind="abstract"))
    records += chunk_records_for_markdown(body)
    return await index_chunks(db, paper_id, records, title=title)
