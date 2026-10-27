"""Where a paper stands in each retrieval index: dense vectors and keyword search.

PURE: no DB, no ORM. `api/v1/projects.py` gathers the inputs in grouped
queries for the whole list and calls these per row.

Dense status reads the paper's ingest status and its chunk count under the
configured embedding model: a chunk row IS its embedding, so a ready paper
with chunks is embedded and one without has nothing chat can find.

Keyword status follows `settings.sparse_backend`, because that is what chat
actually searches:
- `postgres`: `tsv` is a generated column on the chunk row, so a paper is
  keyword-searchable exactly when it is embedded.
- `bm25s`: the project index is rebuilt in the background after a debounce,
  so an embedded paper can be waiting for the next build. It is in the
  served index only when its newest chunk predates the published build and
  no change after its chunks is still pending.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Dense: embedded | pending | failed | none
# Keyword: indexed | queued | pending | failed | none


@dataclass(frozen=True)
class Bm25Index:
    """The project's `bm25_index_state` row, as far as a paper cares."""

    current_version: int
    built_at: datetime | None
    dirty_since: datetime | None


def dense_status(paper_status: str, chunks: int) -> str:
    if paper_status in ("pending", "failed"):
        return paper_status
    return "embedded" if chunks > 0 else "none"


def keyword_status(
    paper_status: str,
    chunks: int,
    newest_chunk_at: datetime | None,
    *,
    backend: str,
    index: Bm25Index | None,
) -> str:
    dense = dense_status(paper_status, chunks)
    if dense != "embedded":
        return dense
    if backend != "bm25s":
        return "indexed"
    if index is None or index.current_version == 0 or index.built_at is None:
        return "queued"
    if newest_chunk_at is None or newest_chunk_at > index.built_at:
        return "queued"
    if index.dirty_since is not None and newest_chunk_at >= index.dirty_since:
        return "queued"
    return "indexed"
