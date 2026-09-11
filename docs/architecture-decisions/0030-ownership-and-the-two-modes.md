# ADR 0030 — Ownership, and the two modes

Status: accepted (2026-09-11) · Completes ADR 0024 rather than reversing it
· Related: 0006 (SQLite first), 0008 (per-job filesystem isolation), 0020
(client-uploaded sources), 0024 (no authentication)

## Context

ADR 0024 decided there would be no authentication in the engine, and it named
the four things that would reopen the question. The second, verbatim:

> **A hosted offering.** Anything Content runs on someone else's behalf needs
> real identity on day one; this ADR would not apply at all.

That is what is happening. Content is being deployed as a service, behind four
surfaces under one parent domain, with payment attached. So this is not a
change of mind: it is the branch 0024 wrote down in advance.

Two properties have to survive it, and they pull in opposite directions.

**Self-hosting must not get worse.** Someone running Content on their own LAN
does not want accounts, and 0024 says they are right. An upgrade that asks them
to sign in would be a regression sold as a feature.

**Isolation must not be best-effort.** With more than one user on an instance,
a single unfiltered read leaks another person's work. Twenty-six careful routes
and one careless one is the same as no isolation at all.

## Decision

### Everything has an owner, always

There is no "no user" state anywhere in the codebase. A self-hosted instance
has exactly one user, `local`, and it is a real owner id that simply never had
to sign in.

This is the whole trick, and it is what keeps the two properties above from
fighting. Nothing downstream of identity branches on the deployment mode: every
query filters on an owner, every path contains one, in both modes. **The
self-hosted instance is the hosted instance with one account**, not a second
code path that must be kept in step.

Chosen over `default` (which reads as "a default value" and could collide with
a real account of that name) and over `self_hosted` (which names a deployment,
not a person). Real accounts get `usr_<random>` ids, so `local` cannot collide.

### Identity is established once, at the edge, and derived — never declared

`content/api/auth.py` is the only place that turns a request into an identity,
via a FastAPI dependency. The rule it enforces:

> **the client sends a secret, the server derives the identity.**

A client never states who it is. There is no `user_id` in a body, a query
string or a custom header — if there were, editing one line of JSON would be
enough to read someone else's jobs. This is the single most common way an
otherwise careful authorization layer is defeated, and the shape of the API is
what prevents it, not the discipline of the people writing routes.

Secrets travel in `Authorization: Bearer`, never in a body or a URL: request
bodies end up in logs (ours and the reverse proxy's), a URL secret lands in
browser history and `Referer`, and `Authorization` is treated as sensitive by
default across proxies, tracing and log pipelines.

### Two modes, one code path

```
CONTENT_AUTH_MODE=none    default — no credential asked, every request is `local`
CONTENT_AUTH_MODE=token   hosted  — no valid credential, no request (401)
```

`none` reproduces the pre-0030 behaviour exactly. `token` currently refuses
every request, because nothing issues credentials yet: **an unfinished check
fails closed**, which is the only safe direction for a half-built gate.

### Two words, because there are two questions

`user_id` is *who is making this request*. `owner_id` is *who this row or file
belongs to*. In an ordinary request they hold the same value and are not the
same concept; an operator endpoint that lists someone else's jobs is where the
difference becomes load-bearing.

### The database decides. The filesystem only files.

**Authorization lives in the database.** `jobs`, `artifacts`, `uploads` and
`analysis_records` each carry `owner_id`, and every read and write filters on
it. `job_steps` and `job_events` deliberately have no column: a step has no
identity of its own, it belongs to a job, so isolation is a join on `jobs` and
ownership keeps one source of truth.

`analyses` is deliberately **not** owned. It caches facts about a public
resource — a URL's title, duration, available formats — keyed by
`resource_key`. It holds nothing of the requester, and sharing it across users
is a real saving on a service that will see the same links repeatedly.

**The filesystem gains an owner level, for operations, not for permission.**
Job trees move from `jobs/<job_id>/` to `jobs/<owner_id>/<job_id>/` (same for
`tmp/`). What that buys is concrete: deleting an account is deleting a
directory, a quota is one `du`, and one user's data can be backed up or moved
without touching anyone else's.

⛔ **A reachable path never proves a right.** Permission is decided in the
database before any path is built. Were it otherwise, an id travelling through
a URL would become a traversal primitive.

### The guard rail is a test, not a convention

`tests/test_route_ownership.py` enumerates the live application and fails when
a route outside an explicit allow-list does not depend on `Identity`. Adding an
endpoint without an owner breaks the build; exempting one means writing its
name and its reason where the next reader sees them. A second test fails when
the allow-list keeps exempting a route that no longer exists, so the exemptions
cannot rot into a blanket.

This is the answer to "what if we forget one". Not care — arithmetic.

Reuse is owner-scoped too: `find_reusable_artifact_group` filters on owner, so
a cached render never crosses an identity however identical the recipe.

### Upgrades carry existing data across, or they are not upgrades

A database that predates this ADR holds exactly one user's work, so migration 6
backfills `owner_id` to `local` with four `ALTER TABLE`s and no data pass.
`storage/migrate_owner.py` then files existing job directories under `local` at
startup: idempotent, interruptible, and conservative about what it touches
(only `job_…` entries move, so a second run can never produce
`jobs/local/local/`).

## Consequences

**Gained.** A hosted offering becomes possible without a second engine.
Self-hosting is untouched — same behaviour, same default, no account. And the
isolation is checkable rather than believed: one test names every route that
does not resolve an owner.

**Paid.** Every store method that touches user data takes `owner_id` as a
required first argument, which is deliberately noisy: forgetting it is a
`TypeError` at import time rather than a leak in production. Job paths gained a
level, so anything that built a path by hand had to learn about owners.

**Not decided here.** How credentials are issued. API keys minted from the
Console and email magic-link sessions both land in `_from_credential`, and both
are their own decision. Polar is authorization only — *has this person paid* —
never identity.
