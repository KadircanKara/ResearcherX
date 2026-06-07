from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, log
from app.db.migrate import run_migrations
from app.db.models import ResearchRun, RunStatus
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
    yield
    # Cancel in-flight runs first (their CancelledError handlers write final
    # statuses to the DB), THEN dispose the engine.
    await registry.cancel_all()
    await engine.dispose()
    log.info("app_shutdown")


app = FastAPI(title="ResearcherX", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "researcherx", "docs": "/docs"}
