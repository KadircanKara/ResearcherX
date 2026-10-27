"""Backfill paper summaries through the OpenRouter Batch API, pinned to Sail.

Run:  docker compose exec -T backend python -m scripts.backfill_paper_summaries \\
          submit [--project-id UUID] [--force] [--limit N] [--chunk N] [--dry-run]
      docker compose exec -T backend python -m scripts.backfill_paper_summaries \\
          collect [--batch-id ID]

The `-m` form is required: `scripts` is not an installed package. The prod
image does not contain `scripts/`; backfilling production means a local
checkout pointed at the prod DATABASE_URL.

`submit` sends every ready paper with no summary row, a `failed` row, or one
written by an older prompt version (`--force`: every ready paper), never one
whose row is already `pending` in a batch. Each submitted chunk's rows are
recorded `pending` with the batch id in one transaction, AFTER the 202, so a
refused submit (402 included) records nothing.

`collect` is safe to run at any time, as often as wanted: a batch still
running is reported and left alone; a finished one is written through
`write_record`, which clears `batch_id`, so a second run finds nothing to
fetch. A result is written only while its row is still `pending` under THAT
batch id: a deleted paper, or a row a live ingest rewrote since, is skipped.
It ends with a repair pass that embeds ready rows missing a doc vector (or
holding one from another embedding model), with no LLM call.
"""

import argparse
import asyncio
import sys
from collections import Counter
from collections.abc import Sequence
from typing import Any

import httpx
from sqlalchemy import and_, null, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Paper, PaperStatus, PaperSummary, _now
from app.db.session import SessionLocal
from app.services import openrouter_batch as ob
from app.services.paper_summary_client import GeneratedSummary
from app.services.paper_summary_prompt import SUMMARY_VER, full_text, parse
from app.services.paper_summary_service import FAILED, READY, embed_summary, write_record

PENDING = "pending"
# Measured in the spike: Sail via the Batch API, deepseek-v4.1-flash.
SPIKE_USD_PER_PAPER = 0.0008
_ERROR_MAX = 500


def _dialect_insert(db: AsyncSession):
    return pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert


# --- submit ----------------------------------------------------------------


def _candidates(*, project_id: str | None, force: bool, limit: int | None):
    s = PaperSummary
    in_flight = and_(s.status == PENDING, s.batch_id.is_not(None))
    stmt = (
        select(Paper.id)
        .outerjoin(s, s.paper_id == Paper.id)
        .where(Paper.status == PaperStatus.READY)
        # NULL-safe: a paper with no row has every `s` column NULL.
        .where(or_(s.paper_id.is_(None), ~in_flight))
        .order_by(Paper.created_at, Paper.id)
    )
    if not force:
        stmt = stmt.where(
            or_(s.paper_id.is_(None), s.status == FAILED, s.summary_ver < SUMMARY_VER)
        )
    if project_id is not None:
        # String compare: papers.project_id is varchar.
        stmt = stmt.where(Paper.project_id == str(project_id))
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt


async def _load(db: AsyncSession, ids: Sequence[str]) -> list[ob.Paper]:
    rows = (
        await db.execute(
            select(Paper.id, Paper.title, Paper.abstract, Paper.extracted_pages, Paper.body).where(
                Paper.id.in_(ids)
            )
        )
    ).all()
    by_id = {
        r.id: (
            r.id,
            r.title,
            r.abstract,
            full_text(
                r.extracted_pages, r.body, r.abstract, cap=settings.paper_summary_input_chars
            ),
        )
        for r in rows
    }
    return [by_id[i] for i in ids if i in by_id]


async def _record_pending(db: AsyncSession, paper_ids: Sequence[str], batch_id: str) -> None:
    """ONE transaction. Core upserts that never touch `doc_embedding`."""
    ins = _dialect_insert(db)
    for paper_id in paper_ids:
        values = {"status": PENDING, "batch_id": batch_id, "error": None}
        await db.execute(
            ins(PaperSummary)
            .values(paper_id=paper_id, **values)
            .on_conflict_do_update(
                index_elements=["paper_id"], set_={**values, "updated_at": _now()}
            )
        )
    await db.commit()


async def run_submit(
    *,
    project_id: str | None = None,
    force: bool = False,
    limit: int | None = None,
    chunk: int | None = None,
    dry_run: bool = False,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = ob.MAX_BODY_BYTES,
) -> dict[str, Any]:
    chunk = chunk or settings.summary_batch_chunk
    model, provider = settings.summary_batch_model, settings.summary_batch_provider
    async with SessionLocal() as db:
        ids = list(
            (
                await db.execute(_candidates(project_id=project_id, force=force, limit=limit))
            ).scalars()
        )
    estimate = len(ids) * SPIKE_USD_PER_PAPER
    print(
        f"{len(ids)} papers to summarise on {model} via {provider}; "
        f"estimate ${estimate:.2f} at ${SPIKE_USD_PER_PAPER}/paper (spike rate)"
    )
    result: dict[str, Any] = {"papers": len(ids), "estimate_usd": estimate, "batch_ids": []}

    # Texts are loaded `chunk` papers at a time, so a large backfill never
    # holds every paper's text in memory; each slice is split again by bytes.
    n_chunks, n_chars = 0, 0
    for start in range(0, len(ids), chunk):
        async with SessionLocal() as db:
            papers = await _load(db, ids[start : start + chunk])
        n_chars += sum(len(p[3]) for p in papers)
        for part in ob.chunk_papers(
            papers, max_count=chunk, max_bytes=max_bytes, model=model, provider=provider
        ):
            n_chunks += 1
            if dry_run:
                continue
            batch_id = await ob.submit(
                ob.batch_body(part, model=model, provider=provider), client=client
            )
            # Printed before the DB write, so a batch whose rows fail to
            # record can still be found and collected by hand.
            print(f"  submitted {batch_id}: {len(part)} papers")
            async with SessionLocal() as db:
                await _record_pending(db, [p[0] for p in part], batch_id)
            result["batch_ids"].append(batch_id)

    result["chunks"] = n_chunks
    verb = "would submit" if dry_run else "submitted"
    print(f"{verb} {len(ids)} papers in {n_chunks} batch(es), {n_chars:,} chars of text")
    return result


# --- collect ---------------------------------------------------------------


def _error_text(value: Any, default: str) -> str:
    if isinstance(value, dict) and isinstance(value.get("message"), str):
        return value["message"][:_ERROR_MAX]
    return default


def _generated(result: dict, fallback_model: str) -> GeneratedSummary | None:
    """A record only from a 200, complete, schema-valid choice; else None."""
    response = result.get("response")
    if not isinstance(response, dict) or response.get("status_code") != 200:
        return None
    body = response.get("body")
    if not isinstance(body, dict):
        return None
    try:
        choice = body["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if choice.get("finish_reason") == "length" or not isinstance(content, str):
        return None
    record = parse(content, max_keywords=settings.paper_summary_max_keywords)
    if record is None:
        return None
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}

    def _int(v: Any) -> int:
        return v if isinstance(v, int) and not isinstance(v, bool) else 0

    cost = usage.get("cost")
    model = body.get("model")
    return GeneratedSummary(
        record=record,
        model=model if isinstance(model, str) and model else fallback_model,
        input_tokens=_int(usage.get("prompt_tokens")),
        output_tokens=_int(usage.get("completion_tokens")),
        cost_usd=float(cost)
        if isinstance(cost, (int, float)) and not isinstance(cost, bool)
        else None,
    )


async def _fail_pending(batch_id: str, message: str) -> int:
    async with SessionLocal() as db:
        res = await db.execute(
            update(PaperSummary)
            .where(PaperSummary.batch_id == batch_id, PaperSummary.status == PENDING)
            .values(status=FAILED, error=message[:_ERROR_MAX], batch_id=None, updated_at=_now())
        )
        await db.commit()
        return res.rowcount or 0


async def _still_ours(db: AsyncSession, paper_id: str, batch_id: str) -> bool:
    row = (
        await db.execute(
            select(PaperSummary.status, PaperSummary.batch_id).where(
                PaperSummary.paper_id == paper_id
            )
        )
    ).first()
    return row is not None and row.status == PENDING and row.batch_id == batch_id


async def _collect_batch(batch: dict, totals: Counter, providers: Counter) -> None:
    batch_id = batch["id"]
    status = batch["status"]
    if status in ob.FAILED_FINAL:
        message = _error_text(batch.get("error"), f"batch {status}")
        n = await _fail_pending(batch_id, f"batch {status}: {message}")
        totals["failed"] += n
        print(f"  {batch_id}: {status} ({message}); {n} rows marked failed")
        return
    results = batch.get("results")
    if not isinstance(results, list):
        # 402 on GET withholds results until the balance covers the charge.
        print(f"  {batch_id}: completed, results not available yet; left pending")
        return

    batch_model = batch.get("model") or settings.summary_batch_model
    usage = batch.get("usage") if isinstance(batch.get("usage"), dict) else {}
    cost = usage.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        totals["cost_micro"] += round(cost * 1_000_000)
    provider = batch.get("provider") or "unknown"
    providers[provider] += 1

    for result in results:
        paper_id = result.get("custom_id") if isinstance(result, dict) else None
        if not isinstance(paper_id, str):
            continue
        async with SessionLocal() as db:
            # Deleted papers cascade their row away; a row a live ingest
            # rewrote no longer carries this batch id. Both are skipped.
            paper = await db.get(Paper, paper_id)
            if paper is None or not await _still_ours(db, paper_id, batch_id):
                totals["skipped"] += 1
                continue
            if paper.status != PaperStatus.READY:
                # Re-ingesting, or its ingest failed: this text may be stale.
                # Failed, so the next submit (or the ingest) redoes it.
                await _fail_pending_one(db, paper_id, "paper was not ready at collect")
                totals["failed"] += 1
                continue
            generated = _generated(result, batch_model)
            status_written = await write_record(db, paper, generated)
        if status_written == READY:
            totals["llm" if generated is not None else "fallback"] += 1
        else:
            totals["failed"] += 1

    # A request with no result at all would leave its row pending forever.
    missing = await _fail_pending(batch_id, "no result for this paper in the batch")
    totals["failed"] += missing
    print(
        f"  {batch_id}: completed on {provider}, {len(results)} results"
        + (f", ${cost:.4f} billed" if isinstance(cost, (int, float)) else "")
        + (f", {missing} missing" if missing else "")
    )


async def _fail_pending_one(db: AsyncSession, paper_id: str, message: str) -> None:
    await db.execute(
        update(PaperSummary)
        .where(PaperSummary.paper_id == paper_id, PaperSummary.status == PENDING)
        .values(status=FAILED, error=message, batch_id=None, updated_at=_now())
    )
    await db.commit()


async def _repair_embeddings() -> int:
    """Ready rows with no doc vector, or one from another model: embed only."""
    async with SessionLocal() as db:
        ids = list(
            (
                await db.execute(
                    select(PaperSummary.paper_id).where(
                        PaperSummary.status == READY,
                        PaperSummary.summary.is_not(None),
                        or_(
                            PaperSummary.doc_embedding.is_(null()),
                            PaperSummary.model != settings.embedding_model,
                        ),
                    )
                )
            ).scalars()
        )
    repaired = 0
    for paper_id in ids:
        async with SessionLocal() as db:
            if await embed_summary(db, paper_id):
                repaired += 1
    if ids:
        print(f"repaired {repaired} of {len(ids)} missing or stale doc embeddings")
    return repaired


async def run_collect(
    *, batch_id: str | None = None, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    if batch_id is not None:
        batch_ids = [batch_id]
    else:
        async with SessionLocal() as db:
            batch_ids = list(
                (
                    await db.execute(
                        select(PaperSummary.batch_id)
                        .where(PaperSummary.status == PENDING, PaperSummary.batch_id.is_not(None))
                        .distinct()
                        .order_by(PaperSummary.batch_id)
                    )
                ).scalars()
            )
    print(f"{len(batch_ids)} batch(es) pending")

    totals: Counter = Counter()
    providers: Counter = Counter()
    for bid in batch_ids:
        try:
            batch = await ob.fetch(bid, client=client)
        except ob.BatchError as exc:
            if exc.status == 410:
                # Results expired (30 days): nothing left to collect.
                n = await _fail_pending(bid, f"batch results expired: {exc.message}")
                totals["failed"] += n
                print(f"  {bid}: results expired; {n} rows marked failed")
            else:
                print(f"  {bid}: fetch failed ({exc.status}: {exc.message}); left pending")
            continue
        batch["id"] = bid  # the id we asked for is the one rows carry
        if batch["status"] not in ob.FINAL:
            counts = batch.get("request_counts") or {}
            print(f"  {bid}: {batch['status']} {counts}; left pending")
            continue
        await _collect_batch(batch, totals, providers)

    repaired = await _repair_embeddings()
    cost = totals["cost_micro"] / 1_000_000
    print(
        f"done: {totals['llm']} llm, {totals['fallback']} abstract fallback, "
        f"{totals['failed']} failed, {totals['skipped']} skipped; "
        f"billed ${cost:.4f}; providers {dict(providers) or '{}'}"
    )
    return {
        "llm": totals["llm"],
        "fallback": totals["fallback"],
        "failed": totals["failed"],
        "skipped": totals["skipped"],
        "cost_usd": cost,
        "providers": dict(providers),
        "repaired": repaired,
    }


# --- CLI -------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("submit", help="submit batches for papers that need a summary")
    s.add_argument("--project-id", help="only this project's papers")
    s.add_argument("--force", action="store_true", help="every ready paper, not just those due")
    s.add_argument("--limit", type=int, help="at most N papers")
    s.add_argument(
        "--chunk", type=int, help=f"papers per batch (default {settings.summary_batch_chunk})"
    )
    s.add_argument("--dry-run", action="store_true", help="count and estimate; send nothing")
    c = sub.add_parser("collect", help="write finished batches' results")
    c.add_argument("--batch-id", help="only this batch")
    return parser


async def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "submit":
            await run_submit(
                project_id=args.project_id,
                force=args.force,
                limit=args.limit,
                chunk=args.chunk,
                dry_run=args.dry_run,
            )
        else:
            await run_collect(batch_id=args.batch_id)
    except ob.BatchError as exc:
        print(f"error: {exc.status}: {exc.message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
