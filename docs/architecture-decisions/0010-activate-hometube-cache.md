# ADR 0010 — Enable the cache for the HomeTube use case

Status: accepted (2026-07-26)

## Context

ADR 0009 laid down the `tmp / work / artifacts / cache` boundaries and left the
cross-job cache **disabled in V1**, until its governance was framed. The
HomeTube frontend is now working: a controlled YouTube use case, where content
is often re-analyzed and regenerated from the **same** URLs. Re-probing the URL
and re-downloading the video on every generation is expensive and pointless.

## Decision

Enable the cache for this deployment (`CONTENT_CACHE_ENABLED=true` in the
compose file; the **code** default stays `false`, which is safe for tests and
other deployments). Two caches, both under the reserved `cache/` boundary:

1. **URL analysis JSON, TTL 3 days.** The default TTL moves to **72h**. The
   analysis payload (resource facts) is persisted as a durable file
   `cache/analysis/<resource_key>.json` (write-through, including on a DB hit),
   a source of truth that survives a database reset. `resource_key` is pure
   (no network), so a hit never re-probes.

2. **Reuse of the downloaded media.** The `reuse_existing` mechanism
   (content-addressed by step signature, checksum-verified) already present
   (ADR 0008) becomes active: an identical video/audio acquisition is not
   re-executed, and derived generations (transcript, summary) reuse the
   acquisition.

## Consequences

- Near-instant regenerations on an already-acquired video; repeated analyses of
  the same URL served from the cache for 3 days.
- Reuse is **safe**: an artifact only exists for a successful step
  (write-then-register) and the file is checksum-verified before reuse (covers
  INV-101 in practice).
- Accepted granularity: the signature covers every video option, so changing
  quality/format/embed/sponsorblock re-downloads (a distinct variant). Reusing
  the **raw** acquisition across different transformations would require
  separating acquisition from post-processing — blocked while `cut`
  (sponsorblock-remove = cutting) is deferred. Deferred.
- The cache currently reuses the jobs' `artifacts/` (durable, no GC in V1). A
  real `cache/blobs/sha256` store independent of job retention remains a future
  improvement (ADR 0009).

## Alternatives considered

- **Staying cache-off**: faithful to ADR 0009 but leaves the HomeTube case
  re-downloading needlessly; against the expressed need.
- **Enabling it by default in the code**: would break the "cache disabled" tests
  and change the behaviour of every deployment; we prefer a safe code default
  plus explicit per-deployment activation.
- **Building the content-addressed `cache/blobs` store right away**:
  over-engineering for "a bit of cache now"; the existing reuse is enough and
  the boundaries allow it to evolve.
