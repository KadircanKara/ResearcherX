import asyncio
from typing import Any


class EventBus:
    """In-process pub/sub keyed by run_id.

    Single-worker only. For multi-replica, swap this out for Redis pub/sub —
    callers stay the same.
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[dict[str, Any] | None]]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._queues.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        if run_id in self._queues:
            self._queues[run_id] = [q for q in self._queues[run_id] if q is not queue]
            if not self._queues[run_id]:
                del self._queues[run_id]

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._queues.get(run_id, [])):
            await queue.put(event)

    async def close(self, run_id: str) -> None:
        for queue in list(self._queues.get(run_id, [])):
            await queue.put(None)


bus = EventBus()
