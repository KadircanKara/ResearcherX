"""Paper ingest: PDF bytes → text chunks → embeddings → DB rows."""
import fitz  # pymupdf

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.models import PaperChunkEmbedding
from app.services.embedding_service import EmbeddingService

# Simple word-based chunking: ~384 words ≈ 512 tokens, 48-word overlap ≈ 64 tokens
_CHUNK_WORDS = 384
_OVERLAP_WORDS = 48

_embedding_svc = EmbeddingService()


def _extract_text(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes. sort=True handles IEEE double-column layout."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = [page.get_text("text", sort=True) for page in doc]
    doc.close()
    return "\n".join(pages)


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
    """
    from sqlalchemy import delete
    await db.execute(
        delete(PaperChunkEmbedding).where(PaperChunkEmbedding.paper_id == paper_id)
    )

    text = _extract_text(pdf_bytes)
    chunks = _chunk_text(text)
    if not chunks:
        log.warning("paper_ingest_no_text", paper_id=paper_id)
        return 0

    embeddings = await _embedding_svc.embed_batch(chunks, task_type="RETRIEVAL_DOCUMENT")

    rows = [
        PaperChunkEmbedding(
            paper_id=paper_id,
            chunk_index=i,
            text=chunk,
            embedding=str(emb),   # pgvector accepts "[0.1, 0.2, ...]" string format
        )
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
    ]
    db.add_all(rows)
    await db.commit()
    log.info("paper_ingest_done", paper_id=paper_id, chunks=len(rows))
    return len(rows)
