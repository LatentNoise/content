"""Asyncio worker pool over the SQLite queue (HomeTube-proven pattern).

Workers claim queued jobs atomically and run the blocking executor in a
thread-pool executor; concurrency is bounded. No external broker.
"""

import asyncio
import logging
from collections.abc import Callable

from content.persistence.store import Store

logger = logging.getLogger("content.worker")

Runner = Callable[[dict], None]


class JobQueue:
    def __init__(
        self,
        store: Store,
        runner: Runner,
        concurrency: int = 2,
        poll_interval: float = 0.5,
    ):
        self.store = store
        self.runner = runner
        self.concurrency = max(1, concurrency)
        self.poll_interval = poll_interval
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        requeued = self.store.requeue_running()
        if requeued:
            logger.info("re-queued %d interrupted job(s) on startup", requeued)
        self._tasks = [
            asyncio.create_task(self._worker_loop(i)) for i in range(self.concurrency)
        ]

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _worker_loop(self, worker_id: int) -> None:
        loop = asyncio.get_running_loop()
        while self._running:
            job = self.store.claim_next_queued()
            if job is None:
                await asyncio.sleep(self.poll_interval)
                continue
            logger.info("worker %d running job %s", worker_id, job["id"])
            await loop.run_in_executor(None, self.runner, job)
