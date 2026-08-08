# ADR 0006 — SQLite first (the DB is the source of truth, queue included)

Status: accepted (2026-07-17)

## Context

V1 targets a single-host deployment (homelab, Docker). HomeTube proved that a SQLite queue (WAL, atomic `BEGIN IMMEDIATE` claim, requeue on startup) handles that load perfectly without a broker. Introducing Redis/Celery/Postgres now would be speculative infrastructure (explicitly excluded from the project).

## Decision

- SQLite is the **source of truth**: jobs, steps, events (append-only, sequenced), artifacts, analysis cache.
- The same database serves as the **queue** behind the internal `JobQueue` abstraction (atomic claim, recovery on startup) — a pattern taken from HomeTube.
- Short-lived connection per call, WAL, `busy_timeout`: safe between API threads and workers.
- Large immutable objects (normalized request, analysis, plan, result) live as **JSON snapshots** (files per job) referenced from the DB, not in columns.

## Consequences

- Zero external services; `docker compose up` is enough.
- Known limit: a single execution host. Moving to Postgres/object storage will change neither the domain nor the contract (`persistence`/`storage` boundaries).
- Accepted V1 limit: the plan is only readable on the job's filesystem (see ADR 0008).

## Alternatives considered

- **Postgres right away**: better multi-writer behaviour, but one more service with no current need.
- **Redis/Celery**: a premature broker + distributed workers, explicitly excluded.
- **JSON files only (no DB)**: queries (listing, statuses, events after_sequence) and claim atomicity become hand-rolled.
