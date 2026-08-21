# ADR 0025 — Retrying only what failed

Status: proposed (2026-08-21) · Follows ADR 0021, which made partial failure
visible · Constrained by ADR 0009 (work/ is job-local, the cache is off) and
ADR 0019 (a member is not addressable in the request)

## Context

`POST /jobs/{id}/retry` re-runs **the whole normalized request** as a new job:
fresh analysis, fresh plan, everything downloaded again.

That was defensible while a partly-failed job reported `succeeded`. Nobody
retried a green job. ADR 0021 changed that: the engine now says
`partially_succeeded` out loud, which is the point — and the obvious next
gesture is a retry. A twenty-video playlist where one member failed will
re-download nineteen videos the user already has.

The waste is bandwidth, time, and provider goodwill: member concurrency was
bounded for politeness, and this hands the same host twenty redundant requests.
It can also be *worse* than not retrying — nineteen artifacts are produced
again, and while `DeliveryStore.deliver` returns the existing path for
byte-identical content, anything the source re-encoded between runs lands as a
`…-1` clone in the user's library.

## What the engine already knows

Three facts decide most of this, and all three already exist:

1. **Per-step outcomes are recorded.** `job_steps` carries a status per step:
   `succeeded`, `failed`, `skipped`, `pending`. "Which steps failed" needs no
   inference.
2. **Steps have a content-addressed identity.** `PlanStep.signature` is
   operation + provider + resource + params + dependency signatures, and
   planning is deterministic. Re-planning the same request against the same
   analysis produces the same signatures, so the outcomes of run *n* can be
   matched onto the plan of run *n+1* without depending on step ids.
3. **A collection's members are already separate steps.** `each_item` expands
   into one `collection.member` step per member, so "the member that failed" is
   literally "the step that failed" — no member-level bookkeeping to invent.

## What the engine cannot do, and it decides the shape

**Intermediate materials are job-local.** `work/` belongs to one job and is
purged when it ends (ADR 0009); the cross-job cache that would make another
job's intermediates reusable is `CONTENT_CACHE_ENABLED=false` in V1.

So "skip the steps that already succeeded" is only safe when nothing that still
has to run needs their *materials*. That is true for a step whose output is a
finished artifact and a leaf of the plan. It is false in the middle of a chain:
if `subtitles → transcript → summary` failed at the summary, the transcript's
material lived in the old job's `work/`, and it is gone.

This is the constraint the naive framing misses, and it is what the decision
below is built around.

## Decision

**A retry-failed re-runs the smallest set of independent branches that did not
produce their artifacts, as a new job.**

### 1. The unit is a *branch*, not a step

Partition the plan into maximal connected components of the dependency graph.
Within a component, steps share intermediates; across components they do not.
A component is re-run whole if any of its steps failed or never ran; a
component whose steps all succeeded is skipped entirely.

For the case that motivated this, that is exactly right: an `each_item`
collection fans out into one component per member, so a twenty-video playlist
with one failure re-runs **one** member. For a chain, the component is the
chain, so a failed summary re-derives its transcript — which is honest, since
the material to resume from no longer exists, and it is still strictly less
work than re-running the whole request.

The answer to *"a chain failed mid-way — are the downstream steps failed or not
attempted?"* is therefore: it does not matter. They are in the same component
as the failure, and the component is the unit.

### 2. It is a new job, never a continuation

A job is one execution of a plan and its terminal states are terminal —
`content/domain/job.py` is the only authority on that, and resuming a finished
job would make "terminal" mean "terminal until someone asks nicely". The retry
job keeps the existing `retry_of` lineage column.

### 3. The retry job's *request* is the original one; its *plan* is the
restriction

This is the uncomfortable part, and the alternative is worse. A member is
deliberately not addressable in the public contract (ADR 0019: a collection
source is a URL, members are discovered by the engine), so there is no way to
write "re-run members 7 and 13" as a `GenerationRequest` without inventing an
addressing scheme that would exist for retry alone — a parallel contract, which
this project does not do.

So the retry job carries the original request (the intent really was twenty
videos) and a plan containing only the components being re-run, plus a recorded
`retry_scope: "failed"`. The cost, stated rather than hidden: **a retried job's
request over-describes what it ran**, and a reader who wants "what did this run
actually do" must read its plan or its steps, not its request.

### 4. The full set is reported through the lineage, not by copying artifacts

The successful artifacts belong to the ancestor job and stay there. The retry
job reports what it produced. `GET /jobs/{id}` gains `retry_of` in both
directions (an ancestor learns it has descendants), so a client can answer "is
my request satisfied now?" by walking a lineage that is two records deep in
practice.

Re-registering the ancestor's artifacts under the new job was considered and
rejected: it either copies files (doubling disk for a bookkeeping convenience)
or creates rows pointing at another job's bytes, which would make retention
(ADR 0023) unable to reason about a job in isolation — reclaiming an ancestor
would silently gut its descendant.

### 5. This is not `reuse_existing`, and must not be built on it

`reuse_existing` is the cross-job cache: *has anyone, ever, produced this?*
Content-addressed by `resource_key`, inert in V1 (ADR 0009/0010).

Partial retry asks a different question with a different lifetime: *did this
particular job already produce this, in this lineage?* It is scoped to one
ancestor, it does not survive the ancestor's retention, and it is correct even
with the cache switched off. Implementing it as a special case of reuse would
make the two fight the moment the cache is enabled — and would make a feature
that works today depend on one that is deliberately disabled.

## The API

```text
POST /jobs/{id}/retry            # unchanged: re-run everything
POST /jobs/{id}/retry?only=failed
```

A query parameter rather than a second endpoint: it is the same act on the same
resource, with a narrower scope. `only=failed` on a job where nothing failed is
not an error — it is a job with an empty plan, refused at planning with
`nothing_to_retry` rather than creating a job that does nothing.

Clients: `content retry --failed`, and the retry button in HomeTube, Studio and
the Console — which today offer the expensive thing silently, and should offer
the cheap one by default when the job is `partially_succeeded`.

## Alternatives rejected, in writing

- **Resume the original job.** Cheapest to describe, and it breaks the state
  machine's one invariant. Every consumer of job status would need to handle a
  terminal job becoming active again.
- **Retry at step granularity.** Correct-looking and unsound in V1: it implies
  reusing a previous job's intermediates, which `work/` does not keep and the
  cache is not enabled to provide. It becomes available if and when the cache
  is turned on, and this ADR does not block that.
- **A new request listing only the missing members.** Requires addressing
  members in the contract. ADR 0019 exists to keep members out of the request;
  a retry-only addressing scheme would be the parallel contract that ADR
  forbids.
- **Copy the ancestor's artifacts into the retry job.** Doubles bytes for a
  reporting convenience, or entangles two jobs' storage in a way retention
  cannot reason about.

## Consequences

**Gained.** The gesture ADR 0021 invites stops being expensive. A playlist
member that failed on a 429 costs one member to fix, not twenty.

**Paid.** A retried job's request describes more than the job ran, and
answering "is my whole request satisfied" means reading a lineage rather than
one record. Both are consequences of refusing to invent member addressing, and
both are visible rather than surprising.

**Follows.** Retention (ADR 0023) must treat a lineage as related: reclaiming
an ancestor's artifacts is legitimate, but the descendant's report of them must
degrade the way a reclaimed artifact already does (410 with a distinct code),
not become a dangling reference.

## What is needed before implementing

This ADR asks for a decision on §1 (branch as the unit) and §3 (request
over-describes) in particular — they are where a different judgement is
reasonable. Nothing is implemented; the endpoint, the CLI flag and the UI
buttons all wait on it.
