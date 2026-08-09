"""Backfill authors/year/venue for papers ingested before metadata extraction.

Run:  docker compose exec -T backend python -m scripts.backfill_paper_metadata
      docker compose exec -T backend python -m scripts.backfill_paper_metadata --force

The `-m` form is required: pyproject.toml packages only `app*`, so `scripts`
isn't installed and file-path invocation drops cwd from sys.path, failing with
`ModuleNotFoundError: No module named 'app'`.

The prod image copies only app/, alembic/, and alembic.ini — `scripts/` is not
in it — so backfilling production means running this from a local checkout
pointed at the prod DATABASE_URL, not from the deployed container. `--force`
against that DATABASE_URL writes unconditionally: see its --help for what
that can overwrite.

Extraction runs through the same `apply_metadata` live ingest uses, so what
this writes is what production would have written.
"""

import argparse
import asyncio

from sqlalchemy import select

from app.db.models import Paper
from app.db.session import SessionLocal
from app.services.paper_metadata_service import SOURCE_NONE, apply_metadata


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "re-extract papers that already have metadata (default: skip them). "
            "Writes unconditionally: a noisier re-extraction than the one already "
            "stored will overwrite real authors with [] and reset metadata_source "
            "to 'none'. There is no dry-run or confirmation prompt."
        ),
    )
    args = parser.parse_args()

    async with SessionLocal() as db:
        papers = (await db.execute(select(Paper).order_by(Paper.created_at))).scalars().all()
        targets = [p for p in papers if args.force or p.metadata_source == SOURCE_NONE]
        # Materialise what each call needs before the loop: this `async with`
        # block closes the session before the loop runs, and the ORM
        # instances loaded through it would be detached from any session
        # thereafter.
        work = [(p.id, p.title, p.extracted_text, p.pdf_url) for p in targets]

    print(f"{len(papers)} papers, {len(work)} to process")

    skipped_no_text = 0
    stored = 0
    for paper_id, title, extracted_text, pdf_url in work:
        if not extracted_text or not extracted_text.strip():
            # Deliberately not re-fetching: an upload's PDF bytes were never
            # persisted, and a link may no longer resolve. Report it instead.
            print(f"  SKIP (no extracted_text)  {title[:70]}")
            skipped_no_text += 1
            continue

        # One paper per call, each committing on its own: this is a recovery
        # script run precisely when something has already gone wrong, and a
        # failure partway through should lose at most one paper.
        async with SessionLocal() as db:
            source = await apply_metadata(db, paper_id, extracted_text, pdf_url)
            paper = await db.get(Paper, paper_id)
            authors = list(paper.authors or []) if paper else []
            year = paper.year if paper else None
            venue = paper.venue if paper else None

        if source == SOURCE_NONE:
            print(f"  none                     {title[:70]}")
        else:
            stored += 1
            bits = [f"authors={len(authors)}"]
            if year is not None:
                bits.append(f"year={year}")
            if venue:
                bits.append(f"venue={venue[:40]!r}")
            print(f"  {source:<8} {' '.join(bits):<24} {title[:70]}")

    print(
        f"\ndone: {stored} stored, {len(work) - stored - skipped_no_text} produced nothing, "
        f"{skipped_no_text} skipped for missing text"
    )


if __name__ == "__main__":
    asyncio.run(main())
