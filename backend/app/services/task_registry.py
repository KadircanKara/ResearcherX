"""Registry of in-flight research-run tasks.

Replaces bare fire-and-forget ``asyncio.create_task``: every spawned run is
tracked so it can be cancelled (viewer gone, server shutting down) and so
shutdown can wait for cancellations to finish instead of killing mid-write.

In-process and single-worker only (like the event bus) — uvicorn must run
``--workers 1``. Multi-replica needs the Arq/Redis upgrade documented in the
README.
"""

import asyncio

from app.core.logging import log


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def register(self, run_id: str, task: asyncio.Task) -> None:
        """Track a run task; it removes itself when done."""
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self.discard(run_id))

    def get(self, run_id: str) -> asyncio.Task | None:
        return self._tasks.get(run_id)

    def discard(self, run_id: str) -> None:
        self._tasks.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        """Cancel a tracked run. True if a live task was actually cancelled."""
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    async def cancel_all(self) -> None:
        """Cancel every in-flight run and wait for them to unwind."""
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            log.info("task_registry_cancel_all", count=len(tasks))
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def __len__(self) -> int:
        return len(self._tasks)


registry = TaskRegistry()
