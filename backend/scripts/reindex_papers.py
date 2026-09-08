"""Re-index paper chunks under the current chunker and embedding model.

Run:  docker compose exec -T backend python -m scripts.reindex_papers [--all] [--fetch] [--dry-run]

The `-m` form is required: pyproject.toml packages only `app*`.

Default: papers with no chunk at the configured embedding model (the
original purpose). `--all`: every paper, which is what a CHUNKER change
needs — the rows exist, they are just cut the old way. `--fetch`: allow one
network fetch per paper that has a `pdf_url` (or a retained PDF) but no
stored pages, so it gets real sections and pages (tier 1/2) instead of the
markdown-only tier 3. Fetches are 3 s apart and a failure degrades that ONE
paper to tier 3 and continues.

Tiers, best available per paper (spec §1):
    pages     stored extracted_pages/outline -> no PDF, no network
    pdf       paper_files blob or pdf_url     -> extract, STORE pages/outline
    markdown  extracted_text                  -> sections, no pages
    manual    abstract + body                 -> index_manual

One paper per transaction, sequentially, so a failure leaves that one paper
unindexed and every other untouched.
"""

import argparse
import asyncio
import json

from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.paper_fetch_service import fetch_pdf
from app.services.paper_ingest_service import (
    _FIGURE_CAPTION_RE,
    chunk_records_for_markdown,
    index_chunks,
    index_manual,
)
from app.services.pdf_extraction import (
    extract_document,
    outline_to_json,
    pages_from_json,
    pages_to_json,
)
from app.services.structured_chunker import ChunkRecord, chunk_pages

_FETCH_DELAY_S = 3.0

_SELECT = """
    SELECT p.id, p.title, p.extracted_text, p.extracted_pages, p.outline, p.pdf_url,
           p.abstract, p.body,
           EXISTS (SELECT 1 FROM paper_files f WHERE f.paper_id = p.id) AS has_file
    FROM papers p
"""
_STALE_SQL = text(
    _SELECT
    + """
    WHERE NOT EXISTS (
        SELECT 1 FROM paper_chunk_embeddings c
        WHERE c.paper_id = p.id AND c.model = :model
    )
    ORDER BY p.created_at
"""
)
_ALL_SQL = text(_SELECT + " ORDER BY p.created_at")
_BLOB_SQL = text("SELECT blob FROM paper_files WHERE paper_id = :id")


def _pdf_url(url: str) -> str:
    """arXiv abstract pages serve HTML; the PDF lives under /pdf/."""
    return url.replace("arxiv.org/abs/", "arxiv.org/pdf/", 1)


def tier_for(row) -> str:
    if row.extracted_pages:
        return "pages"
    if row.pdf_url or getattr(row, "has_file", False):
        return "pdf"
    if row.extracted_text:
        return "markdown"
    return "manual"


async def _pdf_bytes(row) -> bytes | None:
    if getattr(row, "has_file", False):
        async with SessionLocal() as db:
            blob = (await db.execute(_BLOB_SQL, {"id": row.id})).scalar_one_or_none()
        if blob:
            return bytes(blob)
    if row.pdf_url:
        pdf, _served_from = await fetch_pdf(_pdf_url(row.pdf_url))
        return pdf
    return None


async def records_for(row, *, allow_fetch: bool) -> tuple[list[ChunkRecord], str, dict | None]:
    """(chunk records, tier actually used, papers-columns to persist or None)."""
    tier = tier_for(row)
    if tier == "pages":
        pages = pages_from_json(row.extracted_pages)
        return (
            chunk_pages(pages, use_markers=bool(row.outline), caption_re=_FIGURE_CAPTION_RE),
            "pages",
            None,
        )
    if tier == "pdf" and allow_fetch:
        try:
            pdf = await _pdf_bytes(row)
        except Exception as exc:  # noqa: BLE001 — one paper degrades, the run continues
            print(f"    fetch failed ({type(exc).__name__}); falling back to markdown", flush=True)
            pdf = None
        if pdf:
            ex = extract_document(pdf)
            return (
                chunk_pages(ex.pages, use_markers=ex.has_outline, caption_re=_FIGURE_CAPTION_RE),
                "pdf",
                {
                    "extracted_pages": pages_to_json(ex.pages),
                    "outline": outline_to_json(ex.outline),
                    "extracted_text": ex.markdown,
                },
            )
    if row.extracted_text:
        return chunk_records_for_markdown(row.extracted_text), "markdown", None
    return [], "manual", None


async def _reindex_one(row, *, allow_fetch: bool) -> tuple[int, str]:
    records, tier, persist = await records_for(row, allow_fetch=allow_fetch)
    async with SessionLocal() as db:
        if tier == "manual":
            return await index_manual(db, row.id, row.abstract, row.body), tier
        if persist:
            await db.execute(
                text(
                    "UPDATE papers SET extracted_pages = CAST(:pages AS json), "
                    "outline = CAST(:outline AS json), extracted_text = :md WHERE id = :id"
                ),
                {
                    "pages": json.dumps(persist["extracted_pages"]),
                    "outline": json.dumps(persist["outline"]),
                    "md": persist["extracted_text"],
                    "id": row.id,
                },
            )
        n = await index_chunks(db, row.id, records, title=row.title)
        return n, tier


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="list, embed nothing")
    parser.add_argument("--all", action="store_true", help="every paper, not only the stale ones")
    parser.add_argument("--fetch", action="store_true", help="allow one PDF fetch per paper")
    args = parser.parse_args()

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                _ALL_SQL if args.all else _STALE_SQL, {"model": settings.embedding_model}
            )
        ).fetchall()

    print(f"{len(rows)} paper(s) selected ({'all' if args.all else 'stale only'})")
    if args.dry_run:
        for row in rows:
            print(f"  {tier_for(row):<9} {row.id}  {row.title[:60]}")
        return

    failed: list[tuple[str, str]] = []
    tiers: dict[str, int] = {}
    for i, row in enumerate(rows, 1):
        try:
            n, tier = await _reindex_one(row, allow_fetch=args.fetch)
            tiers[tier] = tiers.get(tier, 0) + 1
            print(f"[{i}/{len(rows)}] {n:>4} chunks  {tier:<9} {row.title[:60]}", flush=True)
            if tier == "pdf" and args.fetch and not getattr(row, "has_file", False):
                await asyncio.sleep(_FETCH_DELAY_S)
        except Exception as exc:  # noqa: BLE001 — report at the end, keep going
            failed.append((row.id, f"{type(exc).__name__}: {exc}"))
            print(
                f"[{i}/{len(rows)}] FAILED       {row.title[:60]}  ({type(exc).__name__})",
                flush=True,
            )

    print(f"\nre-indexed {len(rows) - len(failed)} of {len(rows)} papers")
    print("by tier: " + ", ".join(f"{k}={v}" for k, v in sorted(tiers.items())))
    for paper_id, err in failed:
        print(f"  failed: {paper_id}  {err}")


if __name__ == "__main__":
    asyncio.run(main())
