"""Sources on disk to a searchable store.

    PDF / text -> chunks -> embeddings -> embeddings.npy + chunks.jsonl + bm25/

Chunking is IMPORTED from `app.services.structured_chunker` (via
`paper_ingest_service.chunk_records_for_markdown` for the text-file path,
`chunk_pages` directly for PDFs, sharing `paper_ingest_service`'s figure
caption regex), not copied: the section-boundary rule and the figure-caption
rule were measured into their current shape, and a second copy of them would
drift silently — the same reason `scripts/reindex_papers.py` imports these
rather than restating them.

The embedding call is injected. That keeps this module runnable against any
provider, and testable with none.

Everything is rebuilt from the sources on every run: this is a local index
of a handful of papers, and a full rebuild is both simpler and the
documented repair when anything looks wrong.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.config import settings
from app.core.logging import log
from app.local_rag.index import build_bm25, save_bm25
from app.local_rag.store import BM25_DIRNAME, Chunk, LocalStore, Paper
from app.services.chunk_header import embed_text
from app.services.paper_ingest_service import _FIGURE_CAPTION_RE, chunk_records_for_markdown
from app.services.pdf_extraction import extract_document
from app.services.structured_chunker import chunk_pages

Embed = Callable[[list[str]], Awaitable[list[list[float]]]]

_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
_PDF_SUFFIXES = {".pdf"}


@dataclass(frozen=True)
class IngestReport:
    n_papers: int
    n_chunks: int
    root: Path


def _read_source(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"no such source: {path}")
    suffix = path.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        return path.read_text(errors="replace")
    if suffix in _PDF_SUFFIXES:
        return extract_document(path.read_bytes())
    raise ValueError(
        f"cannot read {path.name}: only {sorted(_TEXT_SUFFIXES | _PDF_SUFFIXES)} are supported"
    )


async def ingest(
    *,
    sources: list[Path],
    root: Path,
    embed: Embed,
    embedding_model: str | None = None,
) -> IngestReport:
    """Rebuild the whole index from `sources`."""
    papers: list[Paper] = []
    chunks: list[Chunk] = []

    for path in sources:
        path = Path(path)
        src = _read_source(path)
        paper = Paper(paper_id=path.stem, title=path.stem, source_path=str(path))
        papers.append(paper)

        # A plain text/markdown source has no pages, so it goes through the
        # tier-3 markdown chunker (headings only, no page attribution). A PDF
        # carries `pages`/`has_outline` from pdf_extraction and goes through
        # the same tier-1/tier-2 path the database ingest uses — captions
        # included, in document order, so a "Figure 3" query hits the caption
        # directly instead of a paragraph that merely contains it.
        records = (
            chunk_records_for_markdown(src)
            if isinstance(src, str)
            else chunk_pages(src.pages, use_markers=src.has_outline, caption_re=_FIGURE_CAPTION_RE)
        )
        chunks.extend(
            Chunk(
                paper_id=paper.paper_id,
                paper_title=paper.title,
                chunk_index=i,
                text=r.text,
                section=r.section,
                page=r.page,
            )
            for i, r in enumerate(records)
        )
        log.info("local_rag_source_chunked", source=path.name, chunks=len(records))

    # Embedded text carries the same [Title | Section] header the database
    # path embeds (chunk_header.embed_text) — the fix for the failure that
    # motivated the header: a chunk whose section heading landed in the
    # PREVIOUS chunk carried none of the words that named it. BM25 still
    # indexes the raw chunk text (below), since a lexical index gains nothing
    # from a header repeating the title on every document.
    vectors = (
        np.array(
            await embed([embed_text(c.paper_title, c.section, c.text) for c in chunks]),
            dtype=np.float32,
        )
        if chunks
        else np.zeros((0, settings.embedding_dimensions), dtype=np.float32)
    )

    root = Path(root)
    LocalStore.save(
        root,
        papers=papers,
        chunks=chunks,
        vectors=vectors,
        embedding_model=embedding_model or settings.embedding_model,
    )
    save_bm25(build_bm25([c.text for c in chunks]), root / BM25_DIRNAME)

    log.info("local_rag_ingest_done", papers=len(papers), chunks=len(chunks), root=str(root))
    return IngestReport(n_papers=len(papers), n_chunks=len(chunks), root=root)
