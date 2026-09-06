"""Sources on disk to a searchable store.

    PDF / text -> chunks -> embeddings -> embeddings.npy + chunks.jsonl + bm25/

Chunking is IMPORTED from `app.services.paper_ingest_service`, not copied:
the sliding window and the figure-caption rule were measured into their
current shape, and a second copy of them would drift silently — the same
reason `scripts/reindex_papers.py` imports the same two functions rather
than restating them.

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
from app.services.paper_ingest_service import (
    _chunk_text,
    _extract_figure_captions,
    _extract_markdown,
)

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
        return _extract_markdown(path.read_bytes())
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
        text = _read_source(path)
        paper = Paper(paper_id=path.stem, title=path.stem, source_path=str(path))
        papers.append(paper)

        # Figure captions after the body chunks, as the database path does:
        # a caption is its own retrievable unit, so a "Figure 3" query hits
        # it directly instead of a paragraph that merely contains it.
        texts = _chunk_text(text) + _extract_figure_captions(text)
        chunks.extend(
            Chunk(
                paper_id=paper.paper_id,
                paper_title=paper.title,
                chunk_index=i,
                text=chunk_text,
            )
            for i, chunk_text in enumerate(texts)
        )
        log.info("local_rag_source_chunked", source=path.name, chunks=len(texts))

    vectors = (
        np.array(await embed([c.text for c in chunks]), dtype=np.float32)
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
