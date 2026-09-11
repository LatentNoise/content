# ADR 0032 — The API and the worker are one image

Status: accepted (2026-09-12) · Related: 0006 (SQLite first), 0031 (one door
for outbound email)

## Context

A request arriving while the engine is transcoding waits behind it. The
obvious reading is that the API and the worker need to be separated, and the
obvious implementation is a second image with a different entry point.

That reading is wrong on the second half. `create_app()` already took
`start_worker`, and `claim_next_queued()` already took the oldest queued job
under `BEGIN IMMEDIATE` and marked it running in the same transaction. That is
exactly the shared-queue pattern: several processes can draw from it without
ever claiming the same job. The architecture was ready; only the switch was
missing.

## Decision

One image, one code path, one environment variable.

```
CONTENT_WORKER_ENABLED=false   answers requests, runs no jobs
CONTENT_WORKER_ENABLED=true    runs jobs
```

Unset means `true`, so a single-process deployment — every existing one,
including `docker compose` and every self-hosted install — behaves exactly as
before. In the chart, `worker.enabled` renders a second Deployment from the
same image and sets the variable on both.

An explicit `start_worker=` argument still wins over the configuration, so a
test pins the behaviour whatever the environment says.

## What this fixes, and what it does not

**It fixes latency.** The API stops waiting behind heavy work.

**It does not add CPU.** Every pod on a node shares the same cores. Throughput
is still governed by `CONTENT_MAX_CONCURRENT_JOBS`, which is 2 in production
today — that is the real brake, and it is a separate knob.

## Limits, both from ADR 0006

- **Every pod must land on the same node.** The data volume is ReadWriteOnce
  and node-bound, and a SQLite file reached over a network share corrupts:
  two kernels can each believe they hold the lock.
- **Continuous backup is an open question.** The chart's single-replica note
  assumed one writer, which is what Litestream expects. Check its behaviour
  with more than one writing process before enabling both together.

The day one node stops being enough, two things move together — PostgreSQL for
the database *and* object storage for the files. Migrating the database alone
would change nothing, because the second node still could not read the first
one's artifacts.
