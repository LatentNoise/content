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
Sweeper = Callable[[], dict]


class JobQueue:
    def __init__(
        self,
        store: Store,
        runner: Runner,
        concurrency: int = 2,
        poll_interval: float = 0.5,
        sweeper: Sweeper | None = None,
        sweep_interval: float = 900.0,
    ):
        self.store = store
        self.runner = runner
        self.concurrency = max(1, concurrency)
        self.poll_interval = poll_interval
        # Periodic housekeeping, injected rather than known here: the queue
        # stays a generic job runner and does not learn what an upload is.
        self.sweeper = sweeper
        self.sweep_interval = sweep_interval
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
        if self.sweeper is not None:
            self._tasks.append(asyncio.create_task(self._sweep_loop()))

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

    async def _sweep_loop(self) -> None:
        """Housekeeping on its own clock.

        Deliberately a separate task rather than a branch inside the worker
        loop: that loop spins every `poll_interval` looking for work, and
        mixing a quarter-hourly chore into a twice-a-second loop makes both
        harder to reason about. A sweep that raises must never stop the queue,
        so failures are logged and the loop continues.
        """
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                result = await loop.run_in_executor(None, self.sweeper)
            except Exception:  # noqa: BLE001 — housekeeping must not kill the queue
                logger.exception("housekeeping sweep failed; will retry")
            else:
                if result and (result.get("removed") or result.get("orphans")):
                    logger.info(
                        "swept %d expired upload(s) and %d orphan(s), "
                        "reclaiming %d bytes",
                        result.get("removed", 0),
                        result.get("orphans", 0),
                        result.get("bytes_reclaimed", 0),
                    )
            await asyncio.sleep(self.sweep_interval)
