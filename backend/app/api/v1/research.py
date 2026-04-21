import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.db.session import get_session
from app.schemas.research import ResearchRequest, RunOut
from app.services.event_bus import bus
from app.services.research_service import ResearchService

router = APIRouter(prefix="/research", tags=["research"])

service = ResearchService()


@router.post("", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: ResearchRequest,
    db: AsyncSession = Depends(get_session),
) -> RunOut:
    run = await service.create(db, payload.question)
    # Fire-and-forget pipeline. In prod, replace with a proper worker (Arq/Celery).
    asyncio.create_task(service.run_async(run.id))
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

    return EventSourceResponse(event_gen())
