import asyncio
from typing import Any

from app.core.logging import log

# One run emits ~1.1k events (mostly report_delta tokens), so the bound must
# sit comfortably above that. A drop only degrades the LIVE view of a stuck
# consumer — the UI re-seeds everything from recorded steps on refresh.
_QUEUE_MAXSIZE = 2048

# Log the first drop per queue, then sample — a wedged consumer would
# otherwise emit one warning per dropped event.
_DROP_LOG_EVERY = 500


class EventBus:
    """In-process pub/sub keyed by run_id.

    Single-worker only. For multi-replica, swap this out for Redis pub/sub —
    callers stay the same.
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[dict[str, Any] | None]]] = {}
        self._drops: dict[int, int] = {}  # id(queue) -> dropped-event count

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._queues.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        if run_id in self._queues:
            self._queues[run_id] = [q for q in self._queues[run_id] if q is not queue]
            if not self._queues[run_id]:
                del self._queues[run_id]
        self._drops.pop(id(queue), None)

    def subscriber_count(self, run_id: str) -> int:
        return len(self._queues.get(run_id, []))

    def _put_drop_oldest(
        self, run_id: str, queue: asyncio.Queue[dict[str, Any] | None], event: dict[str, Any] | None
    ) -> None:
        """Never let a slow consumer block the pipeline: drop its oldest event."""
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover — only under races
                pass
            queue.put_nowait(event)
            drops = self._drops.get(id(queue), 0) + 1
            self._drops[id(queue)] = drops
            if drops == 1 or drops % _DROP_LOG_EVERY == 0:
                log.warning("event_bus_dropped_oldest", run_id=run_id, total_dropped=drops)

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._queues.get(run_id, [])):
            self._put_drop_oldest(run_id, queue, event)

    async def close(self, run_id: str) -> None:
        for queue in list(self._queues.get(run_id, [])):
            self._put_drop_oldest(run_id, queue, None)


bus = EventBus()
