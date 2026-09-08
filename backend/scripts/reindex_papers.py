"""Re-index paper chunks under the current chunker and embedding model.

Run:  docker compose exec -T backend python -m scripts.reindex_papers [--all] [--fetch] [--dry-run]

The `-m` form is required: pyproject.toml packages only `app*`.

Default: papers with no chunk at the configured embedding model (the
original purpose). `--all`: every paper, which is what a CHUNKER change
needs — the rows exist, they are just cut the old way. `--fetch`: allow one
network fetch per paper that has a `pdf_url` (or a retained PDF) but no
stored pages, so it gets real sections and pages (tier 1/2) instead of the
markdown-only tier 3. Fetches are paced `_FETCH_DELAY_S` apart WHENEVER ONE
WAS ATTEMPTED — success or failure. A failed fetch still hit arXiv (or
wherever `pdf_url` points) and falls back to tier 3 for that one paper; the
delay is about not hammering the remote host, which a failure does exactly
as much as a success. Pacing on the *resolved* tier instead (the first cut
of this script) was backwards: it sped up on failure, right when a run
should be backing off.

Tiers, best available per paper (spec §1):
    pages     stored extracted_pages/outline -> no PDF, no network
    pdf       paper_files blob or pdf_url     -> extract, STORE pages/outline
    markdown  extracted_text                  -> sections, no pages
    manual    abstract + body                 -> index_manual

A paper that resolves to no chunks AND has no manual content either (no
pages, no fetchable/fetched PDF, no stored markdown, no abstract, no body)
is SKIPPED, not indexed as empty. `index_chunks`/`index_manual` both
delete-then-write, so calling either on empty content would delete that
paper's existing rows and leave it with zero — silently emptying a paper
that was previously fine. Skips are counted and reported, never hidden.

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


async def _blob_bytes(row) -> bytes | None:
    """The retained PDF, read from the DB — never the network.

    `None` covers both "not `has_file`" and "`has_file` but the blob row is
    missing" (shouldn't happen, but that inconsistency must not masquerade
    as a network fetch when it falls through to `pdf_url` below)."""
    if not getattr(row, "has_file", False):
        return None
    async with SessionLocal() as db:
        blob = (await db.execute(_BLOB_SQL, {"id": row.id})).scalar_one_or_none()
    return bytes(blob) if blob else None


async def records_for(
    row, *, allow_fetch: bool
) -> tuple[list[ChunkRecord], str, dict | None, bool]:
    """(chunk records, tier actually used, papers-columns to persist or None,
    whether a NETWORK fetch was attempted this call).

    The fourth element is deliberately about the ATTEMPT, not the outcome:
    a failed `fetch_pdf` still resolves to the `"markdown"` tier (or
    `"manual"`, with nothing to fall back to), but it hit the network exactly
    as much as a successful one, and the caller's rate-limit delay has to
    fire either way. Deriving "did we fetch" from the resolved tier — the
    first cut of this function — collapses that distinction and paces
    successes only, which backs off in the wrong direction under failure.
    """
    tier = tier_for(row)
    if tier == "pages":
        pages = pages_from_json(row.extracted_pages)
        return (
            chunk_pages(pages, use_markers=bool(row.outline), caption_re=_FIGURE_CAPTION_RE),
            "pages",
            None,
            False,
        )
    if tier == "pdf" and allow_fetch:
        pdf = await _blob_bytes(row)
        fetch_attempted = False
        if pdf is None and row.pdf_url:
            fetch_attempted = True
            try:
                pdf, _served_from = await fetch_pdf(_pdf_url(row.pdf_url))
            except Exception as exc:  # noqa: BLE001 — one paper degrades, the run continues
                print(
                    f"    fetch failed ({type(exc).__name__}); falling back to markdown",
                    flush=True,
                )
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
                fetch_attempted,
            )
        if row.extracted_text:
            return (
                chunk_records_for_markdown(row.extracted_text),
                "markdown",
                None,
                fetch_attempted,
            )
        return [], "manual", None, fetch_attempted
    if row.extracted_text:
        return chunk_records_for_markdown(row.extracted_text), "markdown", None, False
    return [], "manual", None, False


def _has_manual_content(row) -> bool:
    return bool((row.abstract and row.abstract.strip()) or (row.body and row.body.strip()))


async def _reindex_one(row, *, allow_fetch: bool) -> tuple[int, str, bool] | None:
    """`None` means SKIPPED — no chunks were written and nothing existing
    was touched. Every other return commits a write."""
    records, tier, persist, fetch_attempted = await records_for(row, allow_fetch=allow_fetch)
    if not records and not _has_manual_content(row):
        # Nothing to chunk from pages/PDF/markdown AND no hand-entered text
        # either. `index_chunks`/`index_manual` both DELETE the paper's
        # existing rows before checking whether there is anything to insert
        # — the right behavior for a genuine re-chunk, but calling either
        # here would delete a paper's existing index and replace it with
        # nothing. Leave it alone; the caller counts and reports this.
        return None
    async with SessionLocal() as db:
        if tier == "manual":
            return await index_manual(db, row.id, row.abstract, row.body), tier, fetch_attempted
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
        return n, tier, fetch_attempted


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
    skipped: list[str] = []
    tiers: dict[str, int] = {}
    for i, row in enumerate(rows, 1):
        try:
            result = await _reindex_one(row, allow_fetch=args.fetch)
            if result is None:
                skipped.append(row.id)
                print(
                    f"[{i}/{len(rows)}] SKIP         {row.title[:60]}  (no content to index)",
                    flush=True,
                )
                continue
            n, tier, fetch_attempted = result
            tiers[tier] = tiers.get(tier, 0) + 1
            print(f"[{i}/{len(rows)}] {n:>4} chunks  {tier:<9} {row.title[:60]}", flush=True)
            if fetch_attempted:
                await asyncio.sleep(_FETCH_DELAY_S)
        except Exception as exc:  # noqa: BLE001 — report at the end, keep going
            failed.append((row.id, f"{type(exc).__name__}: {exc}"))
            print(
                f"[{i}/{len(rows)}] FAILED       {row.title[:60]}  ({type(exc).__name__})",
                flush=True,
            )

    print(f"\nre-indexed {len(rows) - len(failed) - len(skipped)} of {len(rows)} papers")
    print("by tier: " + ", ".join(f"{k}={v}" for k, v in sorted(tiers.items())))
    if skipped:
        print(f"skipped (no content to index): {len(skipped)}")
        for paper_id in skipped:
            print(f"  skipped: {paper_id}")
    for paper_id, err in failed:
        print(f"  failed: {paper_id}  {err}")


if __name__ == "__main__":
    asyncio.run(main())
