import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import update

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, log
from app.db.migrate import run_migrations
from app.db.models import ResearchRun, RunStatus
from app.db.seed import seed_projects, seed_users
from app.db.session import SessionLocal, engine
from app.services.task_registry import registry


async def _fail_orphaned_runs() -> None:
    """Runs left PENDING/RUNNING by a previous process are dead — mark them.

    Pipeline tasks are in-process (single worker), so a restart silently
    drops them; without this they'd sit "running" forever.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            update(ResearchRun)
            .where(ResearchRun.status.in_([RunStatus.PENDING, RunStatus.RUNNING]))
            .values(status=RunStatus.FAILED, error="interrupted by restart")
        )
        await db.commit()
    if result.rowcount:
        log.warning("orphaned_runs_marked_failed", count=result.rowcount)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings.validate_for_environment()
    log.info("app_startup", model=settings.llm_model, environment=settings.environment)
    await run_migrations()
    await _fail_orphaned_runs()
    async with SessionLocal() as db:
        await seed_users(db)
        await seed_projects(db)
        await db.commit()
    yield
    # Cancel in-flight runs first (their CancelledError handlers write final
    # statuses to the DB), THEN dispose the engine.
    await registry.cancel_all()
    await engine.dispose()
    log.info("app_shutdown")


app = FastAPI(title="ResearcherX", version="0.1.0", lifespan=lifespan)

# Registered BEFORE CORSMiddleware on purpose. `add_middleware` prepends, so
# the earliest-registered middleware ends up FURTHEST INSIDE the stack, and
# this one has to sit inside CORS for its response to pick up the CORS headers.
@app.middleware("http")
async def catch_unhandled_errors(request: Request, call_next):
    """Build the 500 for an unhandled exception inside the CORS layer.

    Starlette's ServerErrorMiddleware is outside every user middleware, so the
    500 it produces never passes through CORSMiddleware and ships without
    `access-control-allow-origin`. The browser then discards the response and
    `fetch` rejects with an opaque `TypeError: Failed to fetch` — the client
    cannot tell a server error from a dead network, and every unhandled backend
    error is undebuggable from the UI. Handled errors (HTTPException) already
    come back as real responses; only genuine crashes reach here.

    Catches `Exception`, not `BaseException`: `asyncio.CancelledError` must keep
    propagating or SSE disconnects stop cancelling their runs.
    """
    try:
        return await call_next(request)
    except Exception:
        # Traceback stays server-side; the client body is generic by design.
        log.error("unhandled_error", path=request.url.path, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Debug-Log"],
)


@app.middleware("http")
async def debug_log_middleware(request: Request, call_next):
    from app.core import debug_log

    if settings.environment != "prod":
        debug_log.start()
    response = await call_next(request)
    if settings.environment != "prod":
        entries = debug_log.flush()
        if entries:
            payload = json.dumps(entries, default=str)
            # Hard cap: some proxies reject headers > 8 KB
            response.headers["X-Debug-Log"] = payload[:8000]
    return response

app.include_router(api_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "researcherx", "docs": "/docs"}
