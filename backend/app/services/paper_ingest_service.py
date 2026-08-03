"""Paper ingest: PDF bytes → text chunks → embeddings → DB rows."""

import re
import fitz  # pymupdf
import pymupdf4llm
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.models import PaperChunkEmbedding, _now
from app.services.embedding_service import EmbeddingService

# Simple word-based chunking: ~384 words ≈ 512 tokens, 48-word overlap ≈ 64 tokens
_CHUNK_WORDS = 384
_OVERLAP_WORDS = 48

# Matches figure captions: "Figure 3." / "Fig. 3:" / "FIGURE 3 —" etc.
_FIGURE_CAPTION_RE = re.compile(
    r"(?:Figure|Fig\.?)\s+\d+[\.:\—\-]?\s+\S[^\n]{4,300}",
    re.IGNORECASE,
)

_embedding_svc = EmbeddingService()


def _extract_markdown(pdf_bytes: bytes) -> str:
    """Convert PDF to text-only markdown.

    Images are deliberately not embedded: `embed_images=True` inlines every
    figure as a base64 data URI, which bloats the body and the chunks with
    text nothing downstream reads (we don't analyze images). The default
    still keeps figure captions, which is the part that carries meaning.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    md = pymupdf4llm.to_markdown(doc)
    doc.close()
    return md


def _extract_figure_captions(md: str) -> list[str]:
    """Return each figure caption as a standalone chunk for targeted retrieval."""
    return [m.group(0).strip() for m in _FIGURE_CAPTION_RE.finditer(md)]


def _chunk_text(text: str) -> list[str]:
    """Sliding-window word-based chunking."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + _CHUNK_WORDS, len(words))
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end == len(words):
            break
        start += _CHUNK_WORDS - _OVERLAP_WORDS
    return chunks


async def ingest(db: AsyncSession, paper_id: str, pdf_bytes: bytes) -> int:
    """Extract, chunk, embed, and persist. Returns number of chunks stored.

    Idempotent: deletes existing chunks for this paper before re-inserting.
    Figure captions are added as dedicated chunks after regular text chunks so
    similarity search on "Figure N" queries hits them directly.
    """
    from sqlalchemy import delete

    await db.execute(delete(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == paper_id))

    md = _extract_markdown(pdf_bytes)
    figure_chunks = _extract_figure_captions(md)
    text_chunks = _chunk_text(md)
    chunks = text_chunks + figure_chunks

    if not chunks:
        log.warning("paper_ingest_no_text", paper_id=paper_id)
        return 0

    log.info(
        "paper_ingest_chunks",
        paper_id=paper_id,
        text_chunks=len(text_chunks),
        figure_chunks=len(figure_chunks),
    )

    embeddings = await _embedding_svc.embed_batch(chunks, task_type="RETRIEVAL_DOCUMENT")

    # ORM insert sends embedding as varchar; asyncpg rejects it against vector(768).
    # Use raw INSERT with explicit CAST so Postgres receives the right type.
    now = _now()
    n_chunks = len(chunks)
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        vec_str = "[" + ",".join(str(x) for x in emb) + "]"
        await db.execute(
            text("""
            INSERT INTO paper_chunk_embeddings
                (id, paper_id, chunk_index, text, embedding, created_at)
            VALUES
                (:id, :paper_id, :chunk_index, :text, CAST(:emb AS vector), :now)
            ON CONFLICT (paper_id, chunk_index) DO UPDATE
                SET text = EXCLUDED.text, embedding = EXCLUDED.embedding
        """),
            {
                "id": str(uuid.uuid4()),
                "paper_id": paper_id,
                "chunk_index": i,
                "text": chunk,
                "emb": vec_str,
                "now": now,
            },
        )
    await db.commit()
    log.info("paper_ingest_done", paper_id=paper_id, chunks=n_chunks)
    return n_chunks
