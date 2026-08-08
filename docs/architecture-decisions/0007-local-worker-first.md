# ADR 0007 — Local worker first (an asyncio pool inside the API process)

Status: accepted (2026-07-17)

## Context

The API must never be blocked by a download, but V1 needs neither dedicated machines nor an orchestrator. HomeTube validated the pattern: a pool of asyncio workers that claim from the SQLite queue and run the blocking job in a thread-pool executor, with bounded concurrency.

## Decision

- **API** and **worker** are two distinct logical roles (the API validates/plans/exposes; the worker runs the external processes), hosted in the **same process** in V1 (the FastAPI lifespan starts the pool, `CONTENT_MAX_CONCURRENT_JOBS` bounds concurrency).
- Coupling goes exclusively through the DB (the claim) — no direct API→executor call. The worker can therefore be launched separately without refactoring (a second process, `start_worker=False` on the API side).
- Recovery on startup: orphaned `running` jobs are re-queued.

## Consequences

- A single container is enough; splitting API/worker is a deployment change, not a code change.
- Known limit: a process crash interrupts in-flight jobs (re-queued on restart, re-executed from the beginning — per-step idempotence will come with `reuse_existing`).
- The remaining synchronous concern is the **analysis** inside `POST /jobs`: it runs before the response, so a slow provider is felt by the caller.

## Alternatives considered

- **A mandatory separate worker process**: conceptually "cleaner", but doubles the V1 deployment with no measurable benefit.
- **Celery/RQ/Temporal**: explicitly excluded; no need for distributed retries or complex scheduling today.
