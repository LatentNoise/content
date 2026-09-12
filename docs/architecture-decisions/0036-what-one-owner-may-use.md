# ADR 0036 — What one owner may use

Status: accepted (2026-09-13) · Builds on 0030 (ownership), 0035 (operating is
a privilege) · Related: 0023 (retention)

## Context

With more than one user on an instance, one person can fill the disk or hold
the queue, and everyone else's experience is decided by whoever is greediest.

The maintainer's framing was to open the service with a free tier rather than
wait for billing: *"20 minutes of process, 50 MB of space"*. The intent is
right and it is what this ADR implements. Two of the three numbers changed, and
the reasons are the decision.

## Decision

### Media is counted in seconds of source, never in processing time

Processing time is the obvious unit and the wrong one, twice.

It is **only knowable afterwards**, so a refusal can only arrive once the
expense is already made. And it is **not fair**: transcoding a 4K video costs
twenty times an audio extract, for an identical service rendered. Two people
asking for the same thing would be charged differently for the speed of a
machine they do not choose.

A source's duration is known **at analysis**, before any work happens. So the
check lands at the only useful moment — after the engine knows what is being
asked for, and before anything has been spent.

A playlist counts every member, because asking for a playlist asks for every
video in it. A source whose duration cannot be read counts as zero: refusing
what cannot be measured would turn every unusual source into a support ticket.

### Storage is a ceiling on what is held, not a running total

Retention already expires files (ADR 0023), so counting cumulatively would
bill someone for bytes that no longer exist.

**50 MB was too small to be usable.** Ten minutes of 1080p video weighs between
100 and 300 MB, so that ceiling would refuse the first download of every user
and demonstrate nothing about the product. The free tier has to let someone
finish one real thing.

### Three limits, all off by default

`CONTENT_QUOTA_MEDIA_MINUTES_PER_MONTH`, `CONTENT_QUOTA_STORAGE_BYTES`,
`CONTENT_QUOTA_CONCURRENT_JOBS`. Zero means unlimited, which is what a
self-hosted instance keeps: the person running it is not a customer of
themselves.

**An operator is never counted** (ADR 0035). The quotas protect the
installation from its users, and the operator *is* the installation.

### The window rolls; it is not a calendar month

Someone who signs up on the 30th should not get a fresh allowance the next
morning, and nobody should have to explain why their month resets at midnight
UTC.

### A refused request says what to do about it

The stable code is `quota_exceeded`, with details naming the limit, what is
used and what is allowed. `GET /api/v1/usage` reports the same three counters
against the same three limits, with `allowed: null` where a limit is not set.

**A limit a person cannot watch themselves approach is a trap rather than a
rule.** That is why the route exists in the same change as the enforcement, and
not in the one after.

### A failed job still counts

It consumed the analysis and usually the download. Not counting it would make
failure a way to get free capacity.

## Where the check happens, and why only there

After analysis, before the job row is created. That is the only point where the
media duration is known *and* nothing has been spent yet. Anywhere later would
charge someone for work they were never allowed to ask for, and anywhere
earlier would be guessing.

## Consequences

- Opening a free tier no longer waits for billing. Nothing in the engine knows
  about payment, and nothing needs to: Polar answers whether someone paid,
  Content answers how much they used, and those are different questions
  (ADR 0030, decision 5).
- The usage counters are the foundation a paid tier reads from: raising a
  limit is a value, not a code change.
- `media_seconds` is recorded on each job, so an operator can see where a month
  went instead of inferring it.
- Not decided here: what happens **above** the limit. Blocking is what this
  implements; billing the overage is a product decision that needs Polar's
  real capabilities read first.
