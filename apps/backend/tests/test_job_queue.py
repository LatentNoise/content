"""The queue's own loops — the part nothing used to run.

`test_worker_split.py` guards the *wiring* (which process starts a queue) by
replacing `start`/`stop` with recorders, so until this file existed the loops
inside `JobQueue` were executed by no test at all. What they must guarantee is
one property, and it is the property the sweep loop already states in its
docstring: **a worker survives a failure**. A queue whose workers can die is
worse than a queue that is down, because the API keeps accepting jobs that
nothing will ever claim, and `/health` still answers 200.
"""

import asyncio
import sqlite3

from content.execution.worker import JobQueue

POLL = 0.01
SWEEP = 0.01
# Generous relative to POLL: the assertion is "this happened", and the deadline
# only exists so a regression fails instead of hanging the suite.
DEADLINE = 5.0


async def _until(predicate, what: str) -> None:
    """Bounded poll that asserts its own deadline rather than hoping."""
    loop = asyncio.get_running_loop()
    limit = loop.time() + DEADLINE
    while not predicate():
        assert loop.time() < limit, f"timed out after {DEADLINE}s waiting for {what}"
        await asyncio.sleep(POLL)


class _Store:
    """The two queue-facing store methods, with scripted failures.

    `claim_failures` raises that many times before serving jobs, which is the
    transient-database case: WAL and a 30 s busy timeout make it rare, not
    impossible, and a full disk or an unmounted volume raises here too.
    """

    def __init__(self, jobs: list[dict] | None = None, claim_failures: int = 0):
        self.pending = list(jobs or [])
        self.claim_failures = claim_failures
        self.claims = 0
        self.requeued = 0

    def requeue_running(self) -> int:
        self.requeued += 1
        return 3

    def claim_next_queued(self) -> dict | None:
        self.claims += 1
        if self.claim_failures > 0:
            self.claim_failures -= 1
            raise sqlite3.OperationalError("database is locked")
        return self.pending.pop(0) if self.pending else None


def _job(job_id: str) -> dict:
    return {"id": job_id, "status": "running"}


def _drive(queue: JobQueue, body) -> None:
    """Start the queue, run `body`, and always stop it."""

    async def main():
        await queue.start()
        try:
            await body()
        finally:
            await queue.stop()

    asyncio.run(main())


def test_a_claim_that_raises_does_not_take_the_worker_down():
    """The defect this file was written for.

    `claim_next_queued` is a blocking SQLite call on every pass of the loop.
    Unguarded, one `OperationalError` ends the task, and with the default
    concurrency of 2 two of them leave an engine that accepts jobs and runs
    none — silently, because nobody ever retrieves a dead task's exception.
    """
    store = _Store(jobs=[_job("after-the-failure")], claim_failures=1)
    ran: list[str] = []
    queue = JobQueue(store, lambda job: ran.append(job["id"]), 1, POLL)

    _drive(queue, lambda: _until(lambda: ran, "the job queued behind the failure"))

    assert ran == ["after-the-failure"]


def test_a_runner_that_raises_does_not_take_the_worker_down():
    """Defence in depth, and the executor is the reason it is not redundant.

    `JobExecutor.execute` promises never to raise and keeps that promise by
    persisting a `failed` transition — a database write, which can itself fail.
    When it does, the exception arrives here, and the worker must lose the job
    rather than the loop.
    """
    store = _Store(jobs=[_job("explodes"), _job("survivor")])
    ran: list[str] = []

    def runner(job: dict) -> None:
        ran.append(job["id"])
        if job["id"] == "explodes":
            raise RuntimeError("the failure path failed too")

    queue = JobQueue(store, runner, 1, POLL)

    _drive(queue, lambda: _until(lambda: "survivor" in ran, "the job after the crash"))

    assert ran == ["explodes", "survivor"]


def test_the_queue_recovers_interrupted_jobs_before_claiming_anything():
    store = _Store()
    queue = JobQueue(store, lambda job: None, 1, POLL)

    _drive(queue, lambda: _until(lambda: store.claims > 0, "the first claim"))

    assert store.requeued == 1


def test_starting_twice_does_not_double_the_workers():
    store = _Store()
    queue = JobQueue(store, lambda job: None, 2, POLL)

    async def main():
        await queue.start()
        await queue.start()
        try:
            assert len(queue._tasks) == 2
        finally:
            await queue.stop()

    asyncio.run(main())
    assert store.requeued == 1


def test_stopping_cancels_every_loop():
    store = _Store()
    queue = JobQueue(
        store, lambda job: None, 2, POLL, sweeper=lambda: {}, sweep_interval=SWEEP
    )

    async def main():
        await queue.start()
        assert len(queue._tasks) == 3  # two workers and the sweeper
        tasks = list(queue._tasks)
        await queue.stop()
        assert queue._tasks == []
        assert all(task.done() for task in tasks)
        claims_after_stop = store.claims
        await asyncio.sleep(POLL * 5)
        assert store.claims == claims_after_stop, "a stopped queue kept claiming"

    asyncio.run(main())


def test_cancelling_a_worker_ends_it_even_while_the_queue_runs():
    """The guard must not catch cancellation, and `stop()` does not prove it.

    `stop()` clears `_running` *before* cancelling, so a loop that swallowed
    `CancelledError` would still fall out of its `while` and look correct.
    Cancelling one task while the queue considers itself running is what
    separates the two: `except Exception` lets the cancellation through,
    `except BaseException` would leave an unkillable task behind.
    """
    store = _Store()
    queue = JobQueue(store, lambda job: None, 1, POLL)

    async def main():
        await queue.start()
        worker = queue._tasks[0]
        # Cancelling before the first step is thrown in ahead of the `try` and
        # proves nothing about the guard: wait until the loop is really inside
        # it, suspended on its idle sleep.
        await _until(lambda: store.claims > 0, "the loop to reach its first claim")
        worker.cancel()
        loop = asyncio.get_running_loop()
        limit = loop.time() + DEADLINE
        while not worker.done() and loop.time() < limit:
            await asyncio.sleep(POLL)
        # Release the loop by its other door before asserting. A task that eats
        # `CancelledError` cannot be shut down at all, so without this the
        # regression would hang the suite in `asyncio.run`'s teardown instead
        # of failing here with a readable message.
        queue._running = False
        queue._tasks = []
        await asyncio.gather(worker, return_exceptions=True)
        assert worker.cancelled(), "the worker swallowed its cancellation"

    asyncio.run(main())


def test_the_sweeper_runs_on_its_own_clock_and_reports_what_it_reclaimed(caplog):
    """The log line is the only evidence an operator has that housekeeping ran."""
    sweeps: list[int] = []

    def sweeper() -> dict:
        sweeps.append(1)
        return {"removed": 2, "orphans": 1, "bytes_reclaimed": 4096}

    queue = JobQueue(
        _Store(), lambda job: None, 1, POLL, sweeper=sweeper, sweep_interval=SWEEP
    )

    with caplog.at_level("INFO", logger="content.worker"):
        _drive(queue, lambda: _until(lambda: len(sweeps) >= 2, "two sweeps"))

    reported = [r.getMessage() for r in caplog.records if "swept" in r.getMessage()]
    assert reported, "a sweep that reclaimed something said nothing"
    assert (
        "swept 2 expired upload(s) and 1 orphan(s), reclaiming 4096 bytes" in reported
    )


def test_a_sweep_that_reclaimed_nothing_stays_quiet(caplog):
    """A quarter-hourly chore must not write a line every tick for nothing."""
    sweeps: list[int] = []
    queue = JobQueue(
        _Store(),
        lambda job: None,
        1,
        POLL,
        sweeper=lambda: sweeps.append(1) or {"removed": 0, "orphans": 0},
        sweep_interval=SWEEP,
    )

    with caplog.at_level("INFO", logger="content.worker"):
        _drive(queue, lambda: _until(lambda: len(sweeps) >= 2, "two sweeps"))

    assert not [r for r in caplog.records if "swept" in r.getMessage()]


def test_a_sweep_that_raises_does_not_stop_the_queue():
    """The one failure mode the module documents — now actually proven."""
    sweeps: list[int] = []
    ran: list[str] = []

    def sweeper() -> dict:
        sweeps.append(1)
        raise RuntimeError("housekeeping blew up")

    queue = JobQueue(
        _Store(jobs=[_job("still-runs")]),
        lambda job: ran.append(job["id"]),
        1,
        POLL,
        sweeper=sweeper,
        sweep_interval=SWEEP,
    )

    _drive(
        queue, lambda: _until(lambda: ran and len(sweeps) >= 2, "a job and two sweeps")
    )

    assert ran == ["still-runs"]


def test_no_sweeper_means_no_sweep_loop():
    queue = JobQueue(_Store(), lambda job: None, 1, POLL)

    async def main():
        await queue.start()
        try:
            assert len(queue._tasks) == 1
        finally:
            await queue.stop()

    asyncio.run(main())
