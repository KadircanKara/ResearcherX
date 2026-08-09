"""Bulk-load a pre-downloaded arXiv corpus into a project, for RAG benchmarking.

Run:  docker compose exec -T backend python -m scripts.bulk_ingest_arxiv \
          --project-id <uuid> [--dir /app/stub-papers/arxiv] [--limit N]

The `-m` form is required: pyproject.toml packages only `app*`, so `scripts` is
not installed and file-path invocation drops cwd from sys.path.

Expects `<dir>/corpus.json` — a list of Semantic Scholar records, each with
`arxiv_id`, `title`, `abstract`, `authors`, `year`, `venue`, `pdf_path` — plus
the PDFs themselves. Produced by the fetch/download pair under the session
scratchpad; regenerate them there, not here.

Two deliberate differences from the `/papers/{id}/ingest-from-url` endpoint:

- **Metadata comes from Semantic Scholar, not `apply_metadata`.** S2 already
  gives authoritative authors/year/venue, so the per-paper LLM extraction call
  is pure cost: ~100 calls would exhaust the Groq free tier's 100k tokens/day
  on their own (see CLAUDE.md, "Rate-limit budget"), starving the chat pipeline
  this corpus exists to test. `metadata_source` records "s2" so the provenance
  stays greppable.
- **No HTTP layer**, so the per-IP limiter in `core/security.py` doesn't reject
  the run partway through.

Chunking is NOT re-implemented: it calls the same `_chunk_text` /
`_extract_figure_captions` / `index_chunks` that `ingest()` uses. A corpus
chunked differently from production would make every retrieval number a
measurement of this script instead of the system.
"""

import argparse
import asyncio
import json
import pathlib

from sqlalchemy import select

from app.core.config import settings
from app.db.models import Paper, PaperSource
from app.db.session import SessionLocal
from app.services.paper_ingest_service import (
    _chunk_text,
    _extract_figure_captions,
    _extract_markdown,
    index_chunks,
)

_TITLE_MAX = 512  # papers.title is String(512)


def _abs_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{arxiv_id}"


async def _already_loaded(project_id: str) -> set[str]:
    """pdf_urls already in this project — makes the script re-runnable."""
    async with SessionLocal() as db:
        rows = (
            await db.execute(select(Paper.pdf_url).where(Paper.project_id == project_id))
        ).scalars()
        return {url for url in rows if url}


async def _ingest_one(project_id: str, rec: dict, pdf: pathlib.Path) -> int:
    md = _extract_markdown(pdf.read_bytes())
    chunks = _chunk_text(md) + _extract_figure_captions(md)

    async with SessionLocal() as db:
        paper = Paper(
            project_id=project_id,
            title=rec["title"][:_TITLE_MAX],
            abstract=rec.get("abstract"),
            authors=rec.get("authors") or [],
            year=rec.get("year"),
            venue=rec.get("venue") or "arXiv",
            metadata_source="s2",
            extracted_text=md,
            pdf_url=_abs_url(rec["arxiv_id"]),
            source=PaperSource.LINK,
        )
        db.add(paper)
        await db.flush()  # assigns paper.id without committing
        # index_chunks commits, carrying the flushed Paper with it — so an
        # embedding failure rolls back the paper row too, exactly as
        # create_paper does. No orphan rows whose chunks never got written.
        return await index_chunks(db, paper.id, chunks)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk-load an arXiv corpus into a project.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--dir", default="/app/stub-papers/arxiv")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = pathlib.Path(args.dir)
    records = json.loads((root / "corpus.json").read_text())
    if args.limit:
        records = records[: args.limit]

    seen = await _already_loaded(args.project_id)
    pending = [r for r in records if _abs_url(r["arxiv_id"]) not in seen]
    print(
        f"{len(records)} in corpus, {len(records) - len(pending)} already loaded, "
        f"{len(pending)} to ingest  (model: {settings.embedding_model})"
    )
    if args.dry_run:
        for rec in pending[:10]:
            print(f"  would ingest {rec['arxiv_id']}  {rec['title'][:64]}")
        if len(pending) > 10:
            print(f"  ... and {len(pending) - 10} more")
        return

    total_chunks = 0
    failures: list[tuple[str, str]] = []
    for i, rec in enumerate(pending, 1):
        pdf = root / f"{rec['arxiv_id'].replace('/', '_')}.pdf"
        if not pdf.exists():
            failures.append((rec["arxiv_id"], "pdf missing"))
            print(f"[{i}/{len(pending)}] SKIP  {rec['arxiv_id']}  pdf missing", flush=True)
            continue
        try:
            n = await _ingest_one(args.project_id, rec, pdf)
            total_chunks += n
            print(
                f"[{i}/{len(pending)}] {n:>4} chunks  {rec['arxiv_id']:<12} {rec['title'][:56]}",
                flush=True,
            )
        except Exception as exc:
            failures.append((rec["arxiv_id"], f"{type(exc).__name__}: {str(exc)[:120]}"))
            print(
                f"[{i}/{len(pending)}] FAIL  {rec['arxiv_id']}  {type(exc).__name__}",
                flush=True,
            )

    print(f"\ningested {len(pending) - len(failures)} papers, {total_chunks} chunks")
    for arxiv_id, err in failures:
        print(f"  failed: {arxiv_id}  {err}")


if __name__ == "__main__":
    asyncio.run(main())
