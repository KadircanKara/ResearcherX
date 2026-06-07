"""Anonymous access with strict quotas + owner API key (decision D3).

Three layers, all enforced as FastAPI dependencies:

- per-IP moving-window limits (keyed on the first ``X-Forwarded-For`` hop —
  Caddy sets it; direct connections fall back to the socket peer)
- a global daily run cap counted from ``research_runs`` rows — the DB is
  the counter, so it survives restarts for free
- ``X-API-Key`` owner bypass (constant-time compare)

Built directly on ``limits`` rather than the slowapi wrapper: slowapi's
``exempt_when`` is invoked with no arguments, so a request-aware owner
bypass cannot be expressed through its decorators.

In-memory limiter storage is CORRECT here because the backend is
single-worker by design (in-process bus + task registry). The
multi-replica upgrade swaps MemoryStorage for Redis alongside the bus.
"""

import hmac
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from limits import parse_many
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from app.core.config import settings
from app.core.logging import log

_storage = MemoryStorage()
_limiter = MovingWindowRateLimiter(_storage)


def client_ip(request: Request) -> str:
    """First X-Forwarded-For hop, or the socket peer when not proxied."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_owner(request: Request) -> bool:
    key = request.headers.get("x-api-key", "")
    expected = settings.owner_api_key or ""
    if not key or not expected:
        return False
    return hmac.compare_digest(key.encode(), expected.encode())


def _raise_429(detail: str, retry_after: int) -> None:
    raise HTTPException(
        status_code=429,
        detail=detail,
        headers={"Retry-After": str(max(1, retry_after))},
    )


def _enforce_per_ip(request: Request, limits_csv: str, scope: str) -> None:
    ip = client_ip(request)
    for item in parse_many(limits_csv):
        if not _limiter.hit(item, scope, ip):
            stats = _limiter.get_window_stats(item, scope, ip)
            retry_after = int(stats.reset_time - datetime.now(timezone.utc).timestamp())
            log.info("rate_limited", scope=scope, ip=ip, limit=str(item))
            _raise_429("Rate limit exceeded. Try again later.", retry_after)


async def _enforce_global_daily_cap() -> None:
    # Function-local import: core must stay importable without the db layer
    # (config/logging are foundational); only this check needs it.
    from sqlalchemy import func, select

    from app.db.models import ResearchRun
    from app.db.session import SessionLocal

    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with SessionLocal() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(ResearchRun)
                .where(ResearchRun.created_at >= day_start)
            )
        ).scalar_one()
    if count >= settings.global_daily_run_cap:
        log.warning("global_daily_cap_reached", count=count, cap=settings.global_daily_run_cap)
        next_midnight = day_start + timedelta(days=1)
        retry_after = int((next_midnight - datetime.now(timezone.utc)).total_seconds())
        _raise_429("Daily run capacity reached. Try again tomorrow.", retry_after)


async def enforce_read_limits(request: Request) -> None:
    """Cheap per-IP limit for GETs (run fetch, SSE connect)."""
    if is_owner(request):
        return
    _enforce_per_ip(request, settings.rate_limit_reads, "reads")


async def enforce_run_quotas(request: Request) -> None:
    """POST /research gate: owner bypass → global daily cap → per-IP budget.

    Global cap first so an exhausted day doesn't burn per-IP slots.
    """
    if is_owner(request):
        return
    await _enforce_global_daily_cap()
    _enforce_per_ip(request, settings.rate_limit_runs, "runs")
