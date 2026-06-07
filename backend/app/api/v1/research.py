import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.logging import log
from app.db.session import get_session
from app.schemas.research import ResearchRequest, RunOut
from app.services.event_bus import bus
from app.services.research_service import ResearchService
from app.services.task_registry import registry

router = APIRouter(prefix="/research", tags=["research"])

service = ResearchService()

# Grace window before an unwatched run is cancelled. A page refresh closes
# the old EventSource before the new one connects, so cancelling the moment
# subscriber_count hits zero would kill runs on every reload.
UNWATCHED_CANCEL_GRACE_S = 10.0

# Strong refs to the grace-window watchers (bare create_task results are
# GC-able); each removes itself when done.
_watchers: set[asyncio.Task] = set()


def _watch_unwatched(run_id: str) -> None:
    async def _cancel_if_still_unwatched() -> None:
        await asyncio.sleep(UNWATCHED_CANCEL_GRACE_S)
        if bus.subscriber_count(run_id) == 0 and registry.cancel(run_id):
            log.info("run_cancelled_no_viewers", run_id=run_id)

    watcher = asyncio.create_task(_cancel_if_still_unwatched())
    _watchers.add(watcher)
    watcher.add_done_callback(_watchers.discard)


@router.post("", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: ResearchRequest,
    db: AsyncSession = Depends(get_session),
) -> RunOut:
    run = await service.create(db, payload.question)
    # In-process pipeline task, tracked in the registry so disconnects and
    # shutdown can cancel it (single-worker constraint — see task_registry).
    registry.register(run.id, asyncio.create_task(service.run_async(run.id)))
    return RunOut(
        id=run.id,
        question=run.question,
        status=str(run.status),
        report=run.report,
        error=run.error,
        created_at=run.created_at,
        steps=[],
    )


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, db: AsyncSession = Depends(get_session)) -> RunOut:
    run = await service.get(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunOut.model_validate(run)


@router.get("/{run_id}/events")
async def stream_events(run_id: str, request: Request) -> EventSourceResponse:
    queue = bus.subscribe(run_id)

    async def event_gen() -> AsyncIterator[dict]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                if event is None:
                    yield {"event": "end", "data": "{}"}
                    break
                yield {"event": event.get("type", "message"), "data": json.dumps(event)}
        finally:
            bus.unsubscribe(run_id, queue)
            # Last viewer gone while the run is still executing → start the
            # grace clock; a reconnect within the window keeps the run alive.
            if bus.subscriber_count(run_id) == 0 and registry.get(run_id) is not None:
                _watch_unwatched(run_id)

    return EventSourceResponse(event_gen())
