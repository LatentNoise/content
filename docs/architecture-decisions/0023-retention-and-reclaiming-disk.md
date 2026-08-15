# ADR 0023 — Retention: reclaiming disk without losing what matters

Status: proposed (2026-08-15) · Reconciles the `retention` block reserved in
`docs/contract.md` §9 · Extends the upload sweep of ADR 0020 to every family

## Context

`docs/operations/release-readiness.md` lists this under *knowingly shipped
incomplete*: "No retention or purge. Nothing is deleted automatically; the data
directory grows until an operator manages it." That was an honest position for a
tool with one user. Content now downloads video onto other people's homelabs,
and a homelab has a disk.

**Measured on a real instance rather than imagined**, after a few weeks of
ordinary use:

| Family | Size | Contents |
| --- | --- | --- |
| `delivery/` | **7.42 GB** | 151 files — the user's library |
| `jobs/` | **3.77 GB** | 970 files across 118 jobs |
| `tmp/` | 0.01 GB | swept per job already |
| `cache/` | ~0 | disabled in V1 |
| `uploads/` | swept since ADR 0020 | — |

That table contains the finding that should shape the whole design. With
`CONTENT_DELIVERY_DEFAULT` on — which the packaged deployment sets — **every
delivered artifact exists twice**: once in `jobs/<id>/artifacts/` as the source
of truth, once in the library where the user actually looks. The 3.77 GB in
`jobs/` is, in the common case, a second copy of files that are already safe
somewhere the user browses.

So the question is not "how do we delete the user's downloads". It is "how do we
stop keeping two copies of them forever", which is a much easier thing to say
yes to.

## Decision

### 1. The delivery library is never eligible. Ever.

It is the user's media collection, it is mounted from their filesystem, and
Content's claim on it ends at writing a file into it. No retention rule, no
sweep, no "orphan" heuristic may remove anything from it. This is an invariant a
test enforces, not a guideline.

### 2. Retention acts on the *job*, and reclaiming is not forgetting

Deleting an artifact while keeping its job row leaves a history that lies;
deleting the job too destroys the provenance of a file still sitting in the
user's library. Neither is acceptable, so a retained job gains a third
possibility: its **files are reclaimed while its record survives**.

Concretely, an artifact row gains a `reclaimed_at`. The row keeps its filename,
size, checksum, provenance and `delivered_path`; only the bytes under
`jobs/<id>/artifacts/` are gone. `GET /artifacts/{id}/content` then answers
**`410 Gone`** with a distinct code — never a 404, which would say the artifact
never existed, and never a 500. A client can still show what was produced, where
it went, and that its working copy has been reclaimed.

This is what makes the common case safe: reclaiming a delivered artifact removes
a duplicate, and the library copy — the one the user actually opens — is
untouched.

### 3. Eligibility is a rule anyone can predict

An artifact's bytes are reclaimable when **all** of these hold:

- its job is terminal (never a running one);
- it is older than the configured age;
- it was **delivered** (`delivered_path` is set) *or* the operator opted into
  reclaiming undelivered artifacts too.

That last clause is the whole safety argument. By default Content only reclaims
what it can prove exists elsewhere. An artifact that was never delivered is the
only copy, and deleting the only copy of a user's file on a timer is not a
default anyone should get without asking.

Logs and snapshots follow the job's own retention on an independent clock, as
the reserved `retention` block already describes. `tmp/` and `work/` keep their
existing per-job cleanup; the sweep additionally reclaims orphans left by a
crash, exactly as the upload sweep does.

### 4. Manual before automatic

`POST /api/v1/storage/reclaim` with an explicit selection (an age, or a list of
job ids) and a `dry_run` that reports what *would* go. Nothing disappears
unless asked.

Only once that has proven itself does a periodic sweep get enabled, and then
only through configuration that is off by default —
`CONTENT_RETENTION_ARTIFACTS` unset meaning "keep everything", which is exactly
today's behaviour. An operator who never reads this document keeps the engine
they already have.

The `retention` block in the public request stays **reserved**. Per-request
retention is a different feature — a caller declaring how long *their* outputs
should live — and mixing it into an operator-level cleanup would give one word
two meanings. When it is implemented, it narrows the operator's policy for that
job; it can never widen it.

### 5. The operator can see before deciding

`GET /api/v1/storage` already reports each family, and gained `uploads` with
ADR 0020. It should also report what is *reclaimable* — bytes and count — so
"why is my disk full" and "what can I safely free" are answered in the same
place, and the Console can show both without anyone opening a shell.

## Consequences

**Gained.** The duplicate stops being permanent. An instance that has delivered
everything into a library can reclaim most of `jobs/` and lose nothing a user
can perceive, which is the difference between a tool people keep running and one
they eventually delete when the disk fills.

**Paid.** A third artifact state to handle in every client, and a `410` that
each of them must render as "reclaimed" rather than as an error. A migration
adding `reclaimed_at`. And the first mechanism in Content that removes a user's
data on a schedule — which is why it is off by default, manual first, and
restricted to files proven to exist elsewhere.

**Explicitly rejected.** Deleting job rows to save space: the database is
kilobytes and the history is the only record of what happened. Any rule that
touches the delivery library. A total-size cap that evicts the oldest
automatically, which sounds tidy and means a user's disk pressure silently
destroys their oldest downloads. And a global "purge everything" button without
`dry_run`, which is an incident waiting for a tired operator.
