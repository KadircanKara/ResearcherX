"""Resolve and persist a paper's structured metadata (authors, year, venue).

Live ingest and scripts/backfill_paper_metadata.py both call `apply_metadata`,
so the accuracy that evals/metadata/run_eval.py measures is the accuracy
production produces. Keep it that way — a backfill that extracts differently
from ingest would make the evaluation measure a path nobody runs.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.models import Paper
from app.services.title_extraction_service import (
    PaperMeta,
    extract_doi,
    extract_metadata_from_text,
    fetch_crossref_meta,
)

SOURCE_CROSSREF = "crossref"
SOURCE_LLM = "llm"
SOURCE_NONE = "none"


def _is_empty(meta: PaperMeta) -> bool:
    """True when nothing beyond title/abstract was found. Those two are stored
    elsewhere and are not what this feature exists to answer."""
    return not meta.authors and meta.year is None and not meta.venue


async def resolve_metadata(markdown: str, source_url: str | None = None) -> tuple[PaperMeta, str]:
    """Return (metadata, source label).

    Crossref wins when it names authors — it is a publisher record, not a guess.
    A Crossref record with NO author list is not authoritative for the field
    this feature exists to answer, so it falls through to the LLM rather than
    locking in an empty answer behind an authoritative-looking label.
    """
    doi = extract_doi(source_url) if source_url else None
    if doi:
        crossref = await fetch_crossref_meta(doi)
        if crossref is not None and crossref.authors:
            return crossref, SOURCE_CROSSREF

    meta = await extract_metadata_from_text(markdown)
    return meta, (SOURCE_NONE if _is_empty(meta) else SOURCE_LLM)


async def apply_metadata(
    db: AsyncSession, paper_id: str, markdown: str, source_url: str | None = None
) -> str:
    """Extract metadata and persist it on the paper. Never raises.

    Returns the stored source label, or SOURCE_NONE if nothing was stored.
    """
    try:
        meta, source = await resolve_metadata(markdown, source_url)
        paper = await db.get(Paper, paper_id)
        if paper is None:
            return SOURCE_NONE
        paper.authors = meta.authors
        paper.year = meta.year
        paper.venue = meta.venue
        paper.metadata_source = source
        await db.commit()
        log.info(
            "paper_metadata_stored",
            paper_id=paper_id,
            source=source,
            authors=len(meta.authors),
            year=meta.year,
            has_venue=bool(meta.venue),
        )
        return source
    except Exception:
        log.warning("paper_metadata_failed", paper_id=paper_id, exc_info=True)
        try:
            await db.rollback()
        except Exception:
            # This function's contract is "never raises" — a session too broken
            # to roll back must not take the ingest down with it.
            log.warning("paper_metadata_rollback_failed", paper_id=paper_id, exc_info=True)
        return SOURCE_NONE
