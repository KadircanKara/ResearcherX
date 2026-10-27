"""Backfill paper summaries through the OpenRouter Batch API, pinned to Sail.

Run (from /app in the backend container, `docker compose exec -T backend ...`):

  python -m scripts.backfill_paper_summaries submit [--project-id UUID] [--force]
      [--limit N] [--chunk N] [--max-cost USD] [--dry-run]
  python -m scripts.backfill_paper_summaries collect [--batch-id ID]
      [--adopt submitting:<nonce>] [--release submitting:<nonce>]
      [--abandon ID [--force-abandon]]
  python -m scripts.backfill_paper_summaries list [--limit N]

The `-m` form is required: `scripts` is not an installed package. The prod
image does not contain `scripts/`; backfilling production means a local
checkout pointed at the prod DATABASE_URL.

Row states. A non-null `batch_id` means "in flight". On a `pending` row it is
a paper with no usable summary; on a `ready` row it is a re-summary in
flight over a GOOD summary, and nothing that goes wrong with the batch may
touch that summary: every failure path only clears `batch_id` there.

`submit` selects ready papers with no row, a `failed` row, a `pending` row
not in flight, or an older prompt version (`--force`: every ready paper),
never a row already in flight. It refuses when the larger of two estimates
(per paper, per char; both from the spike) exceeds `--max-cost`. Per chunk:

  1. mark the rows `batch_id='submitting:<uuid>'` in one transaction;
  2. POST;
  3. 202 with an id: re-point the rows to the real id. A definite refusal
     (400/401/402/403/404/413/422, or no key): release the rows (a pending
     row becomes `failed`). Anything else (5xx, 408, 429, a gateway
     timeout, an unreadable 202, a dropped connection): the batch may
     exist, so the marker STAYS, which keeps a re-run from paying twice.
     Find the batch with `list`, then `collect --batch-id ID --adopt
     <marker>` (checked against GET: it must exist and its request total
     must match the marker's rows), or `collect --release <marker>` when no
     batch was created.

`collect` is safe to run at any time, as often as wanted: a batch still
running is reported and left alone; a finished one is written through
`write_record`, which clears `batch_id`, so a second run finds nothing to
fetch. A result is written only while its row is still in flight under
THAT batch id (row locked FOR UPDATE): a deleted paper, or a row a live
ingest rewrote since, is skipped. It ends with a repair pass that embeds
ready rows missing a doc vector (or holding one from another embedding
model), with no LLM call.

Both `submit` and `collect` hold a Postgres advisory lock for the whole run.
"""

import argparse
import asyncio
import contextlib
import sys
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import case, null, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Paper, PaperStatus, PaperSummary, _now
from app.db.session import SessionLocal, engine
from app.services import openrouter_batch as ob
from app.services.paper_summary_client import GeneratedSummary
from app.services.paper_summary_prompt import SUMMARY_VER, full_text, parse
from app.services.paper_summary_service import FAILED, READY, embed_summary, write_record

PENDING = "pending"
MARKER_PREFIX = "submitting:"
# Measured in the spike (Sail via the Batch API, deepseek-v4.1-flash, 20
# papers, one per length ventile): $0.0008 per paper on average. The spike
# doc records each paper's length; the 20 total 1,119,154 chars, so the same
# spend is $0.016 / 1,119,154 chars. The per-char figure scales with the
# corpus' real lengths, the per-paper one does not; submit takes the larger.
SPIKE_USD_PER_PAPER = 0.0008
SPIKE_USD_PER_CHAR = 0.0008 * 20 / 1_119_154
DEFAULT_MAX_COST = 3.0
# One key for both commands: a submit and a collect must not interleave
# either (collect's sweep for missing results would race submit's marking).
LOCK_KEY = 0x5EAF_5A11
_ERROR_MAX = 500


class LockBusy(Exception):
    """Another submit or collect holds the advisory lock."""


class CostExceeded(Exception):
    """The estimate is over `--max-cost`; nothing was sent."""


class OutcomeUnknown(Exception):
    """The POST failed without a definite answer; the marker was kept."""

    def __init__(self, marker: str) -> None:
        super().__init__(marker)
        self.marker = marker


def _dialect_insert(db: AsyncSession):
    return pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert


async def _try_lock(conn) -> bool:
    if conn.dialect.name != "postgresql":
        return True  # sqlite (tests): a single process, nothing to guard
    return bool(
        (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})).scalar()
    )


@contextlib.asynccontextmanager
async def _run_lock() -> AsyncIterator[None]:
    """Hold the advisory lock on a dedicated connection for the whole run."""
    async with engine.connect() as conn:
        locked = await _try_lock(conn)
        # Session-level lock: it outlives this transaction, so end it at once
        # rather than leave the connection idle in a transaction for the run.
        await conn.commit()
        if not locked:
            raise LockBusy("another backfill submit/collect is running; try again when it ends")
        try:
            yield
        finally:
            await _unlock(conn)


async def _unlock(conn) -> None:
    """Best-effort: never masks the run's own exception. Closing the
    connection releases a session lock anyway."""
    if conn.dialect.name != "postgresql":
        return
    try:
        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        await conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"warning: advisory unlock failed ({type(exc).__name__})", file=sys.stderr)


# --- row state ---------------------------------------------------------------


async def _release(
    batch_id: str,
    *,
    error: str,
    fail: bool,
    only: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
) -> tuple[int, int]:
    """Take rows out of flight. A `ready` row only loses its `batch_id` (its
    summary is good). Any other row becomes `failed` (`fail`) or stays
    `pending` with no batch, which the next submit re-selects either way.
    Returns (ready rows kept, other rows released)."""
    s = PaperSummary
    where = [s.batch_id == batch_id]
    if only is not None:
        where.append(s.paper_id.in_(list(only)))
    if exclude:
        where.append(s.paper_id.not_in(list(exclude)))
    async with SessionLocal() as db:
        kept = await db.execute(
            update(s).where(*where, s.status == READY).values(batch_id=None, updated_at=_now())
        )
        released = await db.execute(
            update(s)
            .where(*where, s.status != READY)
            .values(
                status=FAILED if fail else PENDING,
                error=error[:_ERROR_MAX],
                batch_id=None,
                updated_at=_now(),
            )
        )
        await db.commit()
    return kept.rowcount or 0, released.rowcount or 0


async def _repoint(old: str, new: str) -> int:
    async with SessionLocal() as db:
        res = await db.execute(
            update(PaperSummary)
            .where(PaperSummary.batch_id == old)
            .values(batch_id=new, updated_at=_now())
        )
        await db.commit()
    return res.rowcount or 0


async def _mark(paper_ids: Sequence[str], marker: str) -> None:
    """ONE transaction, BEFORE the POST. Core upserts that never touch
    `doc_embedding`. A ready row keeps its status, summary and error; any
    other row (or a new one) becomes `pending`."""
    s = PaperSummary
    async with SessionLocal() as db:
        ins = _dialect_insert(db)
        for paper_id in paper_ids:
            await db.execute(
                ins(s)
                .values(paper_id=paper_id, status=PENDING, batch_id=marker, error=None)
                .on_conflict_do_update(
                    index_elements=["paper_id"],
                    set_={
                        "batch_id": marker,
                        # The existing row's values, in both dialects.
                        "status": case((s.status == READY, READY), else_=PENDING),
                        "error": case((s.status == READY, s.error), else_=None),
                        "updated_at": _now(),
                    },
                )
            )
        await db.commit()


# --- submit ----------------------------------------------------------------


def _candidates(*, project_id: str | None, force: bool, limit: int | None):
    s = PaperSummary
    stmt = (
        select(Paper.id)
        .outerjoin(s, s.paper_id == Paper.id)
        .where(Paper.status == PaperStatus.READY)
        # Never a row in flight (real batch id or submit marker). NULL-safe:
        # a paper with no row has every `s` column NULL.
        .where(s.batch_id.is_(None))
        .order_by(Paper.created_at, Paper.id)
    )
    if not force:
        stmt = stmt.where(
            or_(
                s.paper_id.is_(None),
                s.status.in_([FAILED, PENDING]),
                s.summary_ver < SUMMARY_VER,
            )
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


async def _plan(
    ids: Sequence[str], *, chunk: int, max_bytes: int, model: str, provider: str
) -> tuple[list[list[str]], int]:
    """Batches as id lists, plus the total chars. Texts are loaded `chunk`
    papers at a time and dropped, so the plan never holds them all."""
    groups: list[list[str]] = []
    chars = 0
    for start in range(0, len(ids), chunk):
        async with SessionLocal() as db:
            papers = await _load(db, ids[start : start + chunk])
        chars += sum(len(p[3]) for p in papers)
        for part in ob.chunk_papers(
            papers, max_count=chunk, max_bytes=max_bytes, model=model, provider=provider
        ):
            groups.append([p[0] for p in part])
    return groups, chars


def _estimates(papers: int, chars: int) -> dict[str, float]:
    per_paper = papers * SPIKE_USD_PER_PAPER
    per_char = chars * SPIKE_USD_PER_CHAR
    return {"per_paper": per_paper, "per_char": per_char, "max": max(per_paper, per_char)}


def _print_unknown(marker: str, n: int, why: str) -> None:
    print(
        f"outcome unknown ({why}): check GET /api/v1/batches before resubmitting "
        f"({n} papers held by {marker}). Then either "
        f"`collect --batch-id <id> --adopt {marker}` or `collect --release {marker}`.",
        file=sys.stderr,
    )


async def _submit_group(
    ids: Sequence[str], *, model: str, provider: str, client: httpx.AsyncClient | None
) -> str:
    async with SessionLocal() as db:
        papers = await _load(db, ids)
    body = ob.batch_body(papers, model=model, provider=provider)
    sent = [p[0] for p in papers]
    marker = f"{MARKER_PREFIX}{uuid.uuid4()}"
    await _mark(sent, marker)
    try:
        batch_id = await ob.submit(body, client=client)
    except ob.BatchError as exc:
        if exc.definite:
            # A refusal that proves no batch exists. Nothing was charged.
            await _release(marker, error=f"submit refused: {exc.status}: {exc.message}", fail=True)
            raise
        # 5xx, 408, 429, a gateway timeout, an unreadable 202: the batch may
        # exist. Keep the marker.
        _print_unknown(marker, len(sent), f"{exc.status}: {exc.message}")
        raise OutcomeUnknown(marker) from exc
    except BaseException as exc:
        _print_unknown(marker, len(sent), type(exc).__name__)
        if isinstance(exc, Exception):
            raise OutcomeUnknown(marker) from exc
        raise
    print(f"  submitted {batch_id}: {len(sent)} papers")
    try:
        await _repoint(marker, batch_id)
    except BaseException:
        print(
            f"rows still held by {marker}: run `collect --batch-id {batch_id} --adopt {marker}`",
            file=sys.stderr,
        )
        raise
    return batch_id


async def run_submit(
    *,
    project_id: str | None = None,
    force: bool = False,
    limit: int | None = None,
    chunk: int | None = None,
    dry_run: bool = False,
    max_cost: float = DEFAULT_MAX_COST,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = ob.MAX_BODY_BYTES,
) -> dict[str, Any]:
    async with _run_lock():
        return await _submit(
            project_id=project_id,
            force=force,
            limit=limit,
            chunk=chunk or settings.summary_batch_chunk,
            dry_run=dry_run,
            max_cost=max_cost,
            client=client,
            max_bytes=max_bytes,
        )


async def _submit(*, project_id, force, limit, chunk, dry_run, max_cost, client, max_bytes):
    model, provider = settings.summary_batch_model, settings.summary_batch_provider
    if not dry_run and not settings.summary_api_key:
        raise ob.BatchError(0, "SUMMARY_API_KEY is not set")
    async with SessionLocal() as db:
        ids = list(
            (
                await db.execute(_candidates(project_id=project_id, force=force, limit=limit))
            ).scalars()
        )
    groups, chars = await _plan(
        ids, chunk=chunk, max_bytes=max_bytes, model=model, provider=provider
    )
    est = _estimates(len(ids), chars)
    print(f"{len(ids)} papers, {chars:,} chars, {len(groups)} batch(es) on {model} via {provider}")
    print(f"estimate per paper: ${est['per_paper']:.2f} (${SPIKE_USD_PER_PAPER}/paper, spike)")
    print(f"estimate per char:  ${est['per_char']:.2f} (${SPIKE_USD_PER_CHAR:.3e}/char, spike)")
    print(f"estimate (max):     ${est['max']:.2f}; --max-cost ${max_cost:.2f}")
    result: dict[str, Any] = {
        "papers": len(ids),
        "chars": chars,
        "chunks": len(groups),
        "estimate_usd": est["max"],
        "estimates": est,
        "batch_ids": [],
    }
    over = est["max"] > max_cost
    if dry_run:
        print(
            "dry run: nothing sent"
            + ("; a real run would REFUSE (over --max-cost)" if over else "")
        )
        return result
    if over:
        raise CostExceeded(f"estimate ${est['max']:.2f} exceeds --max-cost ${max_cost:.2f}")

    for group in groups:
        result["batch_ids"].append(
            await _submit_group(group, model=model, provider=provider, client=client)
        )
    print(f"submitted {len(ids)} papers in {len(result['batch_ids'])} batch(es)")
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


def _ours_stmt(paper_id: str):
    # FOR UPDATE: the row stays locked until write_record commits, so a live
    # summary task cannot land between this check and the batch write.
    return (
        select(PaperSummary.status, PaperSummary.batch_id)
        .where(PaperSummary.paper_id == paper_id)
        .with_for_update()
    )


async def _still_ours(db: AsyncSession, paper_id: str, batch_id: str) -> str | None:
    """The row's status while it is in flight under `batch_id`, else None."""
    row = (await db.execute(_ours_stmt(paper_id))).first()
    return row.status if row is not None and row.batch_id == batch_id else None


async def _batch_paper_ids(batch_id: str) -> list[str]:
    async with SessionLocal() as db:
        return list(
            (
                await db.execute(
                    select(PaperSummary.paper_id).where(PaperSummary.batch_id == batch_id)
                )
            ).scalars()
        )


async def _fail_batch(batch_id: str, status: str, message: str, totals: Counter) -> None:
    """A whole batch ended without results. A validation failure names the
    request(s) at fault; when it does, only those rows fail and the rest are
    released for the next submit. Ready rows keep their summary either way."""
    named: list[str] = []
    if status == "failed":
        named = [pid for pid in await _batch_paper_ids(batch_id) if pid in message]
    error = f"batch {status}: {message}"
    if named:
        kept_f, failed = await _release(batch_id, error=error, fail=True, only=named)
        kept_r, released = await _release(
            batch_id, error=f"released: {error}", fail=False, exclude=named
        )
        totals["failed"] += failed
        totals["released"] += released
        totals["kept"] += kept_f + kept_r
        print(
            f"  {batch_id}: {status} ({message}); {failed} named rows failed, "
            f"{released} released, {kept_f + kept_r} ready rows kept"
        )
        return
    kept, failed = await _release(batch_id, error=error, fail=True)
    totals["failed"] += failed
    totals["kept"] += kept
    print(f"  {batch_id}: {status} ({message}); {failed} rows failed, {kept} ready rows kept")


async def _collect_batch(batch: dict, totals: Counter, providers: Counter) -> None:
    batch_id = batch["id"]
    status = batch["status"]
    if status in ob.FAILED_FINAL:
        await _fail_batch(batch_id, status, _error_text(batch.get("error"), "no message"), totals)
        return
    results = batch.get("results")
    if not isinstance(results, list):
        # 402 on GET withholds results until the balance covers the charge.
        print(f"  {batch_id}: completed, results not available yet; left in flight")
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
            row_status = await _still_ours(db, paper_id, batch_id) if paper else None
            if row_status is None:
                totals["skipped"] += 1
                await db.rollback()
                continue
            generated = _generated(result, batch_model)
            paper_ready = paper.status == PaperStatus.READY
            if not paper_ready or (generated is None and row_status == READY):
                # Paper re-ingesting / failed (this text may be stale), or a
                # failed request over a good summary: the one release path,
                # which leaves a ready row's summary alone.
                await db.rollback()
                why = "request failed" if paper_ready else "paper was not ready at collect"
                kept, failed = await _release(batch_id, error=why, fail=True, only=[paper_id])
                totals["kept"] += kept
                totals["failed"] += failed
                continue
            written = await write_record(db, paper, generated)
        if written == READY:
            totals["llm" if generated is not None else "fallback"] += 1
        else:
            totals["failed"] += 1

    # A request with no result at all would otherwise stay in flight forever.
    kept, missing = await _release(batch_id, error="no result for this paper", fail=True)
    totals["kept"] += kept
    totals["failed"] += missing
    print(
        f"  {batch_id}: completed on {provider}, {len(results)} results"
        + (f", ${cost:.4f} billed" if isinstance(cost, (int, float)) else "")
        + (f", {missing + kept} with no result" if missing + kept else "")
    )


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


def _require_marker(value: str) -> str:
    if not value.startswith(MARKER_PREFIX):
        raise ValueError(f"not a submit marker: {value!r} (expected {MARKER_PREFIX}<nonce>)")
    return value


async def run_collect(
    *,
    batch_id: str | None = None,
    adopt: str | None = None,
    release: str | None = None,
    abandon: str | None = None,
    force_abandon: bool = False,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    async with _run_lock():
        return await _collect(
            batch_id=batch_id,
            adopt=adopt,
            release=release,
            abandon=abandon,
            force_abandon=force_abandon,
            client=client,
        )


async def _check_adopt(marker: str, batch_id: str, client) -> None:
    """Refuse to re-point unless `batch_id` exists (200, or 402 with results
    withheld) and its request total, when reported, matches the marker's
    row count. A wrong id would otherwise strand the rows under a batch
    that never carried them."""
    try:
        batch = await ob.fetch(batch_id, client=client)
    except ob.BatchError as exc:
        raise ValueError(
            f"--adopt refused: GET {batch_id} answered {exc.status}: {exc.message}"
        ) from exc
    rows = len(await _batch_paper_ids(marker))
    if rows == 0:
        raise ValueError(f"--adopt refused: no rows carry {marker}")
    counts = batch.get("request_counts")
    total = counts.get("total") if isinstance(counts, dict) else None
    if isinstance(total, int) and not isinstance(total, bool) and total != rows:
        raise ValueError(
            f"--adopt refused: {batch_id} has {total} requests, {marker} holds {rows} rows"
        )


async def _check_abandon(batch_id: str, client) -> None:
    """Abandon only a batch that can no longer produce results: final, or
    unknown to OpenRouter (404), or with results already gone (410)."""
    try:
        batch = await ob.fetch(batch_id, client=client)
    except ob.BatchError as exc:
        if exc.status in (404, 410):
            return
        raise ValueError(
            f"--abandon refused: GET {batch_id} answered {exc.status}: {exc.message}; "
            "pass --force-abandon to abandon anyway"
        ) from exc
    if batch.get("status") not in ob.FINAL:
        raise ValueError(
            f"--abandon refused: {batch_id} is {batch.get('status')}, not final; it may still "
            "be billed and return results. Pass --force-abandon to abandon anyway"
        )


async def _collect(*, batch_id, adopt, release, abandon, force_abandon, client) -> dict[str, Any]:
    totals: Counter = Counter()
    providers: Counter = Counter()

    if adopt is not None:
        if batch_id is None:
            raise ValueError("--adopt needs --batch-id: the batch the marker's rows went to")
        _require_marker(adopt)
        await _check_adopt(adopt, batch_id, client)
        n = await _repoint(adopt, batch_id)
        print(f"adopted {n} rows from {adopt} into {batch_id}")
    if release is not None:
        kept, n = await _release(
            _require_marker(release), error="released: no batch was created", fail=False
        )
        print(f"released {n + kept} rows held by {release}")
    if abandon is not None:
        if not force_abandon:
            await _check_abandon(abandon, client)
        kept, n = await _release(abandon, error=f"batch {abandon} abandoned", fail=True)
        totals["failed"] += n
        totals["kept"] += kept
        print(f"abandoned {abandon}: {n} rows failed, {kept} ready rows kept")

    async with SessionLocal() as db:
        in_flight = (
            await db.execute(
                select(PaperSummary.batch_id)
                .where(PaperSummary.batch_id.is_not(None))
                .distinct()
                .order_by(PaperSummary.batch_id)
            )
        ).scalars()
        in_flight = list(in_flight)
    markers = [b for b in in_flight if b.startswith(MARKER_PREFIX)]
    for marker in markers:
        print(
            f"  {marker}: submit outcome unknown; find the batch with `list`, then "
            f"`collect --batch-id <id> --adopt {marker}` or `collect --release {marker}`"
        )
    batch_ids = [batch_id] if batch_id is not None else [b for b in in_flight if b not in markers]
    print(f"{len(batch_ids)} batch(es) in flight")

    for bid in batch_ids:
        try:
            batch = await ob.fetch(bid, client=client)
        except ob.BatchError as exc:
            # 410: results expired (30 days). 404 on a batch the operator
            # NAMED: it does not exist. Either way nothing is left to collect.
            # A 404 found by the sweep is only reported: abandon it explicitly.
            if exc.status == 410 or (exc.status == 404 and batch_id is not None):
                await _fail_batch(bid, f"gone ({exc.status})", exc.message, totals)
            else:
                print(f"  {bid}: fetch failed ({exc.status}: {exc.message}); left in flight")
            continue
        batch["id"] = bid  # the id we asked for is the one rows carry
        if batch["status"] not in ob.FINAL:
            counts = batch.get("request_counts") or {}
            print(f"  {bid}: {batch['status']} {counts}; left in flight")
            continue
        await _collect_batch(batch, totals, providers)

    repaired = await _repair_embeddings()
    cost = totals["cost_micro"] / 1_000_000
    print(
        f"done: {totals['llm']} llm, {totals['fallback']} abstract fallback, "
        f"{totals['failed']} failed, {totals['released']} released, "
        f"{totals['kept']} ready kept, {totals['skipped']} skipped; "
        f"billed ${cost:.4f}; providers {dict(providers) or '{}'}"
    )
    return {
        "llm": totals["llm"],
        "fallback": totals["fallback"],
        "failed": totals["failed"],
        "released": totals["released"],
        "kept": totals["kept"],
        "skipped": totals["skipped"],
        "cost_usd": cost,
        "providers": dict(providers),
        "repaired": repaired,
        "markers": markers,
    }


# --- list ------------------------------------------------------------------


def _when(ts: Any) -> str:
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    return "-"


async def run_list(*, limit: int = 20, client: httpx.AsyncClient | None = None) -> list[dict]:
    batches = ob.listed(await ob.list_batches(limit=limit, client=client))
    for b in batches:
        counts = b.get("request_counts") if isinstance(b.get("request_counts"), dict) else {}
        print(
            f"{b.get('id', '?')}  {b.get('status', '?'):<11} {_when(b.get('created_at'))}  "
            f"{counts.get('total', '?')} requests  {b.get('model', '')}"
        )
    if not batches:
        print("no batches")
    return batches


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
    s.add_argument(
        "--max-cost",
        type=float,
        default=DEFAULT_MAX_COST,
        help=f"refuse when the larger estimate exceeds this, USD (default {DEFAULT_MAX_COST})",
    )
    s.add_argument("--dry-run", action="store_true", help="count and estimate; send nothing")
    c = sub.add_parser("collect", help="write finished batches' results")
    c.add_argument("--batch-id", help="only this batch")
    c.add_argument(
        "--adopt", metavar="MARKER", help="re-point a submit marker's rows to --batch-id"
    )
    c.add_argument("--release", metavar="MARKER", help="free a marker's rows: no batch exists")
    c.add_argument(
        "--abandon", metavar="ID", help="take a finished (or unknown) batch's rows out of flight"
    )
    c.add_argument(
        "--force-abandon",
        action="store_true",
        help="with --abandon: skip the check that the batch is final or unknown",
    )
    ls = sub.add_parser("list", help="list recent batches on OpenRouter")
    ls.add_argument("--limit", type=int, default=20, help="1..100 (default 20)")
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
                max_cost=args.max_cost,
            )
        elif args.command == "collect":
            await run_collect(
                batch_id=args.batch_id,
                adopt=args.adopt,
                release=args.release,
                abandon=args.abandon,
                force_abandon=args.force_abandon,
            )
        else:
            await run_list(limit=args.limit)
    except ob.BatchError as exc:
        print(f"error: {exc.status}: {exc.message}", file=sys.stderr)
        sys.exit(1)
    except OutcomeUnknown as exc:
        print(f"error: submit outcome unknown; rows held by {exc.marker}", file=sys.stderr)
        sys.exit(1)
    except (LockBusy, CostExceeded, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
